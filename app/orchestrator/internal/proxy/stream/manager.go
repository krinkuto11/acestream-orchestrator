package stream

import (
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

	"github.com/acestream/acestream/internal/proxy/aceapi"
	"github.com/acestream/acestream/internal/proxy/buffer"
	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/rediskeys"
	"github.com/acestream/acestream/internal/state"
	"github.com/acestream/acestream/internal/proxy/upstream"
)

const apiKeepaliveInterval = 2 * time.Second

// EventSink receives stream lifecycle events in-process, replacing HTTP notify calls.
type EventSink interface {
	OnStreamStarted(contentID, engineID string)
	OnStreamEnded(contentID string)
}

type noopSink struct{}

func (noopSink) OnStreamStarted(_, _ string) {}
func (noopSink) OnStreamEnded(_ string)       {}

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
	SourceInputType   string
	FileIndexes       string
	Seekback          int
	Engine            EngineParams
	WorkerID          string
	PlaybackURL       string
	PlaybackSessionID string
	StatURL           string
	CommandURL        string
	IsLive            int
	Bitrate           int
	ControlMode       string
	StreamMode        string
	PrebufferSeconds  int
	PacingMultiplier  float64
}

// Manager handles one stream's lifecycle.
type Manager struct {
	params  StreamParams
	buf     *buffer.RingBuffer
	clients *ClientManager
	hub     *Hub
	sink    EventSink

	mu          sync.Mutex
	connected   bool
	playbackURL string
	statURL     string
	commandURL  string
	sessionID   string
	bitrate     int
	isLive      int

	cancelFn context.CancelFunc

	swapCh      chan EngineParams
	pushStatsCh chan *aceapi.FullStatus

	apiMu     sync.Mutex
	apiClient *aceapi.Client

	tag string
}

func newManager(p StreamParams, buf *buffer.RingBuffer, cm *ClientManager, hub *Hub, sink EventSink) *Manager {
	return &Manager{
		params:      p,
		buf:         buf,
		clients:     cm,
		hub:         hub,
		sink:        sink,
		swapCh:      make(chan EngineParams, 1),
		pushStatsCh: make(chan *aceapi.FullStatus, 1),
		tag:         fmt.Sprintf("[stream:%s]", p.ContentID),
	}
}

func (m *Manager) Run(ctx context.Context) {
	ctx, cancel := context.WithCancel(ctx)
	m.cancelFn = cancel
	defer cancel()

	go m.statsPusher(ctx)

	slog.Info("stream manager starting", "stream", m.params.ContentID)

	m.touchRedisTimestamp(rediskeys.ConnectionAttempt(m.params.ContentID), time.Hour)

	if m.params.PlaybackURL != "" {
		m.mu.Lock()
		m.playbackURL = m.params.PlaybackURL
		m.statURL = m.params.StatURL
		m.commandURL = m.params.CommandURL
		m.sessionID = m.params.PlaybackSessionID
		m.bitrate = m.params.Bitrate
		m.isLive = m.params.IsLive
		m.mu.Unlock()
		m.sink.OnStreamStarted(m.params.ContentID, m.params.Engine.ContainerID)
		go m.measureBitrate(ctx)
		m.startReadLoop(ctx)
		m.sink.OnStreamEnded(m.params.ContentID)
		m.sendEngineStop()
		return
	}

	if err := m.requestStream(ctx); err != nil {
		slog.Error("stream request failed", "stream", m.params.ContentID, "err", err)
		return
	}

	m.sink.OnStreamStarted(m.params.ContentID, m.params.Engine.ContainerID)
	go m.measureBitrate(ctx)
	m.startReadLoop(ctx)
	m.sink.OnStreamEnded(m.params.ContentID)
	m.sendEngineStop()
}

func (m *Manager) requestStream(ctx context.Context) error {
	var err error
	for attempt := 0; attempt < 2; attempt++ {
		if attempt > 0 {
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Second):
			}
		}
		if strings.ToLower(m.params.ControlMode) == "api" {
			err = m.requestViaAPI(ctx)
		} else {
			err = m.requestViaHTTP(ctx)
		}
		if err == nil || !isTransientStreamError(err) {
			return err
		}
		slog.Warn("stream request transient error, retrying", "stream", m.params.ContentID, "attempt", attempt+1, "err", err)
	}
	return err
}

