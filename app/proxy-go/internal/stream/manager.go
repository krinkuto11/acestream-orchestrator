package stream

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/acestream/proxy/internal/aceapi"
	"github.com/acestream/proxy/internal/buffer"
	"github.com/acestream/proxy/internal/config"
	"github.com/acestream/proxy/internal/rediskeys"
	"github.com/acestream/proxy/internal/upstream"
)

// apiKeepaliveInterval matches Python's ~1 s STATUS poll cadence.
const apiKeepaliveInterval = 2 * time.Second

// EngineParams describes an AceStream engine to connect to.
type EngineParams struct {
	Host        string
	Port        int
	APIPort     int
	ContainerID string
}

// StreamParams is the full set of arguments for starting a stream.
type StreamParams struct {
	ContentID         string
	SourceInput       string
	SourceInputType   string // content_id | infohash | torrent_url | direct_url | raw_data
	FileIndexes       string
	Seekback          int
	Engine            EngineParams
	WorkerID          string
	PlaybackURL       string // pre-supplied (skip engine request)
	PlaybackSessionID string
	StatURL           string
	CommandURL        string
	IsLive            int
	Bitrate           int // bytes/s
	ControlMode       string // "http" or "api"
	StreamMode        string // "TS" or "HLS"
	PrebufferSeconds  int    // 0 = disabled; from GUI setting proxy_prebuffer_seconds
}

// Manager handles one stream's lifecycle: engine connection, upstream reading, failover.
type Manager struct {
	params    StreamParams
	buf       *buffer.RingBuffer
	clients   *ClientManager
	hub       *Hub

	mu          sync.Mutex
	connected   bool
	playbackURL string
	statURL     string
	commandURL  string
	sessionID   string
	bitrate     int
	isLive      int

	// pythonStreamID is the opaque ID returned by the orchestrator on stream-started.
	// It is used when notifying stream-ended so the Python state can find the record.
	pythonStreamID string

	cancelFn context.CancelFunc

	// failover coordination
	swapCh chan EngineParams

	// pushStatsCh is a single-slot channel for serialising stats pushes.
	// Callers do a non-blocking send (drop on busy) so a slow orchestrator
	// cannot cause unbounded goroutine fan-out from the ticker loops.
	pushStatsCh chan *aceapi.FullStatus

	// API mode: keep the telnet connection alive for the stream's lifetime.
	// The engine closes the HTTP content stream when this connection drops.
	apiMu     sync.Mutex
	apiClient *aceapi.Client

	tag string
}

func newManager(p StreamParams, buf *buffer.RingBuffer, cm *ClientManager, hub *Hub) *Manager {
	return &Manager{
		params:      p,
		buf:         buf,
		clients:     cm,
		hub:         hub,
		swapCh:      make(chan EngineParams, 1),
		pushStatsCh: make(chan *aceapi.FullStatus, 1),
		tag:         fmt.Sprintf("[stream:%s]", p.ContentID),
	}
}

// Run is the main goroutine for the stream. It blocks until the stream ends or ctx is cancelled.
func (m *Manager) Run(ctx context.Context) {
	ctx, cancel := context.WithCancel(ctx)
	m.cancelFn = cancel
	defer cancel()

	go m.statsPusher(ctx)

	slog.Info("stream manager starting", "stream", m.params.ContentID)

	// Record connection attempt timestamp in Redis
	m.touchRedisTimestamp(rediskeys.ConnectionAttempt(m.params.ContentID), time.Hour)

	// If a pre-supplied playback URL was provided, skip the engine request
	if m.params.PlaybackURL != "" {
		m.mu.Lock()
		m.playbackURL = m.params.PlaybackURL
		m.statURL = m.params.StatURL
		m.commandURL = m.params.CommandURL
		m.sessionID = m.params.PlaybackSessionID
		m.bitrate = m.params.Bitrate
		m.isLive = m.params.IsLive
		m.mu.Unlock()
		m.notifyStreamStarted()
		go m.measureBitrate(ctx)
		m.startReadLoop(ctx)
		m.notifyStreamEnded("stopped")
		m.sendEngineStop()
		return
	}

	if err := m.requestStream(ctx); err != nil {
		slog.Error("stream request failed", "stream", m.params.ContentID, "err", err)
		return
	}

	m.notifyStreamStarted()
	go m.measureBitrate(ctx)
	m.startReadLoop(ctx)
	m.notifyStreamEnded("stopped")
	m.sendEngineStop()
}

