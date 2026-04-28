// Package monitor implements the legacy AceStream STATUS monitoring service.
package monitor

import (
	"bufio"
	"context"
	"crypto/rand"
	"fmt"
	"log/slog"
	"net"
	"net/http"
	"strings"
	"sync"
	"time"

	goredis "github.com/redis/go-redis/v9"

	"github.com/acestream/acestream/internal/proxy/aceapi"
	"github.com/acestream/acestream/internal/engine"
	"github.com/acestream/acestream/internal/persistence"
	"github.com/acestream/acestream/internal/state"
)

// ─── Public types ─────────────────────────────────────────────────────────────

type EngineInfo struct {
	ContainerID string `json:"container_id"`
	Host        string `json:"host"`
	Port        int    `json:"port"`
	APIPort     int    `json:"api_port"`
	Forwarded   bool   `json:"forwarded"`
}

type SessionInfo struct {
	PlaybackSessionID string `json:"playback_session_id"`
	PlaybackURL       string `json:"playback_url"`
	ResolvedInfohash  string `json:"resolved_infohash"`
}

type LivePosData struct {
	Pos     int64 `json:"pos"`
	FirstTS int64 `json:"first_ts"`
	LastTS  int64 `json:"last_ts"`
	IsLive  int   `json:"is_live"`
}

type StatusSample struct {
	TS                string       `json:"ts"`
	Status            string       `json:"status"`
	TotalProgress     int          `json:"total_progress"`
	ImmediateProgress int          `json:"immediate_progress"`
	SpeedDown         int          `json:"speed_down"`
	HTTPSpeedDown     int          `json:"http_speed_down"`
	SpeedUp           int          `json:"speed_up"`
	Peers             int          `json:"peers"`
	HTTPPeers         int          `json:"http_peers"`
	Downloaded        int          `json:"downloaded"`
	HTTPDownloaded    int          `json:"http_downloaded"`
	Uploaded          int          `json:"uploaded"`
	LivePos           *LivePosData `json:"livepos,omitempty"`
}

type MonitorSession struct {
	MonitorID         string         `json:"monitor_id"`
	ContentID         string         `json:"content_id"`
	StreamName        string         `json:"stream_name,omitempty"`
	LiveDelay         int            `json:"live_delay"`
	Status            string         `json:"status"`
	IntervalS         float64        `json:"interval_s"`
	RunSeconds        int            `json:"run_seconds"`
	StartedAt         string         `json:"started_at"`
	LastCollectedAt   string         `json:"last_collected_at,omitempty"`
	EndedAt           string         `json:"ended_at,omitempty"`
	SampleCount       int            `json:"sample_count"`
	LastError         string         `json:"last_error,omitempty"`
	DeadReason        string         `json:"dead_reason,omitempty"`
	ReconnectAttempts int            `json:"reconnect_attempts"`
	Engine            EngineInfo     `json:"engine"`
	Session           SessionInfo    `json:"session"`
	LatestStatus      *StatusSample  `json:"latest_status,omitempty"`
	RecentStatus      []StatusSample `json:"recent_status,omitempty"`
	LiveposMovement   map[string]any `json:"livepos_movement"`
}

type StartRequest struct {
	ContentID         string  `json:"content_id"`
	StreamName        string  `json:"stream_name"`
	LiveDelay         int     `json:"live_delay"`
	IntervalS         float64 `json:"interval_s"`
	RunSeconds        int     `json:"run_seconds"`
	EngineContainerID string  `json:"engine_container_id"`
	MonitorID         string  `json:"monitor_id"`
}

// ─── Internal session state ───────────────────────────────────────────────────

type sessionData struct {
	mu                sync.Mutex
	contentID         string
	streamName        string
	liveDelay         int
	intervalS         float64
	runSeconds        int
	status            string
	startedAt         time.Time
	lastCollectedAt   time.Time
	hasLastCollected  bool
	endedAt           time.Time
	hasEndedAt        bool
	sampleCount       int
	lastError         string
	deadReason        string
	reconnectAttempts int
	engine            EngineInfo
	session           SessionInfo
	latestStatus      *StatusSample
	recentStatus      []StatusSample

	stopOnce sync.Once
	stopCh   chan struct{}
	doneCh   chan struct{}
}

