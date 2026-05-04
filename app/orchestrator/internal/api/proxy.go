package api

import (
	"context"
	"crypto/sha1"
	"crypto/subtle"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"math/rand"
	"net"
	"net/http"
	"net/http/pprof"
	"regexp"
	"strings"
	"time"
	"unicode"

	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/controlplane/circuitbreaker"
	cpengine "github.com/acestream/acestream/internal/controlplane/engine"
	vpnpkg "github.com/acestream/acestream/internal/controlplane/vpn"
	"github.com/acestream/acestream/internal/engine"
	"github.com/acestream/acestream/internal/persistence"
	"github.com/acestream/acestream/internal/proxy/buffer"
	"github.com/acestream/acestream/internal/proxy/hls"
	"github.com/acestream/acestream/internal/proxy/monitor"
	"github.com/acestream/acestream/internal/proxy/stream"
	"github.com/acestream/acestream/internal/proxy/telemetry"
	"github.com/acestream/acestream/internal/state"
)

var fileIndexRE = regexp.MustCompile(`^\d+(,\d+)*$`)

// ProxyServer serves the proxy streaming API on :8000.
type ProxyServer struct {
	hub      *stream.Hub
	mon      *monitor.Service
	st       *state.Store
	settings *persistence.SettingsStore
	mux      *http.ServeMux

	// controlplane subsystems (direct in-process calls, no HTTP)
	ctrl       *cpengine.Controller
	cb         *circuitbreaker.Manager
	pub        *state.RedisPublisher
	prov       *vpnpkg.Provisioner
	creds      *vpnpkg.CredentialManager
	svcRefresh *vpnpkg.ServersRefreshService
	vpnMgr     *vpnpkg.LifecycleManager
}

// NewProxyServer wires up all proxy routes.
func NewProxyServer(
	hub *stream.Hub,
	mon *monitor.Service,
	st *state.Store,
	settings *persistence.SettingsStore,
	ctrl *cpengine.Controller,
	cb *circuitbreaker.Manager,
	pub *state.RedisPublisher,
	prov *vpnpkg.Provisioner,
	creds *vpnpkg.CredentialManager,
	svcRefresh *vpnpkg.ServersRefreshService,
	vpnMgr *vpnpkg.LifecycleManager,
) *ProxyServer {
	s := &ProxyServer{
		hub:        hub,
		mon:        mon,
		st:         st,
		settings:   settings,
		ctrl:       ctrl,
		cb:         cb,
		pub:        pub,
		prov:       prov,
		creds:      creds,
		svcRefresh: svcRefresh,
		vpnMgr:     vpnMgr,
		mux:        http.NewServeMux(),
	}
	s.registerRoutes()
	return s
}

func (s *ProxyServer) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

