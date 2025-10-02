// Acexy - Copyright (C) 2024 - Javinator9889 <dev at javinator9889 dot com>
// This program comes with ABSOLUTELY NO WARRANTY; for details type `show w'.
// This is free software, and you are welcome to redistribute it
// under certain conditions; type `show c' for details.
package main

import (
	_ "embed"
	"encoding/json"
	"flag"
	"fmt"
	"javinator9889/acexy/lib/acexy"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"time"

	"github.com/dustin/go-humanize"
	"net/url"
	"strings"
)

var (
	addr              string
	scheme            string
	host              string
	port              int
	streamTimeout     time.Duration
	m3u8              bool
	emptyTimeout      time.Duration
	size              Size
	noResponseTimeout time.Duration
	maxStreamsPerEngine int
)

//go:embed LICENSE.short
var LICENSE string

// The API URL we are listening to
const APIv1_URL = "/ace"

type Proxy struct {
	Acexy *acexy.Acexy
	Orch  *orchClient
}

type Size struct {
	Bytes   uint64
	Default uint64
}

func (p *Proxy) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	switch r.URL.Path {
	case APIv1_URL + "/getstream":
		fallthrough
	case APIv1_URL + "/getstream/":
		p.HandleStream(w, r)
	case APIv1_URL + "/status":
		p.HandleStatus(w, r)
	case "/":
		_, _ = fmt.Fprintln(w, LICENSE)
	default:
		http.NotFound(w, r)
	}
}

