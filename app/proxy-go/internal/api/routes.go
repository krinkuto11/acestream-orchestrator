// Package api implements the HTTP server for the Go proxy.
// Stream endpoints (/ace/getstream, /ace/manifest.m3u8, /ace/hls/...) are
// handled natively; all other requests are reverse-proxied to the Python
// orchestrator so clients always hit the same port (8000).
package api

import (
	"context"
	"crypto/sha1"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net"
	"net/http"
	"net/http/httputil"
	"net/http/pprof"
	"net/url"
	"regexp"
	"strings"
	"time"
	"unicode"

	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/acestream/proxy/internal/buffer"
	"github.com/acestream/proxy/internal/config"
	"github.com/acestream/proxy/internal/hls"
	"github.com/acestream/proxy/internal/stream"
	"github.com/acestream/proxy/internal/telemetry"
)

var fileIndexRE = regexp.MustCompile(`^\d+(,\d+)*$`)

// Server is the main HTTP server for the Go proxy.
type Server struct {
	hub          *stream.Hub
	orchestProxy *httputil.ReverseProxy
	mux          *http.ServeMux
}

// NewServer wires up all routes.
func NewServer(hub *stream.Hub, orchestratorURL string) *Server {
	target, err := url.Parse(orchestratorURL)
	if err != nil {
		panic("invalid orchestrator URL: " + err.Error())
	}
	orchProxy := httputil.NewSingleHostReverseProxy(target)
	orchProxy.ErrorHandler = func(w http.ResponseWriter, r *http.Request, err error) {
		slog.Warn("orchestrator proxy error", "path", r.URL.Path, "err", err)
		http.Error(w, "upstream error: "+err.Error(), http.StatusBadGateway)
	}

	s := &Server{
		hub:          hub,
		orchestProxy: orchProxy,
		mux:          http.NewServeMux(),
	}
	s.registerRoutes()
	return s
}

func (s *Server) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	s.mux.ServeHTTP(w, r)
}

func (s *Server) registerRoutes() {
	// Proxy-native endpoints
	s.mux.HandleFunc("/ace/getstream", withTelemetry("TS", s.handleGetStream))
	s.mux.HandleFunc("/ace/manifest.m3u8", withTelemetry("HLS", s.handleHLSManifest))
	s.mux.HandleFunc("/ace/hls/segment", withTelemetry("HLS", s.handleHLSSegment))
	s.mux.HandleFunc("/ace/hls/segment.ts", withTelemetry("HLS", s.handleHLSSegment))
	s.mux.HandleFunc("/ace/hls/", withTelemetry("HLS", s.handleHLSSegmentLegacy))  // /ace/hls/{id}/segment/{path}

	// Internal control endpoints (called by Python orchestrator)
	s.mux.HandleFunc("/internal/proxy/stop", s.handleInternalStop)
	s.mux.HandleFunc("/internal/proxy/swap", s.handleInternalSwap)

	// Health
	s.mux.HandleFunc("/proxy/health", s.handleHealth)

	// Metrics
	s.mux.Handle("/metrics/proxy", promhttp.Handler())
	
	// Debug / Profiling — gated behind API key when one is configured.
	// /debug/pprof/profile burns a CPU core for 30 s, so anonymous access is dangerous.
	s.mux.HandleFunc("/debug/pprof/", requireAPIKey(pprof.Index))
	s.mux.HandleFunc("/debug/pprof/cmdline", requireAPIKey(pprof.Cmdline))
	s.mux.HandleFunc("/debug/pprof/profile", requireAPIKey(pprof.Profile))
	s.mux.HandleFunc("/debug/pprof/symbol", requireAPIKey(pprof.Symbol))
	s.mux.HandleFunc("/debug/pprof/trace", requireAPIKey(pprof.Trace))

	// Everything else → Python orchestrator
	s.mux.HandleFunc("/", s.handlePassthrough)
}

// --- Stream handler ---