func (s *ProxyServer) registerRoutes() {
	s.mux.HandleFunc("/ace/getstream", withTelemetry("TS", s.handleGetStream))
	s.mux.HandleFunc("/ace/manifest.m3u8", withTelemetry("HLS", s.handleHLSManifest))
	s.mux.HandleFunc("/ace/hls/segment", withTelemetry("HLS", s.handleHLSSegment))
	s.mux.HandleFunc("/ace/hls/segment.ts", withTelemetry("HLS", s.handleHLSSegment))
	s.mux.HandleFunc("/ace/hls/", withTelemetry("HLS", s.handleHLSSegmentLegacy))

	s.mux.HandleFunc("/internal/proxy/stop", requireAPIKey(s.handleInternalStop))
	s.mux.HandleFunc("/internal/proxy/swap", requireAPIKey(s.handleInternalSwap))

	s.mux.HandleFunc("POST /api/v1/ace/monitor/legacy/start", s.handleMonitorStart)
	s.mux.HandleFunc("GET /api/v1/ace/monitor/legacy/reusable", s.handleMonitorReusable)
	s.mux.HandleFunc("GET /api/v1/ace/monitor/legacy", s.handleMonitorList)
	s.mux.HandleFunc("GET /api/v1/ace/monitor/legacy/{id}", s.handleMonitorGet)
	s.mux.HandleFunc("DELETE /api/v1/ace/monitor/legacy/{id}", s.handleMonitorDelete)
	s.mux.HandleFunc("DELETE /api/v1/ace/monitor/legacy/{id}/entry", s.handleMonitorDelete)
	s.mux.HandleFunc("POST /api/v1/ace/monitor/legacy/parse-m3u", s.handleMonitorParseM3U)

	s.mux.HandleFunc("/proxy/health", s.handleHealth)

	s.mux.Handle("/metrics/proxy", promhttp.Handler())

	s.mux.HandleFunc("/debug/pprof/", requireAPIKey(pprof.Index))
	s.mux.HandleFunc("/debug/pprof/cmdline", requireAPIKey(pprof.Cmdline))
	s.mux.HandleFunc("/debug/pprof/profile", requireAPIKey(pprof.Profile))
	s.mux.HandleFunc("/debug/pprof/symbol", requireAPIKey(pprof.Symbol))
	s.mux.HandleFunc("/debug/pprof/trace", requireAPIKey(pprof.Trace))

	s.registerManagementRoutes()

	// Catch-all: Python is gone; return 404 for unhandled paths.
	s.mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		http.NotFound(w, r)
	})
}

// ─── Stream handler ───────────────────────────────────────────────────────────