func (p *Proxy) HandleStream(w http.ResponseWriter, r *http.Request) {
	// Verify the request method
	if r.Method != http.MethodGet {
		slog.Error("Method not allowed", "method", r.Method, "path", r.URL.Path)
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	q := r.URL.Query()
	// Verify the client has included the ID parameter
	aceId, err := acexy.NewAceID(q.Get("id"), q.Get("infohash"))
	if err != nil {
		slog.Error("ID parameter is required", "path", r.URL.Path, "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}

	// Check that the client is not trying to force a PID
	if _, ok := q["pid"]; ok {
		slog.Error("PID parameter is not allowed", "path", r.URL.Path)
		http.Error(w, "PID parameter is not allowed", http.StatusBadRequest)
		return
	}

	// Select the best available engine from orchestrator if configured
	var selectedHost string
	var selectedPort int
	
	if p.Orch != nil {
		// Try to get an available engine from orchestrator
		host, port, err := p.Orch.SelectBestEngine()
		if err != nil {
			slog.Warn("Failed to select engine from orchestrator, falling back to configured engine", "error", err)
			selectedHost = p.Acexy.Host
			selectedPort = p.Acexy.Port
		} else {
			selectedHost = host
			selectedPort = port
			slog.Info("Selected engine from orchestrator", "host", host, "port", port)
		}
	} else {
		// No orchestrator configured, use the default configured engine
		selectedHost = p.Acexy.Host
		selectedPort = p.Acexy.Port
	}

	// Temporarily update acexy configuration for this request
	originalHost := p.Acexy.Host
	originalPort := p.Acexy.Port
	p.Acexy.Host = selectedHost
	p.Acexy.Port = selectedPort

	// Restore original configuration after stream handling
	defer func() {
		p.Acexy.Host = originalHost
		p.Acexy.Port = originalPort
	}()

	// Gather the stream information
	stream, err := p.Acexy.FetchStream(aceId, q)
	if err != nil {
		slog.Error("Failed to start stream", "stream", aceId, "error", err)

		// Emit error event to orchestrator even if FetchStream fails
		if p.Orch != nil {
			_, key := aceId.ID()
			// Generate stream ID for failed stream
			failedStreamID := key + "|fetch_failed"
			slog.Debug("Emitting error event for failed stream fetch", "stream_id", failedStreamID)
			p.Orch.EmitEnded(failedStreamID, "fetch_stream_failed")
		}

		http.Error(w, "Failed to start stream: "+err.Error(), http.StatusInternalServerError)
		return
	}

	// Prepare orchestrator event data early
	var orchEventData struct {
		streamID   string
		idType     acexy.AceIDType
		key        string
		playbackID string
	}

	if p.Orch != nil && stream != nil {
		orchEventData.idType, orchEventData.key = aceId.ID()
		orchEventData.playbackID = playbackIDFromStat(stream.StatURL)
		// Generate consistent stream ID: key|playback_session_id format expected by orchestrator
		orchEventData.streamID = orchEventData.key + "|" + orchEventData.playbackID

		// Emit stream started event early for orchestrator tracking
		// Use the selected engine host/port instead of the original acexy configuration
		orchKeyType := mapAceIDTypeToOrchestrator(orchEventData.idType)
		p.Orch.EmitStarted(selectedHost, selectedPort, orchKeyType, orchEventData.key,
			orchEventData.playbackID, stream.StatURL, stream.CommandURL, orchEventData.streamID)

		// Ensure stream ended event is always emitted, even on errors
		defer func() {
			if r := recover(); r != nil {
				p.Orch.EmitEnded(orchEventData.streamID, "panic")
				panic(r) // Re-panic after logging
			} else {
				p.Orch.EmitEnded(orchEventData.streamID, "handler_exit")
			}
		}()
	}

	// Set response headers first, before starting the stream or writing status
	// When in M3U8 mode, the client connects directly to a subset of endpoints, so we are blind to what the client
	// is doing. However, it periodically polls the M3U8 list to verify nothing has changed,
	// simulating a new connection. Therefore, we can accumulate a lot of open streams and let
	// the timeout finish them.
	//
	// When in MPEG-TS mode, the client connects to the endpoint and waits for the stream to finish.
	// This is a blocking operation, so we can finish the stream when the client disconnects.
	switch p.Acexy.Endpoint {
	case acexy.M3U8_ENDPOINT:
		w.Header().Set("Content-Type", "application/x-mpegURL")
		timedOut := acexy.SetTimeout(streamTimeout)
		defer func() {
			<-timedOut
			p.Acexy.StopStream(stream, w)
		}()
	case acexy.MPEG_TS_ENDPOINT:
		w.Header().Set("Content-Type", "video/MP2T")
		w.Header().Set("Transfer-Encoding", "chunked")
		defer p.Acexy.StopStream(stream, w)
	}

	// And start playing the stream. The `StartStream` will dump the contents of the new or
	// existing stream to the client. It takes an interface of `io.Writer` to write the stream
	// contents to. The `http.ResponseWriter` implements the `io.Writer` interface, so we can
	// pass it directly.
	slog.Debug("Starting stream", "path", r.URL.Path, "id", aceId)
	if err := p.Acexy.StartStream(stream, w); err != nil {
		slog.Error("Failed to start stream", "stream", aceId, "error", err)
		// Emit error event to orchestrator before returning
		if p.Orch != nil && orchEventData.streamID != "" {
			p.Orch.EmitEnded(orchEventData.streamID, "start_stream_failed")
		}
		http.Error(w, "Failed to start stream: "+err.Error(), http.StatusInternalServerError)
		return
	}

	// Now that we know the stream started successfully, write the status
	w.WriteHeader(http.StatusOK)

	// And wait for the client to disconnect
	select {
	case <-r.Context().Done():
		slog.Debug("Client disconnected", "path", r.URL.Path)
	case <-p.Acexy.WaitStream(stream):
		slog.Debug("Stream finished", "path", r.URL.Path)
	}
}

func (p *Proxy) HandleStatus(w http.ResponseWriter, r *http.Request) {
	// Verify the request method
	if r.Method != http.MethodGet {
		slog.Error("Method not allowed", "method", r.Method, "path", r.URL.Path)
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// Check if the client has included the ID parameter
	var id *acexy.AceID
	if r.URL.Query().Has("id") || r.URL.Query().Has("infohash") {
		aceId, err := acexy.NewAceID(r.URL.Query().Get("id"), r.URL.Query().Get("infohash"))
		if err != nil {
			slog.Error("Invalid ID parameter", "path", r.URL.Path, "error", err)
			http.Error(w, "Invalid ID parameter", http.StatusBadRequest)
			return
		}
		id = &aceId
	}

	// Get the status
	status, err := p.Acexy.GetStatus(id)
	if err != nil {
		slog.Error("Failed to get status", "error", err)
		http.Error(w, "Failed to get status: "+err.Error(), http.StatusInternalServerError)
		return
	}

	// And write it to the response
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"clients":  status.Clients,
		"streams":  status.Streams,
		"streamId": status.ID,
		"stat_url": status.StatURL,
	})
}

func (s *Size) Set(value string) error {
	size, err := humanize.ParseBytes(value)
	if err != nil {
		return err
	}
	s.Bytes = uint64(size)
	return nil
}

func (s *Size) String() string { return humanize.Bytes(s.Bytes) }

func (s *Size) Get() any { return s.Bytes }

func parseArgs() {
	// Parse the command-line arguments
	flag.StringVar(&addr, "addr", "127.0.0.1:6878", "Server address")
	flag.StringVar(&scheme, "scheme", "http", "AceStream scheme")
	flag.StringVar(&host, "host", "127.0.0.1", "AceStream host (fallback when orchestrator not configured)")
	flag.IntVar(&port, "port", 6878, "AceStream port (fallback when orchestrator not configured)")
	flag.DurationVar(&streamTimeout, "timeout", 60*time.Second, "Stream timeout (M3U8 mode)")
	flag.BoolVar(&m3u8, "m3u8", false, "M3U8 mode")
	flag.DurationVar(&emptyTimeout, "emptyTimeout", 10*time.Second, "Empty timeout (no data copied)")
	flag.DurationVar(&noResponseTimeout, "noResponseTimeout", 20*time.Second, "Timeout to receive first response byte from engine")
	flag.IntVar(&maxStreamsPerEngine, "maxStreamsPerEngine", 1, "Maximum streams per engine when using orchestrator")
	flag.Var(&size, "buffer", "Buffer size for copying (e.g. 1MiB)")
	size.Default = 1 << 20

	// Actually parse the command line flags
	flag.Parse()

	// Env overrides
	if v := os.Getenv("ACEXY_ADDR"); v != "" {
		addr = v
	}
	if v := os.Getenv("ACEXY_SCHEME"); v != "" {
		scheme = v
	}
	if v := os.Getenv("ACEXY_HOST"); v != "" {
		host = v
	}
	if v := os.Getenv("ACEXY_PORT"); v != "" {
		if p, err := strconv.Atoi(v); err == nil {
			port = p
		}
	}
	if v := os.Getenv("ACEXY_TIMEOUT"); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			streamTimeout = d
		}
	}
	if v := os.Getenv("ACEXY_M3U8"); v != "" {
		m3u8 = v == "1" || v == "true" || v == "TRUE"
	}
	if v := os.Getenv("ACEXY_EMPTY_TIMEOUT"); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			emptyTimeout = d
		}
	}
	if v := os.Getenv("ACEXY_NO_RESPONSE_TIMEOUT"); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			noResponseTimeout = d
		}
	}
	if v := os.Getenv("ACEXY_BUFFER"); v != "" {
		if s, err := humanize.ParseBytes(v); err == nil {
			size.Bytes = s
		}
	}
	if v := os.Getenv("ACEXY_MAX_STREAMS_PER_ENGINE"); v != "" {
		if m, err := strconv.Atoi(v); err == nil && m > 0 {
			maxStreamsPerEngine = m
		}
	}
}