// notifyStreamStarted informs the Python orchestrator that this stream is live
// so the GUI can display it. Failures are non-fatal — we log and continue.
func (m *Manager) notifyStreamStarted() {
	m.mu.Lock()
	sessionID := m.sessionID
	statURL := m.statURL
	commandURL := m.commandURL
	isLive := m.isLive
	bitrate := m.bitrate
	m.mu.Unlock()

	body, err := json.Marshal(map[string]any{
		"container_id":        m.params.Engine.ContainerID,
		"engine_host":         m.params.Engine.Host,
		"engine_port":         m.params.Engine.Port,
		"key_type":            m.params.SourceInputType,
		"key":                 m.params.ContentID,
		"playback_session_id": sessionID,
		"stat_url":            statURL,
		"command_url":         commandURL,
		"is_live":             isLive,
		"bitrate":             bitrate,
	})
	if err != nil {
		return
	}

	orchURL := config.C.OrchestratorURL + "/internal/proxy/go/stream-started"
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, orchURL, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		slog.Warn("notify stream started failed", "stream", m.params.ContentID, "err", err)
		return
	}
	defer resp.Body.Close()

	var result struct {
		StreamID string `json:"stream_id"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err == nil && result.StreamID != "" {
		m.mu.Lock()
		m.pythonStreamID = result.StreamID
		m.mu.Unlock()
		slog.Debug("stream registered with orchestrator", "stream", m.params.ContentID, "python_id", result.StreamID)
	}
}

// notifyStreamEnded informs the Python orchestrator that this stream has stopped.
func (m *Manager) notifyStreamEnded(reason string) {
	m.mu.Lock()
	pythonStreamID := m.pythonStreamID
	containerID := m.params.Engine.ContainerID
	m.mu.Unlock()

	body, err := json.Marshal(map[string]any{
		"container_id": containerID,
		"stream_id":    pythonStreamID,
		"reason":       reason,
	})
	if err != nil {
		return
	}

	orchURL := config.C.OrchestratorURL + "/internal/proxy/go/stream-ended"
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, orchURL, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		slog.Debug("notify stream ended failed", "stream", m.params.ContentID, "err", err)
		return
	}
	resp.Body.Close()
}

// requestStream asks the AceStream engine for a playback URL.
func (m *Manager) requestStream(ctx context.Context) error {
	if strings.ToLower(m.params.ControlMode) == "api" {
		return m.requestViaAPI(ctx)
	}
	return m.requestViaHTTP(ctx)
}

func (m *Manager) requestViaHTTP(ctx context.Context) error {
	ep := m.params.Engine
	params := url.Values{}
	params.Set("format", "json")
	params.Set("file_indexes", m.params.FileIndexes)
	if m.params.Seekback > 0 {
		params.Set("seekback", strconv.Itoa(m.params.Seekback))
	}

	switch m.params.SourceInputType {
	case "infohash":
		params.Set("id", m.params.SourceInput)
		params.Set("infohash", m.params.SourceInput)
	case "torrent_url":
		params.Set("torrent_url", m.params.SourceInput)
	case "direct_url":
		params.Set("direct_url", m.params.SourceInput)
		params.Set("url", m.params.SourceInput)
	case "raw_data":
		params.Set("raw_data", m.params.SourceInput)
	default:
		params.Set("id", m.params.SourceInput)
	}

	engineURL := fmt.Sprintf("http://%s:%d/ace/getstream?%s", ep.Host, ep.Port, params.Encode())
	slog.Info("requesting stream via HTTP", "stream", m.params.ContentID, "engine", fmt.Sprintf("%s:%d", ep.Host, ep.Port))

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, engineURL, nil)
	if err != nil {
		return err
	}

	cli := &http.Client{Timeout: config.C.UpstreamConnectTimeout + 5*time.Second}
	resp, err := cli.Do(req)
	if err != nil {
		return fmt.Errorf("engine http request: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("read engine response: %w", err)
	}

	var envelope struct {
		Error    string `json:"error"`
		Response struct {
			PlaybackURL       string `json:"playback_url"`
			StatURL           string `json:"stat_url"`
			CommandURL        string `json:"command_url"`
			PlaybackSessionID string `json:"playback_session_id"`
			IsLive            int    `json:"is_live"`
			Bitrate           int    `json:"bitrate"`
		} `json:"response"`
	}
	if err := json.Unmarshal(body, &envelope); err != nil {
		return fmt.Errorf("parse engine response: %w", err)
	}
	if envelope.Error != "" {
		return fmt.Errorf("engine error: %s", envelope.Error)
	}
	r := envelope.Response
	if r.PlaybackURL == "" {
		return fmt.Errorf("engine returned empty playback_url")
	}

	m.mu.Lock()
	m.playbackURL = r.PlaybackURL
	m.statURL = r.StatURL
	m.commandURL = r.CommandURL
	m.sessionID = r.PlaybackSessionID
	m.bitrate = r.Bitrate
	m.isLive = r.IsLive
	m.mu.Unlock()

	m.persistBitrate(r.Bitrate)
	slog.Info("engine HTTP request succeeded", "stream", m.params.ContentID, "bitrate_bps", r.Bitrate)
	return nil
}

func (m *Manager) requestViaAPI(ctx context.Context) error {
	ep := m.params.Engine
	apiPort := ep.APIPort
	if apiPort == 0 {
		apiPort = 62062
	}

	cli := aceapi.New(ep.Host, apiPort)
	if err := cli.Connect(); err != nil {
		return fmt.Errorf("ace_api connect: %w", err)
	}

	if err := cli.Authenticate(); err != nil {
		cli.Close()
		return fmt.Errorf("ace_api auth: %w", err)
	}

	info, err := cli.LoadAndStart(m.params.SourceInput, m.params.SourceInputType, m.params.FileIndexes, m.params.Seekback)
	if err != nil {
		cli.Close()
		return fmt.Errorf("ace_api LoadAndStart: %w", err)
	}

	m.mu.Lock()
	m.playbackURL = info.PlaybackURL
	m.statURL = info.StatURL
	m.commandURL = info.CommandURL
	m.sessionID = info.PlaybackSessionID
	m.mu.Unlock()

	// Keep the API socket open. The AceStream engine closes the HTTP content
	// stream the moment its API session disconnects. We hold the client alive
	// until sendEngineStop() tears it down.
	m.apiMu.Lock()
	m.apiClient = cli
	m.apiMu.Unlock()

	slog.Info("engine API request succeeded", "stream", m.params.ContentID, "playback_url", info.PlaybackURL)
	return nil
}

// startReadLoop runs the HTTP upstream reader, restarting if a hot-swap is signalled.
func (m *Manager) startReadLoop(ctx context.Context) {
	for {
		m.mu.Lock()
		purl := m.playbackURL
		m.connected = true
		m.mu.Unlock()

		if purl == "" {
			slog.Error("no playback URL, cannot start read loop", "stream", m.params.ContentID)
			return
		}

		m.buf.Reset()
		r := upstream.New(m.params.ContentID, purl, m.buf)
		readerCtx, readerCancel := context.WithCancel(ctx)

		readerDone := make(chan error, 1)
		go func() {
			readerDone <- r.Start(readerCtx)
		}()

		// API mode: keep the telnet session alive with periodic STATUS pings.
		// Python sends STATUS roughly every second; we use 2 s.
		var keepaliveCancel context.CancelFunc
		if strings.ToLower(m.params.ControlMode) == "api" {
			kCtx, kCancel := context.WithCancel(ctx)
			keepaliveCancel = kCancel
			go m.runAPIKeepalive(kCtx)
		}

		stopKeepalive := func() {
			if keepaliveCancel != nil {
				keepaliveCancel()
				keepaliveCancel = nil
			}
		}

		select {
		case newEngine := <-m.swapCh:
			stopKeepalive()
			readerCancel()
			r.Stop()
			<-readerDone
			slog.Info("hot-swapping engine", "stream", m.params.ContentID, "new_engine", fmt.Sprintf("%s:%d", newEngine.Host, newEngine.Port))
			m.params.Engine = newEngine
			m.touchRedisTimestamp(rediskeys.StreamInitTime(m.params.ContentID), time.Hour)
			m.touchRedisTimestamp(rediskeys.LastClientDisconnect(m.params.ContentID), 60*time.Second)
			if err := m.requestStream(ctx); err != nil {
				slog.Error("engine request failed after swap", "stream", m.params.ContentID, "err", err)
				return
			}

		case err := <-readerDone:
			stopKeepalive()
			readerCancel()
			if err != nil && ctx.Err() == nil {
				slog.Warn("upstream reader exited", "stream", m.params.ContentID, "err", err)
			} else {
				slog.Info("upstream reader finished", "stream", m.params.ContentID)
			}
			return

		case <-ctx.Done():
			stopKeepalive()
			readerCancel()
			r.Stop()
			<-readerDone
			return
		}
		readerCancel()
	}
}

// runAPIKeepalive sends STATUS pings over the open API connection so the engine
// does not consider the session idle and terminate the content stream.
// It also collects live stats (peers, speed, etc.) from the STATUS response
// and pushes them to the Python orchestrator for dashboard visibility.
func (m *Manager) runAPIKeepalive(ctx context.Context) {
	ticker := time.NewTicker(apiKeepaliveInterval)
	defer ticker.Stop()
	const maxConsecFails = 3
	consecFails := 0
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			m.apiMu.Lock()
			cli := m.apiClient
			m.apiMu.Unlock()
			if cli == nil {
				return
			}
			fullStatus, err := cli.PingWithStatus()
			if err != nil {
				consecFails++
				slog.Debug("API keepalive ping failed", "stream", m.params.ContentID,
					"err", err, "consecutive_fails", consecFails)
				if consecFails >= maxConsecFails {
					slog.Warn("API keepalive: persistent failure, abandoning keepalive",
						"stream", m.params.ContentID)
					return
				}
				continue
			}
			consecFails = 0
			if fullStatus != nil {
				select {
				case m.pushStatsCh <- fullStatus:
				default:
				}
			}
		}
	}
}

// measureBitrate periodically reads the PCR-derived video bitrate from the ring
// buffer and updates m.bitrate + Redis. PCR-based measurement is accurate from
// the first complete PCR interval (~100–200 ms) and is immune to AceStream's
// initial data-transfer burst, which inflates data-rate metrics for 30–60 s.
func (m *Manager) measureBitrate(ctx context.Context) {
	ticker := time.NewTicker(3 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			measured := int(m.buf.VideoBitrate())
			if measured < 10_000 {
				continue // PCR not yet stabilised or stream not flowing
			}

			// Data is flowing; initial buffering is over.
			m.clients.SetInitialBuffering(false)

			// Sync buffer head index to Redis for orchestrator telemetry.
			head := m.buf.Head()
			if head >= 0 && m.hub != nil {
				m.hub.rdb.Set(ctx, rediskeys.BufferIndex(m.params.ContentID),
					strconv.FormatInt(head, 10), time.Hour)
			}

			m.mu.Lock()
			m.bitrate = measured
			m.mu.Unlock()
			m.persistBitrate(measured)

			// API mode pushes full stats via runAPIKeepalive.
			// For HTTP mode push the PCR-measured bitrate so the dashboard
			// shows the true video rate instead of the engine's estimate.
			if strings.ToLower(m.params.ControlMode) != "api" {
				select {
				case m.pushStatsCh <- nil:
				default:
				}
			}
		}
	}
}

// statsPusher drains pushStatsCh and calls pushStats serially. Having a single
// goroutine here means a slow orchestrator can never cause unbounded goroutine
// accumulation — excess pushes are dropped by the non-blocking sends upstream.
func (m *Manager) statsPusher(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case s := <-m.pushStatsCh:
			m.pushStats(s)
		}
	}
}

// pushStats sends stream statistics to the Python orchestrator so the dashboard
// can display live data for streams managed by the Go proxy.
//
// info carries ACE API STATUS fields (peers, speed, etc.) — nil for HTTP-mode
// calls where only the measured bitrate needs updating.
func (m *Manager) pushStats(full *aceapi.FullStatus) {
	m.mu.Lock()
	pythonID := m.pythonStreamID
	contentID := m.params.ContentID
	m.mu.Unlock()

	bitrate := int(m.buf.VideoBitrate())
	if bitrate < 10_000 {
		bitrate = 0
	}

	payload := map[string]any{
		"stream_id":   pythonID,
		"content_key": contentID,
		"bitrate":     bitrate,
	}
	if full != nil && full.Status != nil {
		info := full.Status
		payload["peers"] = info.Peers
		payload["http_peers"] = info.HttpPeers
		payload["speed_down"] = info.SpeedDown
		payload["http_speed_down"] = info.HttpSpeedDown
		payload["speed_up"] = info.SpeedUp
		payload["downloaded"] = info.Downloaded
		payload["http_downloaded"] = info.HttpDownloaded
		payload["uploaded"] = info.Uploaded
		payload["state"] = info.Status // Maps to "status" in Python, but field name in JSON is often "state" or "status"
		payload["status"] = info.Status
		payload["total_progress"] = info.TotalProgress
		payload["immediate_progress"] = info.ImmediateProgress

		if full.LivePos != nil {
			lp := full.LivePos
			payload["livepos"] = map[string]any{
				"pos":           lp.Pos,
				"last_ts":       lp.LastTS,
				"live_last":     lp.LastTS,
				"first_ts":      lp.FirstTS,
				"live_first":    lp.FirstTS,
				"is_live":       lp.IsLive,
				"buffer_pieces": lp.BufferPieces,
			}
		}
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return
	}

	orchURL := config.C.OrchestratorURL + "/internal/proxy/go/stream-stats"
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodPost, orchURL, bytes.NewReader(body))
	if err != nil {
		return
	}
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		slog.Debug("push stats failed", "stream", contentID, "err", err)
		return
	}
	resp.Body.Close()
}

// HotSwap signals the manager to switch to a new engine mid-stream.
func (m *Manager) HotSwap(ep EngineParams) {
	select {
	case m.swapCh <- ep:
	default:
		// swap already pending
	}
}

// Stop cancels the stream.
func (m *Manager) Stop() {
	if m.cancelFn != nil {
		m.cancelFn()
	}
}

// Bitrate returns the stream's current bitrate in bytes/sec.
func (m *Manager) Bitrate() int {
	m.mu.Lock()
	b := m.bitrate
	m.mu.Unlock()
	return b
}

// Connected returns true once the upstream reader has started.
func (m *Manager) Connected() bool {
	m.mu.Lock()
	c := m.connected
	m.mu.Unlock()
	return c
}

// ControlMode returns "http" or "api" for this stream.
func (m *Manager) ControlMode() string {
	return m.params.ControlMode
}

// PrebufferSeconds returns the prebuffer hold duration for this stream.
func (m *Manager) PrebufferSeconds() int {
	return m.params.PrebufferSeconds
}

// StreamMode returns "TS" or "HLS" for this stream.
func (m *Manager) StreamMode() string {
	return m.params.StreamMode
}

// EngineHost returns the current engine hostname.
func (m *Manager) EngineHost() string {
	m.mu.Lock()
	h := m.params.Engine.Host
	m.mu.Unlock()
	return h
}

// EnginePort returns the current engine port.
func (m *Manager) EnginePort() int {
	m.mu.Lock()
	p := m.params.Engine.Port
	m.mu.Unlock()
	return p
}

func (m *Manager) persistBitrate(bps int) {
	if m.hub == nil || bps <= 0 {
		return
	}
	ctx := context.Background()
	m.hub.rdb.HSet(ctx, rediskeys.StreamMetadata(m.params.ContentID), "bitrate", strconv.Itoa(bps))
}

// touchRedisTimestamp writes the current Unix timestamp (as float string) to key with the given TTL.
func (m *Manager) touchRedisTimestamp(key string, ttl time.Duration) {
	if m.hub == nil {
		return
	}
	val := fmt.Sprintf("%f", float64(time.Now().UnixNano())/1e9)
	m.hub.rdb.Set(context.Background(), key, val, ttl)
}

// sendEngineStop notifies the engine that this stream session has ended.
// API mode: sends STOP then closes the telnet connection.
// HTTP mode: GET command_url?method=stop
func (m *Manager) sendEngineStop() {
	if strings.ToLower(m.params.ControlMode) == "api" {
		m.apiMu.Lock()
		cli := m.apiClient
		m.apiClient = nil
		m.apiMu.Unlock()
		if cli != nil {
			if err := cli.StopPlayback(); err != nil {
				slog.Debug("API STOP failed", "stream", m.params.ContentID, "err", err)
			}
			cli.Close()
			slog.Debug("API session closed", "stream", m.params.ContentID)
		}
		return
	}

	m.mu.Lock()
	cmdURL := m.commandURL
	m.mu.Unlock()

	if cmdURL == "" {
		return
	}

	stopURL := cmdURL
	if strings.Contains(stopURL, "?") {
		stopURL += "&method=stop"
	} else {
		stopURL += "?method=stop"
	}

	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, stopURL, nil)
	if err != nil {
		return
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		slog.Debug("engine stop request failed", "stream", m.params.ContentID, "err", err)
		return
	}
	resp.Body.Close()
	slog.Debug("engine stop sent", "stream", m.params.ContentID)
}