func (sd *sessionData) stop() {
	sd.stopOnce.Do(func() { close(sd.stopCh) })
}

func (sd *sessionData) isStopped() bool {
	select {
	case <-sd.stopCh:
		return true
	default:
		return false
	}
}

func (sd *sessionData) serialize(id string, includeRecent bool) MonitorSession {
	sd.mu.Lock()
	defer sd.mu.Unlock()

	ms := MonitorSession{
		MonitorID:         id,
		ContentID:         sd.contentID,
		StreamName:        sd.streamName,
		LiveDelay:         sd.liveDelay,
		Status:            sd.status,
		IntervalS:         sd.intervalS,
		RunSeconds:        sd.runSeconds,
		StartedAt:         sd.startedAt.Format(time.RFC3339),
		SampleCount:       sd.sampleCount,
		LastError:         sd.lastError,
		DeadReason:        sd.deadReason,
		ReconnectAttempts: sd.reconnectAttempts,
		Engine:            sd.engine,
		Session:           sd.session,
		LatestStatus:      sd.latestStatus,
		LiveposMovement:   buildLiveposMovement(sd.recentStatus),
	}
	if sd.hasLastCollected {
		ms.LastCollectedAt = sd.lastCollectedAt.Format(time.RFC3339)
	}
	if sd.hasEndedAt {
		ms.EndedAt = sd.endedAt.Format(time.RFC3339)
	}
	if includeRecent {
		ms.RecentStatus = append([]StatusSample(nil), sd.recentStatus...)
	}
	return ms
}

// ─── Relay server ─────────────────────────────────────────────────────────────

type relayServer struct {
	ln      net.Listener
	mu      sync.Mutex
	clients []net.Conn
	closed  bool
}

func newRelayServer() (*relayServer, error) {
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return nil, fmt.Errorf("relay listen: %w", err)
	}
	return &relayServer{ln: ln}, nil
}

func (r *relayServer) URL() string {
	return "http://" + r.ln.Addr().String() + "/"
}

func (r *relayServer) serve(ctx context.Context) {
	for {
		conn, err := r.ln.Accept()
		if err != nil {
			return
		}
		go r.handleConn(ctx, conn)
	}
}

func (r *relayServer) handleConn(ctx context.Context, conn net.Conn) {
	defer conn.Close()

	br := bufio.NewReaderSize(conn, 2048)
	conn.SetReadDeadline(time.Now().Add(5 * time.Second)) //nolint:errcheck
	for {
		line, err := br.ReadString('\n')
		if err != nil || strings.TrimRight(line, "\r\n") == "" {
			break
		}
	}
	conn.SetReadDeadline(time.Time{}) //nolint:errcheck

	if _, err := conn.Write([]byte("HTTP/1.1 200 OK\r\nContent-Type: video/mp2t\r\nConnection: keep-alive\r\n\r\n")); err != nil {
		return
	}

	r.mu.Lock()
	if r.closed {
		r.mu.Unlock()
		return
	}
	r.clients = append(r.clients, conn)
	r.mu.Unlock()

	<-ctx.Done()

	r.mu.Lock()
	for i, c := range r.clients {
		if c == conn {
			r.clients = append(r.clients[:i], r.clients[i+1:]...)
			break
		}
	}
	r.mu.Unlock()
}

func (r *relayServer) broadcast(data []byte) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.closed || len(r.clients) == 0 {
		return
	}
	alive := r.clients[:0]
	for _, c := range r.clients {
		c.SetWriteDeadline(time.Now().Add(2 * time.Second)) //nolint:errcheck
		if _, err := c.Write(data); err == nil {
			alive = append(alive, c)
		} else {
			c.Close()
		}
	}
	r.clients = alive
}

func (r *relayServer) close() {
	r.ln.Close()
	r.mu.Lock()
	defer r.mu.Unlock()
	r.closed = true
	for _, c := range r.clients {
		c.Close()
	}
	r.clients = nil
}

// ─── Stream consumer ──────────────────────────────────────────────────────────