func LookupLogLevel() slog.Level {
	logLevel := os.Getenv("ACEXY_LOG_LEVEL")
	switch logLevel {
	case "DEBUG":
		return slog.LevelDebug
	case "INFO":
		return slog.LevelInfo
	case "WARN":
		return slog.LevelWarn
	case "ERROR":
		return slog.LevelError
	default:
		return slog.LevelInfo
	}
}

func main() {
	// Parse the command-line arguments
	parseArgs()
	slog.SetLogLoggerLevel(LookupLogLevel())
	slog.Debug("CLI Args", "args", flag.CommandLine)

	var endpoint acexy.AcexyEndpoint
	if m3u8 {
		endpoint = acexy.M3U8_ENDPOINT
	} else {
		endpoint = acexy.MPEG_TS_ENDPOINT
	}
	
	// Create orchestrator client
	orchURL := os.Getenv("ACEXY_ORCH_URL")
	var orchClient *orchClient
	if orchURL != "" {
		orchClient = newOrchClient(orchURL)
		orchClient.SetMaxStreamsPerEngine(maxStreamsPerEngine)
		slog.Info("Orchestrator integration enabled", "url", orchURL, "max_streams_per_engine", maxStreamsPerEngine)
	} else {
		slog.Info("Orchestrator integration disabled - using fallback engine configuration", "host", host, "port", port)
	}
	
	// Create a new Acexy instance
	acexy := &acexy.Acexy{
		Scheme:            scheme,
		Host:              host,
		Port:              port,
		Endpoint:          endpoint,
		EmptyTimeout:      emptyTimeout,
		BufferSize:        int(size.Get().(uint64)),
		NoResponseTimeout: noResponseTimeout,
	}
	acexy.Init()

	// Create a new HTTP server
	proxy := &Proxy{Acexy: acexy, Orch: orchClient}
	mux := http.NewServeMux()
	mux.Handle(APIv1_URL+"/getstream", proxy)
	mux.Handle(APIv1_URL+"/getstream/", proxy)
	mux.Handle(APIv1_URL+"/status", proxy)
	mux.Handle("/", proxy) // Let proxy handle all other requests including root

	// Start the HTTP server
	slog.Info("Starting server", "addr", addr)
	if err := http.ListenAndServe(addr, mux); err != nil {
		slog.Error("Failed to start server", "error", err)
		os.Exit(1)
	}
}

// mapAceIDTypeToOrchestrator maps acexy ID types to orchestrator expected types
func mapAceIDTypeToOrchestrator(aceType acexy.AceIDType) string {
	switch aceType {
	case "infohash":
		return "infohash"
	case "id":
		// In AceStream context, "id" typically refers to content_id
		return "content_id"
	default:
		return "content_id" // default fallback
	}
}
func playbackIDFromStat(statURL string) string {
	if statURL == "" {
		return ""
	}

	u, err := url.Parse(statURL)
	if err != nil {
		return ""
	}

	parts := strings.Split(strings.Trim(u.Path, "/"), "/")
	// Expect at least 3 parts: ['ace', 'stat', 'infohash', 'playback_session_id']
	// But the path structure could be: .../ace/stat/<infohash>/<playback_session_id>
	if len(parts) >= 4 && parts[len(parts)-3] == "stat" {
		return parts[len(parts)-1]
	}

	// Fallback: if path structure is different but has at least one part, return the last one
	if len(parts) >= 1 {
		return parts[len(parts)-1]
	}

	return ""
}