func (s *ProxyServer) handleGetStream(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()

	inputType, inputVal, err := selectInput(q.Get("id"), q.Get("infohash"),
		q.Get("torrent_url"), q.Get("direct_url"), q.Get("raw_data"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	fileIndexes := normalizeFileIndexes(q.Get("file_indexes"))
	seekback := parseInt(q.Get("seekback"), parseInt(q.Get("live_delay"), 0))
	streamKey := buildStreamKey(inputType, inputVal, fileIndexes, seekback)

	mgr, buf, cm := s.hub.GetEntry(streamKey)

	var streamMode, controlMode string
	var prebufferSeconds int

	if mgr == nil {
		s.st.OnStreamAllocating(streamKey)
		ep, err := s.selectEngineWithWait(r.Context())
		if err != nil {
			s.st.OnStreamEnded(state.StreamEndedEvent{ContentID: streamKey})
			http.Error(w, "no engine available: "+err.Error(), http.StatusServiceUnavailable)
			return
		}
		streamMode = ep.StreamMode
		controlMode = ep.ControlMode
		prebufferSeconds = ep.PrebufferSeconds

		p := stream.StreamParams{
			ContentID:        streamKey,
			SourceInput:      inputVal,
			SourceInputType:  inputType,
			FileIndexes:      fileIndexes,
			Seekback:         seekback,
			Engine:           ep.EngineParams,
			ControlMode:      controlMode,
			StreamMode:       streamMode,
			PrebufferSeconds: prebufferSeconds,
			PacingMultiplier: ep.PacingMultiplier,
		}
		started := s.hub.StartStream(r.Context(), p)
		mgr, buf, cm = s.hub.GetEntry(streamKey)
		if mgr == nil {
			s.st.ReleaseEnginePending(ep.ContainerID)
			s.st.OnStreamEnded(state.StreamEndedEvent{ContentID: streamKey})
			if !started {
				http.Error(w, "stream at capacity: resource limit reached", http.StatusServiceUnavailable)
			} else {
				http.Error(w, "stream start failed", http.StatusInternalServerError)
			}
			return
		}
		if !started {
			// Another goroutine started the same stream before us; release our reservation.
			s.st.ReleaseEnginePending(ep.ContainerID)
		}
	} else {
		s.hub.CancelShutdown(streamKey)
		streamMode = mgr.StreamMode()
		controlMode = mgr.ControlMode()
		if streamMode == "" {
			streamMode = strings.ToUpper(config.C.Load().StreamMode)
		}
	}

	if streamMode == "HLS" {
		http.Redirect(w, r, "/ace/manifest.m3u8?"+r.URL.RawQuery, http.StatusFound)
		return
	}

	clientIP := clientIP(r)
	clientID := buildClientID(clientIP, r.Header.Get("User-Agent"))
	_ = controlMode

	if !cm.HasCapacity() {
		http.Error(w, "stream at capacity", http.StatusServiceUnavailable)
		return
	}

	w.Header().Set("Content-Type", "video/mp2t")
	w.Header().Set("Transfer-Encoding", "chunked")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("X-Content-Type-Options", "nosniff")

	flusher, _ := w.(http.Flusher)
	cs := stream.NewClientStreamer(
		streamKey, clientID, clientIP, r.Header.Get("User-Agent"),
		seekback, mgr, buf, cm, w, flusher,
	)
	cs.Stream(r.Context())

	if cm.LocalCount() == 0 {
		if mgr != nil && !mgr.Connected() {
			slog.Info("terminating stream on client disconnect during initialization", "stream", streamKey)
			s.hub.StopStream(streamKey)
		} else {
			s.hub.ScheduleShutdown(streamKey, config.C.Load().ChannelShutdownDelay)
		}
	}
}

// ─── HLS manifest handler ─────────────────────────────────────────────────────

func (s *ProxyServer) handleHLSManifest(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	inputType, inputVal, err := selectInput(q.Get("id"), q.Get("infohash"),
		q.Get("torrent_url"), q.Get("direct_url"), q.Get("raw_data"))
	if err != nil {
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	fileIndexes := normalizeFileIndexes(q.Get("file_indexes"))
	seekback := parseInt(q.Get("seekback"), parseInt(q.Get("live_delay"), 0))
	streamKey := buildStreamKey(inputType, inputVal, fileIndexes, seekback)

	mgr, buf, _ := s.hub.GetEntry(streamKey)

	if mgr != nil {
		s.hub.CancelShutdown(streamKey)
		controlMode := mgr.ControlMode()
		if strings.ToLower(controlMode) == "api" {
			s.handleHLSManifestAPIMode(w, r, inputType, inputVal, fileIndexes, seekback, streamKey, mgr, buf)
			s.recordHLSClient(streamKey, 0, r)
			return
		}
		engineURL := fmt.Sprintf("http://%s:%d/ace/manifest.m3u8?id=%s&file_indexes=%s",
			mgr.EngineHost(), mgr.EnginePort(), urlQueryEscape(inputVal), urlQueryEscape(fileIndexes))
		sess := hls.NewSession(streamKey, engineURL, fmt.Sprintf("http://%s", r.Host))
		defer sess.Stop()
		if err := sess.ServeManifest(r.Context(), w); err != nil {
			http.Error(w, "manifest error: "+err.Error(), http.StatusBadGateway)
			return
		}
		s.recordHLSClient(streamKey, 0, r)
		return
	}

	ep, err := s.selectEngineWithWait(r.Context())
	if err != nil {
		s.st.OnStreamEnded(state.StreamEndedEvent{ContentID: streamKey})
		http.Error(w, "no engine available: "+err.Error(), http.StatusServiceUnavailable)
		return
	}

	if ep.ControlMode == "api" {
		// Release our reservation; handleHLSManifestAPIMode will select and claim its own.
		s.st.ReleaseEnginePending(ep.ContainerID)
		s.handleHLSManifestAPIMode(w, r, inputType, inputVal, fileIndexes, seekback, streamKey, nil, nil)
		s.recordHLSClient(streamKey, 0, r)
		return
	}

	// Non-API HLS: stream is not tracked via StartStream/OnStreamStarted.
	// Hold the pending reservation through ServeManifest so the engine is not
	// eligible for canStopEngine while it is actively handling this request.
	defer s.st.ReleaseEnginePending(ep.ContainerID)
	engineURL := fmt.Sprintf("http://%s:%d/ace/manifest.m3u8?id=%s&file_indexes=%s",
		ep.EngineParams.Host, ep.EngineParams.Port, urlQueryEscape(inputVal), urlQueryEscape(fileIndexes))
	sess := hls.NewSession(streamKey, engineURL, fmt.Sprintf("http://%s", r.Host))
	defer sess.Stop()
	if err := sess.ServeManifest(r.Context(), w); err != nil {
		http.Error(w, "manifest error: "+err.Error(), http.StatusBadGateway)
	}
	s.recordHLSClient(streamKey, 0, r)
}

func (s *ProxyServer) handleHLSManifestAPIMode(
	w http.ResponseWriter, r *http.Request,
	inputType, inputVal, fileIndexes string, seekback int,
	streamKey string, mgr *stream.Manager, buf *buffer.RingBuffer,
) {
	if mgr == nil {
		s.st.OnStreamAllocating(streamKey)
		ep, err := s.selectEngineWithWait(r.Context())
		if err != nil {
			s.st.OnStreamEnded(state.StreamEndedEvent{ContentID: streamKey})
			http.Error(w, "no engine available: "+err.Error(), http.StatusServiceUnavailable)
			return
		}
		p := stream.StreamParams{
			ContentID:        streamKey,
			SourceInput:      inputVal,
			SourceInputType:  inputType,
			FileIndexes:      fileIndexes,
			Seekback:         seekback,
			Engine:           ep.EngineParams,
			ControlMode:      ep.ControlMode,
			StreamMode:       "HLS",
			PrebufferSeconds: ep.PrebufferSeconds,
			PacingMultiplier: ep.PacingMultiplier,
		}
		started := s.hub.StartStream(r.Context(), p)
		_, buf, _ = s.hub.GetEntry(streamKey)
		if buf == nil {
			s.st.ReleaseEnginePending(ep.ContainerID)
			s.st.OnStreamEnded(state.StreamEndedEvent{ContentID: streamKey})
			if !started {
				http.Error(w, "stream at capacity: resource limit reached", http.StatusServiceUnavailable)
			} else {
				http.Error(w, "stream start failed", http.StatusInternalServerError)
			}
			return
		}
		if !started {
			s.st.ReleaseEnginePending(ep.ContainerID)
		}
	} else {
		s.hub.CancelShutdown(streamKey)
	}

	seg := s.hub.GetOrCreateSegmenter(streamKey, buf)
	if seg == nil {
		http.Error(w, "segmenter unavailable", http.StatusInternalServerError)
		return
	}

	prebufSec := 0
	if mgr != nil {
		prebufSec = mgr.PrebufferSeconds()
	}
	if prebufSec <= 0 {
		prebufSec = config.C.Load().ProxyPrebufferSeconds
	}

	targetSegs := 1
	if prebufSec > 0 {
		if computed := (prebufSec / 2) + 1; computed > targetSegs {
			targetSegs = computed
		}
	}

	if seg.SegmentCount() < targetSegs {
		slog.Debug("HLS pre-buffering", "stream", streamKey, "current", seg.SegmentCount(), "target", targetSegs)
		deadline := time.Now().Add(45 * time.Second)
		for time.Now().Before(deadline) {
			if seg.SegmentCount() >= targetSegs {
				break
			}
			select {
			case <-r.Context().Done():
				return
			case <-time.After(250 * time.Millisecond):
			}
		}
	}

	if seg.SegmentCount() == 0 {
		w.Header().Set("Retry-After", "5")
		http.Error(w, "stream not ready", http.StatusServiceUnavailable)
		return
	}

	manifest := seg.Manifest(fmt.Sprintf("http://%s", r.Host), streamKey)
	w.Header().Set("Content-Type", "application/vnd.apple.mpegurl")
	w.Header().Set("Cache-Control", "no-cache")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	fmt.Fprint(w, manifest)
}

// ─── HLS segment handler ──────────────────────────────────────────────────────

func (s *ProxyServer) handleHLSSegment(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	streamKey := validateStreamKey(q.Get("stream"))
	if streamKey == "" {
		http.Error(w, "stream param required", http.StatusBadRequest)
		return
	}

	if seqStr := q.Get("seq"); seqStr != "" {
		seq := parseInt(seqStr, -1)
		if seq < 0 {
			http.Error(w, "invalid seq param", http.StatusBadRequest)
			return
		}
		seg := s.hub.GetSegmenter(streamKey)
		if seg == nil {
			http.Error(w, "no HLS segmenter active for this stream", http.StatusNotFound)
			return
		}
		data, ok := seg.Segment(seq)
		if !ok {
			http.Error(w, "segment not found or expired", http.StatusNotFound)
			return
		}
		w.Header().Set("Content-Type", "video/MP2T")
		w.Header().Set("Content-Length", fmt.Sprintf("%d", len(data)))
		w.Header().Set("Cache-Control", "max-age=60")
		w.Write(data) //nolint:errcheck
		s.recordHLSClient(streamKey, int64(len(data)), r)
		return
	}

	segURL := q.Get("url")
	if segURL == "" {
		http.Error(w, "url or seq param required", http.StatusBadRequest)
		return
	}
	decodedURL, err := urlQueryUnescape(segURL)
	if err != nil {
		http.Error(w, "invalid url param", http.StatusBadRequest)
		return
	}
	sess := hls.NewSession(streamKey, "", "")
	defer sess.Stop()
	n, err := sess.ServeSegment(r.Context(), decodedURL, w)
	if err != nil {
		http.Error(w, "segment error: "+err.Error(), http.StatusBadGateway)
		return
	}
	s.recordHLSClient(streamKey, n, r)
}

func (s *ProxyServer) handleHLSSegmentLegacy(w http.ResponseWriter, r *http.Request) {
	http.NotFound(w, r)
}

// ─── Internal control endpoints ───────────────────────────────────────────────

func (s *ProxyServer) handleInternalStop(w http.ResponseWriter, r *http.Request) {
	streamKey := r.URL.Query().Get("stream")
	if streamKey == "" {
		http.Error(w, "stream param required", http.StatusBadRequest)
		return
	}
	s.hub.StopStream(streamKey)
	w.WriteHeader(http.StatusNoContent)
}

func (s *ProxyServer) handleInternalSwap(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	streamKey := q.Get("stream")
	host := q.Get("host")
	port := parseInt(q.Get("port"), 0)
	apiPort := parseInt(q.Get("api_port"), 62062)
	containerID := q.Get("container_id")

	if streamKey == "" || host == "" || port == 0 {
		http.Error(w, "stream, host, port required", http.StatusBadRequest)
		return
	}

	ep := stream.EngineParams{Host: host, Port: port, APIPort: apiPort, ContainerID: containerID}
	if !s.hub.HotSwap(streamKey, ep) {
		http.Error(w, "stream not found", http.StatusNotFound)
		return
	}
	w.WriteHeader(http.StatusNoContent)
}

func (s *ProxyServer) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"status":"ok","worker":"%s"}`, s.hub.WorkerID())
}

// ─── Monitor handlers ─────────────────────────────────────────────────────────

func (s *ProxyServer) handleMonitorStart(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 1<<20) // 1 MiB
	var req monitor.StartRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, `{"error":"invalid json"}`, http.StatusBadRequest)
		return
	}
	sess, err := s.mon.Start(req)
	if err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()}) //nolint:errcheck
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusCreated)
	json.NewEncoder(w).Encode(sess) //nolint:errcheck
}

func (s *ProxyServer) handleMonitorList(w http.ResponseWriter, r *http.Request) {
	full := r.URL.Query().Get("recent") != "false"
	sessions := s.mon.List(full)
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{"items": sessions, "total": len(sessions)}) //nolint:errcheck
}

func (s *ProxyServer) handleMonitorGet(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	full := r.URL.Query().Get("recent") != "false"
	sess := s.mon.Get(id, full)
	if sess == nil {
		http.Error(w, `{"error":"monitor not found"}`, http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(sess) //nolint:errcheck
}

func (s *ProxyServer) handleMonitorDelete(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if !s.mon.Delete(id) {
		http.Error(w, `{"error":"monitor not found"}`, http.StatusNotFound)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]string{"status": "deleted"}) //nolint:errcheck
}

func (s *ProxyServer) handleMonitorReusable(w http.ResponseWriter, r *http.Request) {
	contentID := r.URL.Query().Get("content_id")
	sess := s.mon.GetReusable(contentID)
	w.Header().Set("Content-Type", "application/json")
	if sess == nil {
		json.NewEncoder(w).Encode(map[string]any{"session": nil}) //nolint:errcheck
		return
	}
	json.NewEncoder(w).Encode(map[string]any{"session": sess}) //nolint:errcheck
}

func (s *ProxyServer) handleMonitorParseM3U(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 10<<20) // 10 MiB — M3U files can be large
	var body struct {
		M3UContent string `json:"m3u_content"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		w.Header().Set("Content-Type", "application/json")
		w.WriteHeader(http.StatusBadRequest)
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid json"}) //nolint:errcheck
		return
	}

	type m3uItem struct {
		Name      string `json:"name"`
		ContentID string `json:"content_id"`
	}

	var items []m3uItem
	var pendingName string
	for _, line := range strings.Split(body.M3UContent, "\n") {
		line = strings.TrimSpace(line)
		if strings.HasPrefix(line, "#EXTINF:") {
			// Extract name from #EXTINF:-1,Channel Name
			if idx := strings.Index(line, ","); idx >= 0 {
				pendingName = strings.TrimSpace(line[idx+1:])
			}
			continue
		}
		var contentID string
		if strings.HasPrefix(line, "acestream://") {
			contentID = strings.TrimPrefix(line, "acestream://")
		} else if strings.Contains(line, "infohash=") {
			// http://host/ace/getstream?infohash=abc123
			u, err := parseURLQuery(line)
			if err == nil {
				contentID = u
			}
		}
		if contentID != "" {
			name := pendingName
			if name == "" {
				name = contentID
			}
			items = append(items, m3uItem{Name: name, ContentID: contentID})
			pendingName = ""
		}
	}
	if items == nil {
		items = []m3uItem{}
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(map[string]any{"items": items}) //nolint:errcheck
}

func parseURLQuery(rawURL string) (string, error) {
	idx := strings.Index(rawURL, "infohash=")
	if idx < 0 {
		return "", fmt.Errorf("no infohash")
	}
	val := rawURL[idx+len("infohash="):]
	if amp := strings.IndexByte(val, '&'); amp >= 0 {
		val = val[:amp]
	}
	return val, nil
}

// ─── Engine selection ─────────────────────────────────────────────────────────

type engineSelection struct {
	stream.EngineParams
	PrebufferSeconds int
	PacingMultiplier float64
	StreamMode       string
	ControlMode      string
}

// selectEngineWithWait is like selectEngine but blocks until an engine slot
// becomes available or the request context is cancelled. On the first miss it
// bumps desiredReplicas via NudgeDemand so the autoscaler starts provisioning
// immediately rather than waiting for its next tick. Waiters unblock the instant
// a new engine calls AddEngine (zero-poll, channel-based broadcast).
//
// If the caller eventually fails (client disconnect or timeout) after having
// nudged demand, the bump is reversed so the reconciler does not provision
// ghost engines for requests that are no longer active.
func (s *ProxyServer) selectEngineWithWait(ctx context.Context) (engineSelection, error) {
	const waitTimeout = 90 * time.Second
	deadline := time.Now().Add(waitTimeout)
	nudged := false

	undoNudge := func() {
		if nudged && s.ctrl != nil {
			cfg := config.C.Load()
			s.st.DecreaseDesiredReplicas(1, cfg.MinReplicas)
		}
	}

	for {
		// Capture the channel BEFORE attempting the select so we cannot miss a
		// NotifyEngineReady that fires between the failed select and the Wait.
		ch := s.st.EngineReadyCh()
		ep, err := s.selectEngine()
		if err == nil {
			defer undoNudge() // release any demand we bumped once we've successfully selected an engine
			return ep, nil
		}

		if !nudged && s.ctrl != nil {
			s.ctrl.NudgeDemand(1)
			nudged = true
		}

		remaining := time.Until(deadline)
		if remaining <= 0 {
			undoNudge()
			return engineSelection{}, fmt.Errorf("no engine available: timed out waiting for capacity")
		}
		select {
		case <-ctx.Done():
			undoNudge()
			return engineSelection{}, ctx.Err()
		case <-time.After(remaining):
			undoNudge()
			return engineSelection{}, fmt.Errorf("no engine available: timed out waiting for capacity")
		case <-ch:
			// a new engine registered; add a small randomized jitter before
			// retrying to prevent a thundering herd where all waiters slam
			// the same new engine API in the same millisecond.
			jitter := time.Duration(rand.Intn(200)+50) * time.Millisecond
			select {
			case <-ctx.Done():
				undoNudge()
				return engineSelection{}, ctx.Err()
			case <-time.After(jitter):
				// continue retry loop
			}
		}
	}
}

func (s *ProxyServer) selectEngine() (engineSelection, error) {
	sel, err := engine.Select(s.st, s.settings)
	if err != nil {
		return engineSelection{}, err
	}
	return engineSelection{
		EngineParams: stream.EngineParams{
			Host:        sel.Host,
			Port:        sel.Port,
			APIPort:     sel.APIPort,
			ContainerID: sel.ContainerID,
		},
		PrebufferSeconds: sel.PrebufferSeconds,
		PacingMultiplier: sel.PacingMultiplier,
		StreamMode:       sel.StreamMode,
		ControlMode:      sel.ControlMode,
	}, nil
}

// ─── HLS client bookkeeping ───────────────────────────────────────────────────

func (s *ProxyServer) recordHLSClient(streamKey string, bytesDelta int64, r *http.Request) {
	_, _, cm := s.hub.GetEntry(streamKey)
	if cm == nil {
		return
	}
	ip := clientIP(r)
	ua := r.Header.Get("User-Agent")
	cid := buildClientID(ip, ua)
	cm.HeartbeatHLSClient(cid, ip, ua, bytesDelta)
	if bytesDelta > 0 {
		telemetry.DefaultTelemetry.ObserveEgress("HLS", bytesDelta)
	}
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

func selectInput(id, infohash, torrentURL, directURL, rawData string) (string, string, error) {
	type candidate struct{ t, v string }
	var choices []candidate
	for _, c := range []candidate{
		{"content_id", id},
		{"infohash", infohash},
		{"torrent_url", torrentURL},
		{"direct_url", directURL},
		{"raw_data", rawData},
	} {
		if v := sanitizeID(c.v); v != "" {
			choices = append(choices, candidate{c.t, v})
		}
	}
	if len(choices) == 0 {
		return "", "", fmt.Errorf("provide one of: id, infohash, torrent_url, direct_url, raw_data")
	}
	if len(choices) > 1 {
		return "", "", fmt.Errorf("parameters are mutually exclusive")
	}
	return choices[0].t, choices[0].v, nil
}

func normalizeFileIndexes(s string) string {
	s = strings.TrimSpace(s)
	if s == "" {
		return "0"
	}
	if !fileIndexRE.MatchString(s) {
		return "0"
	}
	return s
}

func buildStreamKey(inputType, inputVal, fileIndexes string, seekback int) string {
	if inputType == "content_id" || inputType == "infohash" {
		if fileIndexes == "0" && seekback <= 0 {
			return inputVal
		}
	}
	if fileIndexes == "0" && seekback <= 0 {
		h := sha1.Sum([]byte(inputVal))
		return fmt.Sprintf("%s:%x", inputType, h[:8])
	}
	payload := fmt.Sprintf("%s:%s|file_indexes=%s|seekback=%d", inputType, inputVal, fileIndexes, seekback)
	h := sha1.Sum([]byte(payload))
	return fmt.Sprintf("%s:%x", inputType, h[:8])
}

func buildClientID(ip, userAgent string) string {
	h := sha1.Sum([]byte(ip + "|" + userAgent))
	return fmt.Sprintf("%x", h[:8])
}

func sanitizeID(v string) string {
	v = strings.TrimSpace(v)
	v = strings.Trim(v, `\{}'\"`)
	v = strings.TrimSpace(v)
	if v == "" {
		return ""
	}
	var sb strings.Builder
	for _, r := range strings.ToLower(v) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_' || r == '.' || r == '-' || r == '|' {
			sb.WriteRune(r)
		} else {
			sb.WriteRune('_')
		}
	}
	return sb.String()
}