func isTransientStreamError(err error) bool {
	if err == nil {
		return false
	}
	msg := strings.ToLower(err.Error())
	return strings.Contains(msg, "connection refused") ||
		strings.Contains(msg, "connection reset") ||
		strings.Contains(msg, "eof") ||
		strings.Contains(msg, "i/o timeout")
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

	cli := &http.Client{Timeout: config.C.Load().UpstreamConnectTimeout + 5*time.Second}
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

	m.apiMu.Lock()
	m.apiClient = cli
	m.apiMu.Unlock()

	slog.Info("engine API request succeeded", "stream", m.params.ContentID, "playback_url", info.PlaybackURL)
	return nil
}

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
		r := upstream.New(m.params.ContentID, purl, m.buf, m.params.StreamMode)
		readerCtx, readerCancel := context.WithCancel(ctx)

		readerDone := make(chan error, 1)
		go func() {
			readerDone <- r.Start(readerCtx)
		}()

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
			m.mu.Lock()
			m.params.Engine = newEngine
			m.mu.Unlock()
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
				continue
			}

			m.clients.SetInitialBuffering(false)

			head := m.buf.Head()
			if head >= 0 && m.hub != nil {
				m.hub.rdb.Set(ctx, rediskeys.BufferIndex(m.params.ContentID),
					strconv.FormatInt(head, 10), time.Hour)
			}

			m.mu.Lock()
			prev := m.bitrate
			m.bitrate = measured
			m.mu.Unlock()

			if prev == 0 || measured > prev*11/10 || measured < prev*9/10 {
				m.persistBitrate(measured)
			}

			if strings.ToLower(m.params.ControlMode) != "api" {
				select {
				case m.pushStatsCh <- nil:
				default:
				}
			}
		}
	}
}

// statsPusher writes live stream statistics into the unified state store.
// It receives *aceapi.FullStatus (API mode, from runAPIKeepalive) or nil
// (HTTP mode bitrate signal, from measureBitrate).
func (m *Manager) statsPusher(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case fs := <-m.pushStatsCh:
			m.buildAndAppendStat(fs)
		}
	}
}

func (m *Manager) buildAndAppendStat(fs *aceapi.FullStatus) {
	snap := &state.StatSnapshot{
		Ts: time.Now().UTC(),
	}

	m.mu.Lock()
	bitrateBps := m.bitrate
	m.mu.Unlock()

	if bitrateBps > 0 {
		snap.Bitrate = &bitrateBps
	}

	if fs != nil && fs.Status != nil {
		// API mode: full engine stats from PingWithStatus.
		st := fs.Status
		snap.Peers = &st.Peers
		snap.SpeedDown = &st.SpeedDown
		snap.SpeedUp = &st.SpeedUp
		snap.Downloaded = &st.Downloaded
		snap.Uploaded = &st.Uploaded
		snap.Status = st.Status
	} else {
		// HTTP mode: carry forward the latest peer/speed values so the ring
		// buffer history doesn't lose data between bitrate measurements.
		existing := state.Global.GetStats(m.params.ContentID)
		if len(existing) == 0 {
			return // nothing to carry forward yet; skip empty snapshot
		}
		latest := existing[len(existing)-1]
		snap.Peers = latest.Peers
		snap.SpeedDown = latest.SpeedDown
		snap.SpeedUp = latest.SpeedUp
		snap.Downloaded = latest.Downloaded
		snap.Uploaded = latest.Uploaded
		snap.Livepos = latest.Livepos
		snap.Status = latest.Status
	}

	if fs != nil && fs.LivePos != nil {
		lp := fs.LivePos
		snap.Livepos = &state.LivePosData{
			Pos:          lp.Pos,
			LiveFirst:    lp.FirstTS,
			LiveLast:     lp.LastTS,
			FirstTs:      lp.FirstTS,
			LastTs:       lp.LastTS,
			BufferPieces: lp.BufferPieces,
		}
	}

	state.Global.AppendStat(m.params.ContentID, snap)
}

func (m *Manager) HotSwap(ep EngineParams) {
	select {
	case m.swapCh <- ep:
	default:
	}
}

func (m *Manager) Stop() {
	if m.cancelFn != nil {
		m.cancelFn()
	}
}

func (m *Manager) Bitrate() int {
	m.mu.Lock()
	b := m.bitrate
	m.mu.Unlock()
	return b
}

func (m *Manager) Connected() bool {
	m.mu.Lock()
	c := m.connected
	m.mu.Unlock()
	return c
}

func (m *Manager) ControlMode() string { return m.params.ControlMode }

func (m *Manager) PrebufferSeconds() int { return m.params.PrebufferSeconds }

func (m *Manager) PacingMultiplier() float64 { return m.params.PacingMultiplier }

func (m *Manager) StreamMode() string { return m.params.StreamMode }

func (m *Manager) EngineHost() string {
	m.mu.Lock()
	h := m.params.Engine.Host
	m.mu.Unlock()
	return h
}

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

func (m *Manager) touchRedisTimestamp(key string, ttl time.Duration) {
	if m.hub == nil {
		return
	}
	val := fmt.Sprintf("%f", float64(time.Now().UnixNano())/1e9)
	m.hub.rdb.Set(context.Background(), key, val, ttl)
}

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