func (s *Server) handleGetStream(w http.ResponseWriter, r *http.Request) {
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
		ep, err := s.selectEngine()
		if err != nil {
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
		s.hub.StartStream(r.Context(), p)
		deadline := time.Now().Add(500 * time.Millisecond)
		for time.Now().Before(deadline) {
			mgr, buf, cm = s.hub.GetEntry(streamKey)
			if mgr != nil {
				break
			}
			select {
			case <-r.Context().Done():
				http.Error(w, "request cancelled", http.StatusServiceUnavailable)
				return
			case <-time.After(10 * time.Millisecond):
			}
		}
		if mgr == nil {
			http.Error(w, "stream start failed", http.StatusInternalServerError)
			return
		}
	} else {
		s.hub.CancelShutdown(streamKey)
		streamMode = mgr.StreamMode()
		controlMode = mgr.ControlMode()
		if streamMode == "" {
			streamMode = strings.ToUpper(config.C.StreamMode)
		}
	}

	// HLS mode: the client gets a manifest, not a raw TS byte stream.
	// Start the stream first (ring buffer fills while client fetches segments),
	// then redirect to the manifest endpoint which carries the same input params.
	if streamMode == "HLS" {
		http.Redirect(w, r, "/ace/manifest.m3u8?"+r.URL.RawQuery, http.StatusFound)
		return
	}

	// MPEG-TS mode: stream raw bytes.
	clientIP := clientIP(r)
	clientID := buildClientID(clientIP, r.Header.Get("User-Agent"))
	_ = controlMode // used via mgr.ControlMode() inside the streamer if needed

	// Check capacity before writing headers — once headers are committed a
	// 503 can no longer be sent; the client would receive a zero-byte TS stream.
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
		s.hub.ScheduleShutdown(streamKey, config.C.ChannelShutdownDelay)
	}
}

// --- HLS manifest handler ---
//
// Two paths depending on control mode:
//
//   HTTP mode  – the AceStream engine exposes its own HLS manifest; we proxy
//                and rewrite segment URLs so clients always hit this proxy.
//
//   API mode   – the engine only gives a raw MPEG-TS stream (no HLS endpoint).
//                We run an in-process hls.Segmenter that reads from the ring
//                buffer and cuts segments using PCR timestamps. No FFmpeg needed.

func (s *Server) handleHLSManifest(w http.ResponseWriter, r *http.Request) {
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

	// When the stream is already running, use its stored control mode.
	// When it's not running yet, call selectEngine() once for both the mode and engine params.
	if mgr != nil {
		s.hub.CancelShutdown(streamKey)
		controlMode := mgr.ControlMode()
		if strings.ToLower(controlMode) == "api" {
			s.handleHLSManifestAPIMode(w, r, inputType, inputVal, fileIndexes, seekback, streamKey, mgr, buf)
			s.recordHLSClient(streamKey, 0, r)
			return
		}
		// HTTP mode: proxy the engine's manifest.
		engineURL := fmt.Sprintf("http://%s:%d/ace/manifest.m3u8?id=%s&file_indexes=%s",
			mgr.EngineHost(), mgr.EnginePort(), url.QueryEscape(inputVal), url.QueryEscape(fileIndexes))
		sess := hls.NewSession(streamKey, engineURL, fmt.Sprintf("http://%s", r.Host))
		defer sess.Stop()
		if err := sess.ServeManifest(r.Context(), w); err != nil {
			http.Error(w, "manifest error: "+err.Error(), http.StatusBadGateway)
			return
		}
		s.recordHLSClient(streamKey, 0, r)
		return
	}

	// Stream not running — call selectEngine() to get both control mode and engine host.
	ep, err := s.selectEngine()
	if err != nil {
		http.Error(w, "no engine available: "+err.Error(), http.StatusServiceUnavailable)
		return
	}

	if ep.ControlMode == "api" {
		s.handleHLSManifestAPIMode(w, r, inputType, inputVal, fileIndexes, seekback, streamKey, nil, nil)
		s.recordHLSClient(streamKey, 0, r)
		return
	}

	// HTTP mode: proxy the engine's manifest without starting a hub stream.
	engineURL := fmt.Sprintf("http://%s:%d/ace/manifest.m3u8?id=%s&file_indexes=%s",
		ep.EngineParams.Host, ep.EngineParams.Port, url.QueryEscape(inputVal), url.QueryEscape(fileIndexes))
	sess := hls.NewSession(streamKey, engineURL, fmt.Sprintf("http://%s", r.Host))
	defer sess.Stop()
	if err := sess.ServeManifest(r.Context(), w); err != nil {
		http.Error(w, "manifest error: "+err.Error(), http.StatusBadGateway)
	}
	s.recordHLSClient(streamKey, 0, r)
}