func consumeStream(ctx context.Context, playbackURL string, relay *relayServer) {
	cl := &http.Client{Timeout: 0}
	buf := make([]byte, 65536)
	for ctx.Err() == nil {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, playbackURL, nil)
		if err != nil {
			return
		}
		resp, err := cl.Do(req)
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
			}
			continue
		}
		for ctx.Err() == nil {
			n, err := resp.Body.Read(buf)
			if n > 0 {
				relay.broadcast(buf[:n])
			}
			if err != nil {
				break
			}
		}
		resp.Body.Close()
	}
}

// ─── Service ──────────────────────────────────────────────────────────────────

// Service manages legacy monitor sessions.
type Service struct {
	mu       sync.RWMutex
	sessions map[string]*sessionData
	rdb      *goredis.Client
	st       *state.Store
	settings *persistence.SettingsStore
}

// New creates a Service. st and settings are used for direct engine selection
// without an HTTP roundtrip.
func New(rdb *goredis.Client, st *state.Store, settings *persistence.SettingsStore) *Service {
	return &Service{
		sessions: make(map[string]*sessionData),
		rdb:      rdb,
		st:       st,
		settings: settings,
	}
}

func (s *Service) Start(req StartRequest) (*MonitorSession, error) {
	contentID := strings.ToLower(strings.TrimSpace(req.ContentID))
	if contentID == "" {
		return nil, fmt.Errorf("content_id required")
	}
	if req.IntervalS < 0.5 {
		req.IntervalS = 1.0
	}
	if req.RunSeconds < 0 {
		req.RunSeconds = 0
	}
	if req.LiveDelay < 0 {
		req.LiveDelay = 0
	}

	s.mu.RLock()
	for id, ssd := range s.sessions {
		ssd.mu.Lock()
		cid, st := ssd.contentID, ssd.status
		ssd.mu.Unlock()
		if cid == contentID && isActiveStatus(st) {
			sess := ssd.serialize(id, true)
			s.mu.RUnlock()
			return &sess, nil
		}
	}
	if req.MonitorID != "" {
		if ssd, ok := s.sessions[req.MonitorID]; ok {
			sess := ssd.serialize(req.MonitorID, true)
			s.mu.RUnlock()
			return &sess, nil
		}
	}
	s.mu.RUnlock()

	eng, err := s.selectEngine()
	if err != nil {
		return nil, fmt.Errorf("no engine available: %w", err)
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	for id, ssd := range s.sessions {
		ssd.mu.Lock()
		cid, st := ssd.contentID, ssd.status
		ssd.mu.Unlock()
		if cid == contentID && isActiveStatus(st) {
			sess := ssd.serialize(id, true)
			return &sess, nil
		}
	}

	id := strings.TrimSpace(req.MonitorID)
	if id == "" {
		id = newUUID()
	}

	ssd := &sessionData{
		contentID:  contentID,
		streamName: strings.TrimSpace(req.StreamName),
		liveDelay:  req.LiveDelay,
		intervalS:  req.IntervalS,
		runSeconds: req.RunSeconds,
		status:     "starting",
		startedAt:  time.Now().UTC(),
		engine:     eng,
		stopCh:     make(chan struct{}),
		doneCh:     make(chan struct{}),
	}
	s.sessions[id] = ssd
	go s.runSession(id)

	sess := ssd.serialize(id, true)
	return &sess, nil
}

func (s *Service) Stop(id string) bool {
	s.mu.RLock()
	ssd, ok := s.sessions[id]
	s.mu.RUnlock()
	if !ok {
		return false
	}
	ssd.stop()
	select {
	case <-ssd.doneCh:
	case <-time.After(5 * time.Second):
	}
	return true
}

func (s *Service) Delete(id string) bool {
	s.mu.RLock()
	_, ok := s.sessions[id]
	s.mu.RUnlock()
	if !ok {
		return false
	}
	s.Stop(id)
	s.mu.Lock()
	delete(s.sessions, id)
	s.mu.Unlock()
	return true
}

func (s *Service) Get(id string, includeRecent bool) *MonitorSession {
	s.mu.RLock()
	ssd, ok := s.sessions[id]
	s.mu.RUnlock()
	if !ok {
		return nil
	}
	sess := ssd.serialize(id, includeRecent)
	return &sess
}

func (s *Service) List(includeRecent bool) []MonitorSession {
	s.mu.RLock()
	out := make([]MonitorSession, 0, len(s.sessions))
	for id, ssd := range s.sessions {
		out = append(out, ssd.serialize(id, includeRecent))
	}
	s.mu.RUnlock()
	return out
}

func (s *Service) GetReusable(contentID string) *MonitorSession {
	norm := strings.ToLower(strings.TrimSpace(contentID))
	if norm == "" {
		return nil
	}

	type candidate struct {
		id            string
		lastCollected string
		ms            MonitorSession
	}

	s.mu.RLock()
	var candidates []candidate
	for id, ssd := range s.sessions {
		ssd.mu.Lock()
		cid := ssd.contentID
		st := ssd.status
		relayURL := ssd.session.PlaybackURL
		hasEngine := ssd.engine.ContainerID != ""
		lc := ""
		if ssd.hasLastCollected {
			lc = ssd.lastCollectedAt.Format(time.RFC3339)
		}
		ssd.mu.Unlock()

		if cid != norm || (st != "running" && st != "stuck") || relayURL == "" || !hasEngine {
			continue
		}
		ms := ssd.serialize(id, false)
		candidates = append(candidates, candidate{id: id, lastCollected: lc, ms: ms})
	}
	s.mu.RUnlock()

	if len(candidates) == 0 {
		return nil
	}
	best := candidates[0]
	for _, c := range candidates[1:] {
		if c.lastCollected > best.lastCollected {
			best = c
		}
	}
	return &best.ms
}

func (s *Service) StopAll() {
	s.mu.RLock()
	ids := make([]string, 0, len(s.sessions))
	for id := range s.sessions {
		ids = append(ids, id)
	}
	s.mu.RUnlock()
	for _, id := range ids {
		s.Stop(id)
	}
}

func (s *Service) RunCountsPublisher(ctx context.Context, interval time.Duration) {
	t := time.NewTicker(interval)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			s.publishMonitorCounts(ctx)
		}
	}
}

