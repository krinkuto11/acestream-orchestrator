// Acexy - Copyright (C) 2024 - Javinator9889 <dev at javinator9889 dot com>
// This program comes with ABSOLUTELY NO WARRANTY; for details type `show w'.
// This is free software, and you are welcome to redistribute it
// under certain conditions; type `show c' for details.
package main

import (
	_ "embed"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"javinator9889/acexy/lib/acexy"
	"javinator9889/acexy/lib/debug"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/dustin/go-humanize"
)

var (
	addr                string
	scheme              string
	host                string
	port                int
	streamTimeout       time.Duration
	m3u8                bool
	emptyTimeout        time.Duration
	size                Size
	noResponseTimeout   time.Duration
	maxStreamsPerEngine int
	debugMode           bool
	debugLogDir         string
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
	debugLog := debug.GetDebugLogger()
	startTime := time.Now()
	var statusCode int = http.StatusOK
	var aceIDStr string

	// Defer debug logging until the end
	defer func() {
		duration := time.Since(startTime)
		debugLog.LogRequest(r.Method, r.URL.Path, duration, statusCode, aceIDStr)

		// Detect slow requests (over 5 seconds)
		if duration > 5*time.Second {
			debugLog.LogStressEvent(
				"slow_request",
				"warning",
				fmt.Sprintf("Request took %.2fs", duration.Seconds()),
				map[string]interface{}{
					"path":     r.URL.Path,
					"ace_id":   aceIDStr,
					"duration": duration.Seconds(),
				},
			)
		}
	}()

	// Verify the request method
	if r.Method != http.MethodGet {
		statusCode = http.StatusMethodNotAllowed
		slog.Error("Method not allowed", "method", r.Method, "path", r.URL.Path)
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	q := r.URL.Query()
	// Verify the client has included the ID parameter
	aceId, err := acexy.NewAceID(q.Get("id"), q.Get("infohash"))
	if err != nil {
		statusCode = http.StatusBadRequest
		slog.Error("ID parameter is required", "path", r.URL.Path, "error", err)
		http.Error(w, err.Error(), http.StatusBadRequest)
		return
	}
	aceIDStr = aceId.String()

	// Check that the client is not trying to force a PID
	if _, ok := q["pid"]; ok {
		statusCode = http.StatusBadRequest
		slog.Error("PID parameter is not allowed", "path", r.URL.Path)
		http.Error(w, "PID parameter is not allowed", http.StatusBadRequest)
		return
	}

	// Select the best available engine from orchestrator if configured
	var selectedHost string
	var selectedPort int
	var selectedEngineContainerID string

	if p.Orch != nil {
		// Try to get an available engine from orchestrator
		host, port, engineContainerID, err := p.Orch.SelectBestEngine()
		if err != nil {
			// Check if it's a structured provisioning error
			var provErr *ProvisioningError
			if errors.As(err, &provErr) {
				statusCode = http.StatusServiceUnavailable
				p.handleProvisioningError(w, provErr)
				return
			}

			// Check if it's a provisioning issue and provide specific error messages (legacy)
			if strings.Contains(err.Error(), "VPN") {
				statusCode = http.StatusServiceUnavailable
				slog.Error("Stream failed due to VPN issue", "error", err)
				http.Error(w, "Service temporarily unavailable: VPN connection required", http.StatusServiceUnavailable)
				return
			}
			if strings.Contains(err.Error(), "circuit breaker") {
				statusCode = http.StatusServiceUnavailable
				slog.Error("Stream failed due to circuit breaker", "error", err)
				http.Error(w, "Service temporarily unavailable: Too many failures, please retry later", http.StatusServiceUnavailable)
				return
			}
			if strings.Contains(err.Error(), "cannot provision") {
				statusCode = http.StatusServiceUnavailable
				slog.Error("Stream failed - provisioning blocked", "error", err)
				http.Error(w, fmt.Sprintf("Service temporarily unavailable: %s", err.Error()), http.StatusServiceUnavailable)
				return
			}

			slog.Warn("Failed to select engine from orchestrator, falling back to configured engine", "error", err)
			selectedHost = p.Acexy.Host
			selectedPort = p.Acexy.Port
		} else {
			selectedHost = host
			selectedPort = port
			selectedEngineContainerID = engineContainerID
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
		statusCode = http.StatusInternalServerError
		slog.Error("Failed to fetch stream", "stream", aceId, "error", err)

		http.Error(w, "Failed to start stream: "+err.Error(), http.StatusInternalServerError)
		return
	}

	// Emit stream started event to orchestrator for internal tracking
	var streamID string
	if p.Orch != nil {
		idType, key := aceId.ID()
		playbackID := playbackIDFromStat(stream.StatURL)
		streamID = key + "|" + playbackID
		orchKeyType := mapAceIDTypeToOrchestrator(idType)
		
		slog.Debug("Emitting stream_started event to orchestrator",
			"stream_id", streamID, "host", selectedHost, "port", selectedPort)
		
		p.Orch.EmitStarted(selectedHost, selectedPort, orchKeyType, key,
			playbackID, stream.StatURL, stream.CommandURL, streamID, selectedEngineContainerID)
	}

	// Set response headers
	switch p.Acexy.Endpoint {
	case acexy.M3U8_ENDPOINT:
		w.Header().Set("Content-Type", "application/x-mpegURL")
	case acexy.MPEG_TS_ENDPOINT:
		w.Header().Set("Content-Type", "video/MP2T")
		w.Header().Set("Transfer-Encoding", "chunked")
	}

	// Write headers before starting stream
	w.WriteHeader(http.StatusOK)

	// Start streaming - this blocks until complete or client disconnects
	slog.Debug("Starting stream", "path", r.URL.Path, "id", aceId)
	streamStartTime := time.Now()
	copier, streamErr := p.Acexy.StartStream(stream, w)
	streamDuration := time.Since(streamStartTime)
	
	// Determine reason for stream ending and classify the error
	var reason string
	var bytesCopied int64
	var detailedReason string
	
	if copier != nil {
		bytesCopied = copier.BytesCopied()
	}
	
	if streamErr != nil {
		slog.Error("Failed to stream", "stream", aceId, "error", streamErr, "bytes_copied", bytesCopied, "duration", streamDuration)
		
		// Classify the error to determine appropriate reason with more detail
		reason, detailedReason = classifyDisconnectReason(streamErr)
		
		// Log detailed disconnect information in debug mode
		debugLog.LogDisconnect(streamID, aceIDStr, reason, streamErr.Error(), bytesCopied, streamDuration, map[string]interface{}{
			"detailed_reason": detailedReason,
			"engine_host":     selectedHost,
			"engine_port":     selectedPort,
			"container_id":    selectedEngineContainerID,
		})
	} else {
		// Stream completed successfully
		slog.Debug("Stream completed", "path", r.URL.Path, "id", aceId, "bytes_copied", bytesCopied, "duration", streamDuration)
		reason = "completed"
		detailedReason = "stream finished normally"
		
		// Log successful completion in debug mode
		debugLog.LogDisconnect(streamID, aceIDStr, reason, "", bytesCopied, streamDuration, map[string]interface{}{
			"detailed_reason": detailedReason,
			"engine_host":     selectedHost,
			"engine_port":     selectedPort,
			"container_id":    selectedEngineContainerID,
		})
	}
	
	// Emit stream_ended event to orchestrator and send stop command to engine
	if p.Orch != nil && streamID != "" {
		slog.Debug("Stream ending, emitting stream_ended event",
			"stream_id", streamID, "reason", reason)
		p.Orch.EmitEnded(streamID, reason)
		
		// Send stop command to AceStream engine to clean up resources
		if err := acexy.CloseStream(stream); err != nil {
			slog.Debug("Failed to send stop command to engine", 
				"stream_id", streamID, "error", err)
		}
	}
}

// handleProvisioningError handles structured provisioning errors and returns user-friendly responses
func (p *Proxy) handleProvisioningError(w http.ResponseWriter, err *ProvisioningError) {
	details := err.Details

	// Log with structured data
	slog.Error("Provisioning blocked",
		"code", details.Code,
		"message", details.Message,
		"recovery_eta", details.RecoveryETASeconds,
		"should_wait", details.ShouldWait)

	// Set Retry-After header if recovery ETA is available
	if details.RecoveryETASeconds > 0 {
		w.Header().Set("Retry-After", fmt.Sprintf("%d", details.RecoveryETASeconds))
	}

	// Return user-friendly error based on code
	var userMessage string
	switch details.Code {
	case "vpn_disconnected":
		userMessage = "Service temporarily unavailable: VPN connection is being restored"
	case "circuit_breaker":
		userMessage = "Service temporarily unavailable: System is recovering from errors"
	case "max_capacity":
		userMessage = "Service at capacity: Please try again in a moment"
	case "vpn_error":
		userMessage = "Service temporarily unavailable: VPN error during provisioning"
	default:
		userMessage = "Service temporarily unavailable: " + details.Message
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusServiceUnavailable)
	json.NewEncoder(w).Encode(map[string]interface{}{
		"error":       userMessage,
		"retry_after": details.RecoveryETASeconds,
	})
}

func (p *Proxy) HandleStatus(w http.ResponseWriter, r *http.Request) {
	// Verify the request method
	if r.Method != http.MethodGet {
		slog.Error("Method not allowed", "method", r.Method, "path", r.URL.Path)
		http.Error(w, "Method not allowed", http.StatusMethodNotAllowed)
		return
	}

	// In stateless mode, just return basic health status
	_, err := p.Acexy.GetStatus(nil)
	if err != nil {
		slog.Error("Failed to get status", "error", err)
		http.Error(w, "Failed to get status: "+err.Error(), http.StatusInternalServerError)
		return
	}

	// Return simple health check
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]any{
		"status": "ok",
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
	flag.BoolVar(&debugMode, "debugMode", false, "Enable debug mode with detailed logging")
	flag.StringVar(&debugLogDir, "debugLogDir", "./debug_logs", "Directory for debug logs")
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
	if v := os.Getenv("DEBUG_MODE"); v != "" {
		debugMode = v == "1" || v == "true" || v == "TRUE"
	}
	if v := os.Getenv("DEBUG_LOG_DIR"); v != "" {
		debugLogDir = v
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

	// Initialize debug logger
	debug.InitDebugLogger(debugMode, debugLogDir)
	if debugMode {
		slog.Info("Debug mode enabled", "log_dir", debugLogDir)
	}

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

// playbackIDFromStat extracts the playback session ID from a stat URL
func playbackIDFromStat(statURL string) string {
	if statURL == "" {
		return ""
	}

	// Validate the URL is parseable (basic check)
	if !strings.Contains(statURL, "/") {
		slog.Debug("Invalid stat URL format", "url", statURL)
		return ""
	}

	// Parse URL to extract path components
	// Expected format: .../ace/stat/<infohash>/<playback_session_id>
	// We use simple string splitting for efficiency, but validate structure
	urlPath := statURL
	if idx := strings.Index(statURL, "://"); idx >= 0 {
		// Remove scheme (http:// or https://)
		urlPath = statURL[idx+3:]
	}
	if idx := strings.Index(urlPath, "/"); idx >= 0 {
		// Remove host/port, keep only path
		urlPath = urlPath[idx:]
	}
	
	parts := strings.Split(strings.Trim(urlPath, "/"), "/")
	
	// Find the "stat" segment and return the ID after it
	// Expected: [..., "ace", "stat", <infohash>, <playback_session_id>]
	for i, part := range parts {
		if part == "stat" && i+2 < len(parts) {
			return parts[i+2] // Return playback_session_id
		}
	}
	
	// Fallback: return last path component if structure is different
	if len(parts) > 0 && parts[len(parts)-1] != "" {
		slog.Debug("Using fallback playback ID extraction", "url", statURL, "id", parts[len(parts)-1])
		return parts[len(parts)-1]
	}
	
	slog.Warn("Could not extract playback ID from stat URL", "url", statURL)
	return ""
}

// classifyDisconnectReason analyzes an error and returns a reason code and detailed description
// This provides more thorough debugging information about why client disconnects occur
func classifyDisconnectReason(err error) (reason string, detailedReason string) {
	if err == nil {
		return "completed", "stream finished normally"
	}
	
	errStr := err.Error()
	errStrLower := strings.ToLower(errStr)
	
	// Check for empty timeout error first (specific check before string matching)
	if strings.Contains(errStrLower, "stream empty timeout") {
		return "empty_timeout", "stream closed due to inactivity (no data received within timeout period)"
	}
	
	// Check for client-side disconnects
	if strings.Contains(errStrLower, "broken pipe") {
		return "client_disconnected", "client closed connection (broken pipe)"
	}
	if strings.Contains(errStrLower, "connection reset by peer") {
		return "client_disconnected", "client reset connection (RST packet received)"
	}
	if strings.Contains(errStrLower, "connection reset") {
		return "client_disconnected", "connection reset by network or client"
	}
	if strings.Contains(errStrLower, "write: connection refused") {
		return "client_disconnected", "client refused connection on write"
	}
	
	// Check for timeout-related errors
	if strings.Contains(errStrLower, "i/o timeout") {
		return "timeout", "I/O operation timed out"
	}
	if strings.Contains(errStrLower, "deadline exceeded") {
		return "timeout", "deadline exceeded (context timeout or read/write timeout)"
	}
	if strings.Contains(errStrLower, "timeout") {
		return "timeout", "operation timed out"
	}
	
	// Check for network errors
	if strings.Contains(errStrLower, "network is unreachable") {
		return "network_error", "network is unreachable"
	}
	if strings.Contains(errStrLower, "no route to host") {
		return "network_error", "no route to host"
	}
	if strings.Contains(errStrLower, "host is down") {
		return "network_error", "host is down"
	}
	
	// Check for unexpected EOF - must check before generic "eof" to be specific
	if strings.Contains(errStrLower, "unexpected eof") {
		return "eof", "unexpected EOF during read"
	}
	
	// Check for EOF-related errors
	if errors.Is(err, io.EOF) {
		return "eof", "unexpected EOF from source stream"
	}
	if strings.Contains(errStrLower, "eof") {
		return "eof", "end of file encountered unexpectedly"
	}
	
	// Check for closed pipe/connection errors
	if errors.Is(err, io.ErrClosedPipe) {
		return "closed_pipe", "write to closed pipe"
	}
	if strings.Contains(errStrLower, "use of closed network connection") {
		return "closed_connection", "attempted to use closed network connection"
	}
	
	// Check for buffer or memory errors
	if strings.Contains(errStrLower, "no buffer space available") {
		return "buffer_error", "system out of buffer space"
	}
	if strings.Contains(errStrLower, "cannot allocate memory") {
		return "memory_error", "system out of memory"
	}
	
	// Generic error fallback
	return "error", fmt.Sprintf("unclassified error: %s", errStr)
}