func validateStreamKey(v string) string {
	v = strings.TrimSpace(v)
	if v == "" {
		return ""
	}
	var sb strings.Builder
	for _, r := range strings.ToLower(v) {
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_' || r == '.' || r == '-' || r == '|' || r == ':' {
			sb.WriteRune(r)
		}
	}
	return sb.String()
}

func clientIP(r *http.Request) string {
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		return strings.SplitN(xff, ",", 2)[0]
	}
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return r.RemoteAddr
	}
	return host
}

func parseInt(s string, def int) int {
	var n int
	if _, err := fmt.Sscanf(s, "%d", &n); err == nil {
		return n
	}
	return def
}

func decodeJSON(r io.Reader, v any) error {
	return json.NewDecoder(r).Decode(v)
}

func requireAPIKey(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		key := config.C.Load().APIKey
		if key == "" {
			next(w, r)
			return
		}
		// Accept X-API-Key header, Authorization: Bearer <token>, or ?key= query param.
		provided := r.Header.Get("X-API-Key")
		if provided == "" {
			if auth := r.Header.Get("Authorization"); strings.HasPrefix(auth, "Bearer ") {
				provided = strings.TrimPrefix(auth, "Bearer ")
			}
		}
		if provided == "" {
			provided = r.URL.Query().Get("key")
		}
		if provided == "" {
			http.Error(w, "unauthorized", http.StatusUnauthorized)
			return
		}
		if subtle.ConstantTimeCompare([]byte(provided), []byte(key)) != 1 {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		next(w, r)
	}
}

func urlQueryEscape(s string) string {
	var sb strings.Builder
	for _, b := range []byte(s) {
		if isUnreserved(b) {
			sb.WriteByte(b)
		} else {
			fmt.Fprintf(&sb, "%%%02X", b)
		}
	}
	return sb.String()
}

func urlQueryUnescape(s string) (string, error) {
	var result []byte
	for i := 0; i < len(s); {
		if s[i] == '%' && i+2 < len(s) {
			var b byte
			if _, err := fmt.Sscanf(s[i+1:i+3], "%02X", &b); err == nil {
				result = append(result, b)
				i += 3
				continue
			}
		}
		result = append(result, s[i])
		i++
	}
	return string(result), nil
}

func isUnreserved(b byte) bool {
	return (b >= 'A' && b <= 'Z') || (b >= 'a' && b <= 'z') || (b >= '0' && b <= '9') ||
		b == '-' || b == '_' || b == '.' || b == '~'
}

var _ = decodeJSON // suppress unused warning if not called