// ─── Background goroutine ─────────────────────────────────────────────────────

func (s *Service) runSession(id string) {
	s.mu.RLock()
	ssd := s.sessions[id]
	s.mu.RUnlock()

	defer close(ssd.doneCh)

	ssd.mu.Lock()
	contentID := ssd.contentID
	intervalS := ssd.intervalS
	runSeconds := ssd.runSeconds
	liveDelay := ssd.liveDelay
	ssd.mu.Unlock()

	startedAt := time.Now()

	relay, err := newRelayServer()
	if err != nil {
		s.markDead(ssd, "relay_error", err.Error())
		return
	}
	relayCtx, relayCancel := context.WithCancel(context.Background())
	go relay.serve(relayCtx)
	defer func() {
		relayCancel()
		relay.close()
	}()

	var aceClient *aceapi.Client
	var consumerCancel context.CancelFunc

	stopClient := func() {
		if consumerCancel != nil {
			consumerCancel()
			consumerCancel = nil
		}
		if aceClient != nil {
			_ = aceClient.StopPlayback()
			aceClient.Close()
			aceClient = nil
		}
	}
	defer stopClient()

	_ = liveDelay

	for !ssd.isStopped() {
		if runSeconds > 0 && time.Since(startedAt) >= time.Duration(runSeconds)*time.Second {
			break
		}

		if aceClient == nil {
			ssd.mu.Lock()
			eng := ssd.engine
			ssd.mu.Unlock()

			aceClient = aceapi.New(eng.Host, eng.APIPort)

			var setupErr error
			if err := aceClient.Connect(); err != nil {
				setupErr = err
			} else if err := aceClient.Authenticate(); err != nil {
				setupErr = err
			}

			if setupErr != nil {
				aceClient.Close()
				aceClient = nil
				if s.tryFailover(ssd, "connect_error", setupErr.Error()) {
					continue
				}
				s.markDead(ssd, "connect_error", setupErr.Error())
				break
			}

			loadResp, err := aceClient.ResolveContent(contentID)
			if err != nil {
				aceClient.Close()
				aceClient = nil
				reason := "loadasync error: " + err.Error()
				if s.tryFailover(ssd, "loadasync_error", reason) {
					continue
				}
				s.markDead(ssd, "loadasync_error", reason)
				break
			}
			if loadResp.Status != 1 && loadResp.Status != 2 {
				aceClient.Close()
				aceClient = nil
				reason := fmt.Sprintf("LOADASYNC status=%d: %s", loadResp.Status, loadResp.Message)
				if s.tryFailover(ssd, "loadasync_error", reason) {
					continue
				}
				s.markDead(ssd, "loadasync_error", reason)
				break
			}

			infohash := loadResp.Infohash
			if infohash == "" {
				infohash = contentID
			}

			startInfo, err := aceClient.StartStream(infohash, "0")
			if err != nil {
				aceClient.Close()
				aceClient = nil
				reason := "start error: " + err.Error()
				if s.tryFailover(ssd, "start_error", reason) {
					continue
				}
				s.markDead(ssd, "start_error", reason)
				break
			}

			cCtx, cCancel := context.WithCancel(context.Background())
			consumerCancel = cCancel
			go consumeStream(cCtx, startInfo.PlaybackURL, relay)

			psid := startInfo.PlaybackSessionID
			if psid == "" {
				psid = fmt.Sprintf("monitor-%s-%d", id[:8], time.Now().Unix())
			}

			ssd.mu.Lock()
			ssd.status = "running"
			ssd.lastError = ""
			ssd.deadReason = ""
			ssd.session = SessionInfo{
				PlaybackSessionID: psid,
				PlaybackURL:       relay.URL(),
				ResolvedInfohash:  infohash,
			}
			ssd.mu.Unlock()
			slog.Info("monitor session running", "id", id, "content_id", contentID, "engine", eng.Host)
		}

		fullStatus, err := aceClient.PingWithStatus()
		if err != nil {
			stopClient()
			reason := "timeout_or_connect_error"
			if !isTimeoutOrConnectErr(err.Error()) {
				reason = "runtime_error"
			}
			if s.tryFailover(ssd, reason, err.Error()) {
				continue
			}
			s.markDead(ssd, reason, err.Error())
			break
		}

		sample := statusSampleFromFull(fullStatus)
		stuck := s.appendSample(ssd, sample, intervalS)

		ssd.mu.Lock()
		prev := ssd.status
		if stuck {
			ssd.status = "stuck"
			ssd.lastError = "livepos did not move and payload did not grow"
		} else if prev == "stuck" || prev == "running" || prev == "starting" || prev == "reconnecting" {
			ssd.status = "running"
			ssd.lastError = ""
		}
		ssd.mu.Unlock()

		if stuck && prev != "stuck" {
			slog.Warn("monitor stuck: livepos not moving", "id", id)
		} else if !stuck && prev == "stuck" {
			slog.Info("monitor recovered from stuck", "id", id)
		}

		timer := time.NewTimer(time.Duration(intervalS * float64(time.Second)))
		select {
		case <-ssd.stopCh:
			timer.Stop()
		case <-timer.C:
		}
	}

	stopClient()

	ssd.mu.Lock()
	if ssd.status != "dead" && ssd.status != "deleted" {
		ssd.status = "stopped"
		if !ssd.hasEndedAt {
			ssd.endedAt = time.Now().UTC()
			ssd.hasEndedAt = true
		}
	}
	ssd.mu.Unlock()
}

