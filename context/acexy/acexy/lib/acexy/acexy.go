// Acexy - Copyright (C) 2024 - Javinator9889 <dev at javinator9889 dot com>
// This program comes with ABSOLUTELY NO WARRANTY; for details type `show w'.
// This is free software, and you are welcome to redistribute it
// under certain conditions; type `show c' for details.
package acexy

import (
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net/http"
	"net/url"
	"strconv"
	"time"

	"github.com/google/uuid"
)

// As of how the middleware is defined, we tell Go the structure that should match the HTTP
// response for AceStream: https://docs.acestream.net/developers/start-playback/#using-middleware.
type AceStreamResponse struct {
	PlaybackURL       string `json:"playback_url"`
	StatURL           string `json:"stat_url"`
	CommandURL        string `json:"command_url"`
	Infohash          string `json:"infohash"`
	PlaybackSessionID string `json:"playback_session_id"`
	IsLive            int    `json:"is_live"`
	IsEncrypted       int    `json:"is_encrypted"`
	ClientSessionID   int    `json:"client_session_id"`
}

type AceStreamMiddleware struct {
	Response AceStreamResponse `json:"response"`
	Error    string            `json:"error"`
}

type AceStreamCommand struct {
	Response string `json:"response"`
	Error    string `json:"error"`
}

type AcexyStatus struct {
	// Simplified status - just basic info
}

// The stream information from AceStream response
type AceStream struct {
	PlaybackURL string
	StatURL     string
	CommandURL  string
	ID          AceID
}

// Structure referencing the AceStream Proxy
// This is now a stateless proxy that forwards requests to AceStream engines
type Acexy struct {
	Scheme            string        // The scheme to be used when connecting to the AceStream middleware
	Host              string        // The host to be used when connecting to the AceStream middleware
	Port              int           // The port to be used when connecting to the AceStream middleware
	Endpoint          AcexyEndpoint // The endpoint to be used when connecting to the AceStream middleware
	EmptyTimeout      time.Duration // Timeout after which, if no data is written, the stream is closed
	BufferSize        int           // The buffer size to use when copying the data
	NoResponseTimeout time.Duration // Timeout to wait for a response from the AceStream middleware

	middleware *http.Client
}

type AcexyEndpoint string

// The AceStream API available endpoints
const (
	M3U8_ENDPOINT    AcexyEndpoint = "/ace/manifest.m3u8"
	MPEG_TS_ENDPOINT AcexyEndpoint = "/ace/getstream"
)

// Initializes the Acexy structure
func (a *Acexy) Init() {
	// The transport optimized for concurrent requests
	a.middleware = &http.Client{
		Transport: &http.Transport{
			DisableCompression:    true,
			MaxIdleConns:          100, // Increased for better concurrent performance
			MaxConnsPerHost:       100, // Increased for better concurrent performance
			MaxIdleConnsPerHost:   50,  // Reuse connections efficiently
			IdleConnTimeout:       30 * time.Second,
			ResponseHeaderTimeout: a.NoResponseTimeout,
			ExpectContinueTimeout: 1 * time.Second,
		},
	}
}

// FetchStream requests stream information from AceStream engine.
// This is stateless - each request gets a unique PID and stream instance.
func (a *Acexy) FetchStream(aceId AceID, extraParams url.Values) (*AceStream, error) {
	// Simply call the AceStream engine to get stream info
	middleware, err := GetStream(a, aceId, extraParams)
	if err != nil {
		slog.Error("Error getting stream middleware", "error", err)
		return nil, err
	}

	// Build and return stream information
	slog.Debug("Middleware Information", "id", aceId, "playback_url", middleware.Response.PlaybackURL)
	stream := &AceStream{
		PlaybackURL: middleware.Response.PlaybackURL,
		StatURL:     middleware.Response.StatURL,
		CommandURL:  middleware.Response.CommandURL,
		ID:          aceId,
	}

	slog.Info("Fetched stream from engine", "id", aceId)
	return stream, nil
}