// handleHLSManifestAPIMode serves a live HLS manifest backed by our in-process
// Segmenter. The TS stream is started (if not already running) and a Segmenter
// is created lazily; subsequent manifest polls get updated playlists.
func (s *Server) handleHLSManifestAPIMode(
	w http.ResponseWriter, r *http.Request,
	inputType, inputVal, fileIndexes string, seekback int,
	streamKey string, mgr *stream.Manager, buf *buffer.RingBuffer,
) {
	if mgr == nil {
		ep, err := s.selectEngine()
		if err != nil {
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
		s.hub.StartStream(r.Context(), p)
		deadline := time.Now().Add(500 * time.Millisecond)
		for time.Now().Before(deadline) {
			_, buf, _ = s.hub.GetEntry(streamKey)
			if buf != nil {
				break
			}
			select {
			case <-r.Context().Done():
				http.Error(w, "request cancelled", http.StatusServiceUnavailable)
				return
			case <-time.After(10 * time.Millisecond):
			}
		}
		if buf == nil {
			http.Error(w, "stream start failed", http.StatusInternalServerError)
			return
		}
	} else {
		s.hub.CancelShutdown(streamKey)
	}

	seg := s.hub.GetOrCreateSegmenter(streamKey, buf)
	if seg == nil {
		http.Error(w, "segmenter unavailable", http.StatusInternalServerError)
		return
	}

	// Prebuffer: hold the manifest request until we have enough segments so 
	// the player has immediate runway. This prevents 'buffering...' loops 
	// during startup on jittery connections.
	
	prebufSec := 0
	if mgr != nil {
		prebufSec = mgr.PrebufferSeconds()
	}
	if prebufSec <= 0 {
		prebufSec = config.C.ProxyPrebufferSeconds
	}
	
	// Target segments: at least 1, otherwise based on prebuffer seconds.
	targetSegs := 1
	if prebufSec > 0 {
		// Assuming ~2.0s per segment (defaultTargetDurSec).
		// We add +1 for safety margin.
		if computed := (prebufSec / 2) + 1; computed > targetSegs {
			targetSegs = computed
		}
	}

	if seg.SegmentCount() < targetSegs {
		slog.Debug("HLS pre-buffering", "stream", streamKey, "current", seg.SegmentCount(), "target", targetSegs)
		deadline := time.Now().Add(45 * time.Second) // higher deadline for deep pre-buffering
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

// --- HLS segment handler ---
//
// Handles two variants on the same path /ace/hls/segment:
//   ?stream=<key>&url=<encoded>  – HTTP mode: fetch segment from engine URL
//   ?stream=<key>&seq=<n>        – API mode: serve from in-process Segmenter

func (s *Server) handleHLSSegment(w http.ResponseWriter, r *http.Request) {
	q := r.URL.Query()
	streamKey := sanitizeID(q.Get("stream"))
	if streamKey == "" {
		http.Error(w, "stream param required", http.StatusBadRequest)
		return
	}

	// API mode: serve by sequence number from in-memory Segmenter.
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

	// HTTP mode: fetch segment from engine via its URL.
	segURL := q.Get("url")
	if segURL == "" {
		http.Error(w, "url or seq param required", http.StatusBadRequest)
		return
	}
	decodedURL, err := url.QueryUnescape(segURL)
	if err != nil {
		http.Error(w, "invalid url param", http.StatusBadRequest)
		return
	}
	sess := hls.NewSession(streamKey, "", "")
	defer sess.Stop()
	if err := sess.ServeSegment(r.Context(), decodedURL, w); err != nil {
		http.Error(w, "segment error: "+err.Error(), http.StatusBadGateway)
	}
}

// --- HLS legacy segment handler (/ace/hls/{content_id}/segment/{path}) ---

func (s *Server) handleHLSSegmentLegacy(w http.ResponseWriter, r *http.Request) {
	s.orchestProxy.ServeHTTP(w, r)
}

// --- Internal control endpoints ---

func (s *Server) handleInternalStop(w http.ResponseWriter, r *http.Request) {
	streamKey := r.URL.Query().Get("stream")
	if streamKey == "" {
		http.Error(w, "stream param required", http.StatusBadRequest)
		return
	}
	s.hub.StopStream(streamKey)
	w.WriteHeader(http.StatusNoContent)
}

func (s *Server) handleInternalSwap(w http.ResponseWriter, r *http.Request) {
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

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	fmt.Fprintf(w, `{"status":"ok","worker":"%s"}`, s.hub.WorkerID())
}

// --- Passthrough ---

func (s *Server) handlePassthrough(w http.ResponseWriter, r *http.Request) {
	s.orchestProxy.ServeHTTP(w, r)
}

// recordHLSClient registers (or heartbeats) an HLS client against the stream's
// ClientManager so it appears in the Python telemetry SSE feed.
// bytesDelta should be 0 for manifest requests and the segment size for segments.
func (s *Server) recordHLSClient(streamKey string, bytesDelta int64, r *http.Request) {
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

// --- Engine selection (calls Python orchestrator internal API) ---

// engineSelection bundles engine params and stream-start settings from the orchestrator.
type engineSelection struct {
	stream.EngineParams
	PrebufferSeconds int
	PacingMultiplier float64
	StreamMode       string // "TS" or "HLS"
	ControlMode      string // "http" or "api"
}

func (s *Server) selectEngine() (engineSelection, error) {
	orchURL := config.C.OrchestratorURL + "/internal/proxy/select-engine"
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()

	req, err := http.NewRequestWithContext(ctx, http.MethodGet, orchURL, nil)
	if err != nil {
		return engineSelection{}, err
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return engineSelection{}, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return engineSelection{}, fmt.Errorf("orchestrator returned %d", resp.StatusCode)
	}

	var result struct {
		Host             string `json:"host"`
		Port             int    `json:"port"`
		APIPort          int    `json:"api_port"`
		ContainerID      string `json:"container_id"`
		PrebufferSeconds int     `json:"proxy_prebuffer_seconds"`
		PacingMultiplier float64 `json:"pacing_bitrate_multiplier"`
		StreamMode       string  `json:"stream_mode"`
		ControlMode      string `json:"control_mode"`
	}
	if err := decodeJSON(resp.Body, &result); err != nil {
		return engineSelection{}, err
	}
	if result.Host == "" || result.Port == 0 {
		return engineSelection{}, fmt.Errorf("no engine available")
	}

	// Fall back to env-configured values when orchestrator omits them.
	if result.StreamMode == "" {
		result.StreamMode = config.C.StreamMode
	}
	if result.ControlMode == "" {
		result.ControlMode = config.C.ControlMode
	}

	return engineSelection{
		EngineParams: stream.EngineParams{
			Host:        result.Host,
			Port:        result.Port,
			APIPort:     result.APIPort,
			ContainerID: result.ContainerID,
		},
		PrebufferSeconds: result.PrebufferSeconds,
		PacingMultiplier: result.PacingMultiplier,
		StreamMode:       strings.ToUpper(result.StreamMode),
		ControlMode:      strings.ToLower(result.ControlMode),
	}, nil
}

// --- Helpers ---

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

func clientIP(r *http.Request) string {
	if xff := r.Header.Get("X-Forwarded-For"); xff != "" {
		return strings.SplitN(xff, ",", 2)[0]
	}
	// Strip port — same client reconnects from a different ephemeral port on every
	// HLS manifest/segment fetch, which would produce a different client ID.
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

// requireAPIKey wraps a handler so it is only accessible when the request
// carries the configured API key (X-API-Key header or ?key= query param).
// If no API key is configured the handler is always allowed through.
func requireAPIKey(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		key := config.C.APIKey
		if key != "" && r.Header.Get("X-API-Key") != key && r.URL.Query().Get("key") != key {
			http.Error(w, "forbidden", http.StatusForbidden)
			return
		}
		next(w, r)
	}
}