// ─── Service helpers ──────────────────────────────────────────────────────────

// selectEngine selects the best available engine via direct in-process call.
func (s *Service) selectEngine() (EngineInfo, error) {
	sel, err := engine.Select(s.st, s.settings)
	if err != nil {
		return EngineInfo{}, err
	}
	return EngineInfo{
		ContainerID: sel.ContainerID,
		Host:        sel.Host,
		Port:        sel.Port,
		APIPort:     sel.APIPort,
		Forwarded:   sel.Forwarded,
	}, nil
}

func (s *Service) tryFailover(ssd *sessionData, reason, errText string) bool {
	newEngine, err := s.selectEngine()
	if err != nil {
		return false
	}
	ssd.mu.Lock()
	ssd.reconnectAttempts++
	ssd.status = "reconnecting"
	ssd.deadReason = ""
	ssd.lastError = reason + ": " + errText
	ssd.engine = newEngine
	ssd.session = SessionInfo{}
	ssd.mu.Unlock()
	slog.Warn("monitor failover", "reason", reason, "new_engine", newEngine.Host)
	return true
}

func (s *Service) markDead(ssd *sessionData, reason, errText string) {
	ssd.mu.Lock()
	ssd.status = "dead"
	ssd.deadReason = reason
	ssd.lastError = errText
	if !ssd.hasEndedAt {
		ssd.endedAt = time.Now().UTC()
		ssd.hasEndedAt = true
	}
	ssd.mu.Unlock()
	slog.Warn("monitor dead", "reason", reason, "err", errText)
}