// StartStream initiates the stream and proxies it to the output writer.
// This is stateless - just gets the stream from AceStream and copies it.
// Returns the copier instance (for metrics) and any error that occurred.
func (a *Acexy) StartStream(stream *AceStream, out io.Writer) (*Copier, error) {
	// Get the stream from AceStream
	resp, err := a.middleware.Get(stream.PlaybackURL)
	if err != nil {
		slog.Error("Failed to get stream", "error", err)
		return nil, err
	}
	defer resp.Body.Close()

	// Use buffered copier to reduce frame drops
	// The larger buffer (configured via ACEXY_BUFFER, default 4.2MiB) helps smooth out streaming by:
	// 1. Reducing frequency of write operations
	// 2. Better handling of network jitter
	// 3. Buffering bursts of data for consistent delivery
	copier := &Copier{
		Destination:  out,
		Source:       resp.Body,
		EmptyTimeout: a.EmptyTimeout,
		BufferSize:   a.BufferSize,
	}
	
	err = copier.Copy()
	if err != nil {
		// Don't suppress empty timeout errors - they should be reported
		if errors.Is(err, ErrEmptyTimeout) {
			slog.Debug("Stream copy ended due to empty timeout", "stream", stream.ID, "error", err)
			return copier, err
		}
		// Suppress io.EOF as it's a normal stream completion
		if !errors.Is(err, io.EOF) {
			slog.Debug("Stream copy completed with error", "stream", stream.ID, "error", err)
			return copier, err
		}
	}

	slog.Debug("Stream finished successfully", "stream", stream.ID)
	return copier, nil
}

// GetStream performs a request to the AceStream backend to start a new stream.
// Each request gets a unique PID to prevent conflicts.
func GetStream(a *Acexy, aceId AceID, extraParams url.Values) (*AceStreamMiddleware, error) {
	slog.Debug("Getting stream", "id", aceId)
	slog.Debug("Acexy Information", "scheme", a.Scheme, "host", a.Host, "port", a.Port)
	
	req, err := http.NewRequest("GET", a.Scheme+"://"+a.Host+":"+strconv.Itoa(a.Port)+string(a.Endpoint), nil)
	if err != nil {
		return nil, err
	}

	// Add the query parameters with a unique PID for this request
	pid := uuid.NewString()
	slog.Debug("Generated PID for stream", "pid", pid, "stream", aceId)
	
	if extraParams == nil {
		extraParams = req.URL.Query()
	}
	idType, id := aceId.ID()
	extraParams.Set(string(idType), id)
	extraParams.Set("format", "json")
	extraParams.Set("pid", pid)
	
	req.Header.Set("Content-Type", "application/json")
	req.URL.RawQuery = extraParams.Encode()

	slog.Debug("Request URL", "url", req.URL.String())
	client := &http.Client{
		Timeout: a.NoResponseTimeout,
	}
	res, err := client.Do(req)
	if err != nil {
		slog.Debug("Error getting stream", "error", err)
		return nil, err
	}
	defer res.Body.Close()

	// Read the response
	body, err := io.ReadAll(res.Body)
	if err != nil {
		slog.Debug("Error reading stream response", "error", err)
		return nil, err
	}

	slog.Debug("Stream response", "response", string(body))
	var response AceStreamMiddleware
	if err := json.Unmarshal(body, &response); err != nil {
		slog.Debug("Error unmarshalling stream response", "error", err)
		return nil, err
	}

	if response.Error != "" {
		slog.Debug("Error in stream response", "error", response.Error)
		return nil, errors.New(response.Error)
	}
	return &response, nil
}

// CloseStream closes a stream by sending a stop command to the AceStream backend.
func CloseStream(stream *AceStream) error {
	req, err := http.NewRequest("GET", stream.CommandURL, nil)
	if err != nil {
		return err
	}

	q := req.URL.Query()
	q.Add("method", "stop")
	req.URL.RawQuery = q.Encode()

	client := &http.Client{
		Timeout: 10 * time.Second,
	}
	res, err := client.Do(req)
	if err != nil {
		return err
	}
	defer res.Body.Close()

	body, err := io.ReadAll(res.Body)
	if err != nil {
		slog.Debug("Error reading stream response", "error", err)
		return err
	}

	var response AceStreamCommand
	if err := json.Unmarshal(body, &response); err != nil {
		slog.Debug("Error unmarshalling stream response", "error", err)
		return err
	}

	if response.Error != "" {
		slog.Debug("Error in stream response", "error", response.Error)
		return errors.New(response.Error)
	}
	return nil
}

// GetStatus returns the simplified status of the proxy.
func (a *Acexy) GetStatus(id *AceID) (AcexyStatus, error) {
	// In the stateless model, we don't track active streams
	return AcexyStatus{}, nil
}

// SetTimeout creates a timeout channel that will be closed after the given timeout
func SetTimeout(timeout time.Duration) chan struct{} {
	timeoutChan := make(chan struct{})
	go func() {
		time.Sleep(timeout)
		close(timeoutChan)
	}()
	return timeoutChan
}