func (s *Service) appendSample(ssd *sessionData, sample StatusSample, intervalS float64) (stuck bool) {
	ssd.mu.Lock()
	defer ssd.mu.Unlock()

	ssd.recentStatus = append(ssd.recentStatus, sample)
	if len(ssd.recentStatus) > 120 {
		ssd.recentStatus = ssd.recentStatus[len(ssd.recentStatus)-120:]
	}
	ssd.latestStatus = &sample
	ssd.lastCollectedAt = time.Now().UTC()
	ssd.hasLastCollected = true
	ssd.sampleCount++

	return isStuck(ssd.recentStatus, intervalS)
}

func (s *Service) publishMonitorCounts(ctx context.Context) {
	counts := make(map[string]int)
	s.mu.RLock()
	for _, ssd := range s.sessions {
		ssd.mu.Lock()
		st := ssd.status
		cid := ssd.engine.ContainerID
		ssd.mu.Unlock()
		if isActiveStatus(st) && cid != "" {
			counts[cid]++
		}
	}
	s.mu.RUnlock()

	// Update in-memory state directly (unified binary — no Redis roundtrip needed).
	// We must also zero out engines that no longer have active monitor sessions.
	existing := s.st.GetMonitorCounts()
	for cid := range existing {
		if _, still := counts[cid]; !still {
			s.st.SetMonitorCount(cid, 0)
		}
	}
	for cid, n := range counts {
		s.st.SetMonitorCount(cid, n)
	}

	if len(counts) == 0 {
		return
	}
	args := make([]any, 0, len(counts)*2)
	for cid, n := range counts {
		args = append(args, cid, n)
	}
	pipe := s.rdb.Pipeline()
	pipe.HSet(ctx, "cp:monitor_counts", args...)
	pipe.Expire(ctx, "cp:monitor_counts", 60*time.Second)
	if _, err := pipe.Exec(ctx); err != nil {
		slog.Debug("monitor counts publish failed", "err", err)
	}
}

// ─── Pure helpers ─────────────────────────────────────────────────────────────

func statusSampleFromFull(fs *aceapi.FullStatus) StatusSample {
	s := StatusSample{TS: time.Now().UTC().Format(time.RFC3339)}
	if fs.Status != nil {
		s.Status = fs.Status.Status
		s.TotalProgress = fs.Status.TotalProgress
		s.ImmediateProgress = fs.Status.ImmediateProgress
		s.SpeedDown = fs.Status.SpeedDown
		s.HTTPSpeedDown = fs.Status.HttpSpeedDown
		s.SpeedUp = fs.Status.SpeedUp
		s.Peers = fs.Status.Peers
		s.HTTPPeers = fs.Status.HttpPeers
		s.Downloaded = fs.Status.Downloaded
		s.HTTPDownloaded = fs.Status.HttpDownloaded
		s.Uploaded = fs.Status.Uploaded
	}
	if fs.LivePos != nil {
		s.LivePos = &LivePosData{
			Pos:     fs.LivePos.Pos,
			FirstTS: fs.LivePos.FirstTS,
			LastTS:  fs.LivePos.LastTS,
			IsLive:  fs.LivePos.IsLive,
		}
	}
	return s
}

func isStuck(recent []StatusSample, intervalS float64) bool {
	const stuckThreshold = 20.0
	if len(recent) < 2 {
		return false
	}
	if intervalS < 0.5 {
		intervalS = 0.5
	}
	required := int(stuckThreshold/intervalS) + 1
	if required < 2 {
		required = 2
	}
	if len(recent) < required {
		return false
	}
	window := recent[len(recent)-required:]

	var posVals, tsVals, dlVals []int64
	liveMissing := 0
	for _, s := range window {
		if s.LivePos == nil {
			liveMissing++
		} else {
			posVals = append(posVals, s.LivePos.Pos)
			tsVals = append(tsVals, s.LivePos.LastTS)
		}
		dlVals = append(dlVals, int64(s.Downloaded+s.HTTPDownloaded))
	}

	if liveMissing == len(window) {
		return true
	}
	if len(posVals) < 2 && len(tsVals) < 2 {
		return false
	}

	posStatic := len(posVals) >= 2 && allEqual64(posVals)
	tsStatic := len(tsVals) >= 2 && allEqual64(tsVals)

	var dlGrowth int64
	if len(dlVals) >= 2 {
		dlGrowth = dlVals[len(dlVals)-1] - dlVals[0]
	}
	return (posStatic || tsStatic) && dlGrowth <= 0
}

func buildLiveposMovement(recent []StatusSample) map[string]any {
	if len(recent) == 0 {
		return map[string]any{
			"is_moving": false, "direction": "unknown",
			"pos_delta": nil, "last_ts_delta": nil,
			"downloaded_delta": nil, "sample_points": 0, "movement_events": 0,
		}
	}
	var posPoints, tsPoints, dlPoints []int64
	for _, s := range recent {
		if s.LivePos != nil {
			posPoints = append(posPoints, s.LivePos.Pos)
			tsPoints = append(tsPoints, s.LivePos.LastTS)
		}
		dlPoints = append(dlPoints, int64(s.Downloaded+s.HTTPDownloaded))
	}

	ptr64 := func(vals []int64) *int64 {
		if len(vals) < 2 {
			return nil
		}
		d := vals[len(vals)-1] - vals[0]
		return &d
	}
	posDelta := ptr64(posPoints)
	tsDelta := ptr64(tsPoints)
	dlDelta := ptr64(dlPoints)

	movEvents := 0
	for i := 1; i < len(posPoints); i++ {
		if posPoints[i] != posPoints[i-1] {
			movEvents++
		}
	}
	for i := 1; i < len(tsPoints); i++ {
		if tsPoints[i] != tsPoints[i-1] {
			movEvents++
		}
	}

	isMoving := (posDelta != nil && *posDelta > 0) || (tsDelta != nil && *tsDelta > 0)

	direction := "unknown"
	if posDelta != nil {
		switch {
		case *posDelta > 0:
			direction = "forward"
		case *posDelta < 0:
			direction = "backward"
		default:
			direction = "stable"
		}
	} else if tsDelta != nil {
		switch {
		case *tsDelta > 0:
			direction = "forward"
		case *tsDelta < 0:
			direction = "backward"
		default:
			direction = "stable"
		}
	}

	var curPos, curTS *int64
	if last := recent[len(recent)-1]; last.LivePos != nil {
		p, t := last.LivePos.Pos, last.LivePos.LastTS
		curPos, curTS = &p, &t
	}

	return map[string]any{
		"is_moving": isMoving, "direction": direction,
		"current_pos": curPos, "current_last_ts": curTS,
		"pos_delta": posDelta, "last_ts_delta": tsDelta,
		"downloaded_delta": dlDelta,
		"sample_points":    len(recent),
		"movement_events":  movEvents,
	}
}

func isActiveStatus(st string) bool {
	switch st {
	case "starting", "running", "stuck", "reconnecting":
		return true
	}
	return false
}

func isTimeoutOrConnectErr(msg string) bool {
	msg = strings.ToLower(msg)
	for _, p := range []string{"timeout", "timed out", "connection closed", "socket", "not connected", "connect"} {
		if strings.Contains(msg, p) {
			return true
		}
	}
	return false
}

func allEqual64(vals []int64) bool {
	for i := 1; i < len(vals); i++ {
		if vals[i] != vals[0] {
			return false
		}
	}
	return true
}

func newUUID() string {
	var b [16]byte
	if _, err := rand.Read(b[:]); err != nil {
		return fmt.Sprintf("mon-%d", time.Now().UnixNano())
	}
	b[6] = (b[6] & 0x0f) | 0x40
	b[8] = (b[8] & 0x3f) | 0x80
	return fmt.Sprintf("%08x-%04x-%04x-%04x-%012x", b[0:4], b[4:6], b[6:8], b[8:10], b[10:16])
}
