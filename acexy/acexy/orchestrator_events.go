package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"time"
)

type orchClient struct {
	base string
	key  string
	hc   *http.Client
	// opcional si el proxy conoce el contenedor
	containerID string
	// Maximum streams per engine
	maxStreamsPerEngine int
}

func newOrchClient(base string) *orchClient {
	if base == "" {
		return nil
	}
	return &orchClient{
		base:                base,
		key:                 os.Getenv("ACEXY_ORCH_APIKEY"),
		containerID:         os.Getenv("ACEXY_CONTAINER_ID"),
		maxStreamsPerEngine: 1, // Default value, will be set from main
		hc:                  &http.Client{Timeout: 3 * time.Second},
	}
}

// SetMaxStreamsPerEngine sets the maximum streams per engine configuration
func (c *orchClient) SetMaxStreamsPerEngine(max int) {
	if c != nil && max > 0 {
		c.maxStreamsPerEngine = max
	}
}

type startedEvent struct {
	ContainerID string `json:"container_id,omitempty"`
	Engine      struct {
		Host string `json:"host"`
		Port int    `json:"port"`
	} `json:"engine"`
	Stream struct {
		KeyType string `json:"key_type"`
		Key     string `json:"key"`
	} `json:"stream"`
	Session struct {
		PlaybackSessionID string `json:"playback_session_id"`
		StatURL           string `json:"stat_url"`
		CommandURL        string `json:"command_url"`
		IsLive            int    `json:"is_live"`
	} `json:"session"`
	Labels map[string]string `json:"labels,omitempty"`
}

type endedEvent struct {
	ContainerID string `json:"container_id,omitempty"`
	StreamID    string `json:"stream_id,omitempty"`
	Reason      string `json:"reason,omitempty"`
}

// New types for engine selection and orchestrator API
type engineState struct {
	ContainerID       string            `json:"container_id"`
	ContainerName     string            `json:"container_name,omitempty"`
	Host              string            `json:"host"`
	Port              int               `json:"port"`
	Labels            map[string]string `json:"labels"`
	FirstSeen         time.Time         `json:"first_seen"`
	LastSeen          time.Time         `json:"last_seen"`
	HealthStatus      string            `json:"health_status"`
	LastHealthCheck   time.Time         `json:"last_health_check"`
	LastStreamUsage   time.Time         `json:"last_stream_usage"`
	Streams           []string          `json:"streams"`
}

type streamState struct {
	ID                  string    `json:"id"`
	KeyType             string    `json:"key_type"`
	Key                 string    `json:"key"`
	ContainerID         string    `json:"container_id"`
	PlaybackSessionID   string    `json:"playback_session_id"`
	StatURL             string    `json:"stat_url"`
	CommandURL          string    `json:"command_url"`
	IsLive              bool      `json:"is_live"`
	StartedAt           time.Time `json:"started_at"`
	EndedAt             *time.Time `json:"ended_at,omitempty"`
	Status              string    `json:"status"`
}

type aceProvisionRequest struct {
	Image    string            `json:"image,omitempty"`
	Labels   map[string]string `json:"labels"`
	Env      map[string]string `json:"env"`
	HostPort *int              `json:"host_port,omitempty"`
}

type aceProvisionResponse struct {
	ContainerID         string `json:"container_id"`
	ContainerName       string `json:"container_name"`
	HostHTTPPort        int    `json:"host_http_port"`
	ContainerHTTPPort   int    `json:"container_http_port"`
	ContainerHTTPSPort  int    `json:"container_https_port"`
}

func (c *orchClient) post(path string, body any) {
	if c == nil {
		return
	}
	b, err := json.Marshal(body)
	if err != nil {
		slog.Warn("Failed to marshal orchestrator event", "error", err, "path", path)
		return
	}

	req, err := http.NewRequest(http.MethodPost, c.base+path, bytes.NewReader(b))
	if err != nil {
		slog.Warn("Failed to create orchestrator request", "error", err, "path", path)
		return
	}

	req.Header.Set("Content-Type", "application/json")
	if c.key != "" {
		req.Header.Set("Authorization", "Bearer "+c.key)
	}

	go func() {
		slog.Debug("Sending event to orchestrator", "url", c.base+path)
		resp, err := c.hc.Do(req)
		if err != nil {
			slog.Warn("Failed to send event to orchestrator", "error", err, "url", c.base+path)
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode < 200 || resp.StatusCode >= 300 {
			slog.Warn("Orchestrator returned error status", "status", resp.StatusCode, "url", c.base+path)
		} else {
			slog.Debug("Successfully sent event to orchestrator", "status", resp.StatusCode, "url", c.base+path)
		}
	}()
}

func (c *orchClient) EmitStarted(host string, port int, keyType, key, playbackID, statURL, cmdURL, streamID string) {
	if c == nil {
		return
	}

	ev := startedEvent{ContainerID: c.containerID}
	ev.Engine.Host, ev.Engine.Port = host, port
	ev.Stream.KeyType, ev.Stream.Key = keyType, key
	ev.Session.PlaybackSessionID = playbackID
	ev.Session.StatURL, ev.Session.CommandURL = statURL, cmdURL
	ev.Session.IsLive = 1
	ev.Labels = map[string]string{"stream_id": streamID}

	// Add debug logging for orchestrator integration
	slog.Debug("Emitting stream_started event to orchestrator",
		"stream_id", streamID, "key_type", keyType, "key", key,
		"host", host, "port", port, "playback_id", playbackID)

	c.post("/events/stream_started", ev)
}

func (c *orchClient) EmitEnded(streamID, reason string) {
	if c == nil {
		return
	}

	ev := endedEvent{ContainerID: c.containerID, StreamID: streamID, Reason: reason}

	// Add debug logging for orchestrator integration
	slog.Debug("Emitting stream_ended event to orchestrator",
		"stream_id", streamID, "reason", reason, "container_id", c.containerID)

	c.post("/events/stream_ended", ev)
}

// GetEngines retrieves all available engines from the orchestrator
func (c *orchClient) GetEngines() ([]engineState, error) {
	if c == nil {
		return nil, fmt.Errorf("orchestrator client not configured")
	}

	req, err := http.NewRequest(http.MethodGet, c.base+"/engines", nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	if c.key != "" {
		req.Header.Set("Authorization", "Bearer "+c.key)
	}

	resp, err := c.hc.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to get engines: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("orchestrator returned status %d", resp.StatusCode)
	}

	var engines []engineState
	if err := json.NewDecoder(resp.Body).Decode(&engines); err != nil {
		return nil, fmt.Errorf("failed to decode engines response: %w", err)
	}

	return engines, nil
}

// GetEngineStreams retrieves streams for a specific engine
func (c *orchClient) GetEngineStreams(containerID string) ([]streamState, error) {
	if c == nil {
		return nil, fmt.Errorf("orchestrator client not configured")
	}

	req, err := http.NewRequest(http.MethodGet, c.base+"/streams?container_id="+containerID+"&status=started", nil)
	if err != nil {
		return nil, fmt.Errorf("failed to create request: %w", err)
	}

	if c.key != "" {
		req.Header.Set("Authorization", "Bearer "+c.key)
	}

	resp, err := c.hc.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to get streams: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("orchestrator returned status %d", resp.StatusCode)
	}

	var streams []streamState
	if err := json.NewDecoder(resp.Body).Decode(&streams); err != nil {
		return nil, fmt.Errorf("failed to decode streams response: %w", err)
	}

	return streams, nil
}

// ProvisionAcestream provisions a new acestream engine
func (c *orchClient) ProvisionAcestream() (*aceProvisionResponse, error) {
	if c == nil {
		return nil, fmt.Errorf("orchestrator client not configured")
	}

	reqData := aceProvisionRequest{
		Labels: map[string]string{},
		Env:    map[string]string{},
	}

	body, err := json.Marshal(reqData)
	if err != nil {
		return nil, fmt.Errorf("failed to marshal provision request: %w", err)
	}

	req, err := http.NewRequest(http.MethodPost, c.base+"/provision/acestream", bytes.NewReader(body))
	if err != nil {
		return nil, fmt.Errorf("failed to create provision request: %w", err)
	}

	req.Header.Set("Content-Type", "application/json")
	if c.key != "" {
		req.Header.Set("Authorization", "Bearer "+c.key)
	}

	resp, err := c.hc.Do(req)
	if err != nil {
		return nil, fmt.Errorf("failed to provision acestream: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("orchestrator returned status %d", resp.StatusCode)
	}

	var provResp aceProvisionResponse
	if err := json.NewDecoder(resp.Body).Decode(&provResp); err != nil {
		return nil, fmt.Errorf("failed to decode provision response: %w", err)
	}

	return &provResp, nil
}

// SelectBestEngine selects the best available engine based on load balancing rules
// Returns host, port, and error. Prioritizes healthy engines first, then among engines with the same 
// health status and stream count, chooses the one with the oldest last_stream_usage timestamp.
func (c *orchClient) SelectBestEngine() (string, int, error) {
	if c == nil {
		return "", 0, fmt.Errorf("orchestrator client not configured")
	}

	// Get all available engines
	engines, err := c.GetEngines()
	if err != nil {
		return "", 0, fmt.Errorf("failed to get engines: %w", err)
	}

	slog.Debug("Found engines from orchestrator", "count", len(engines), "max_streams_per_engine", c.maxStreamsPerEngine)

	// Collect engines with their stream counts for prioritization
	type engineWithLoad struct {
		engine       engineState
		activeStreams int
	}
	
	var availableEngines []engineWithLoad

	// Check stream count for each engine
	for _, engine := range engines {
		streams, err := c.GetEngineStreams(engine.ContainerID)
		if err != nil {
			slog.Warn("Failed to get streams for engine", "container_id", engine.ContainerID, "error", err)
			continue
		}

		activeStreams := 0
		for _, stream := range streams {
			if stream.Status == "started" {
				activeStreams++
			}
		}

		slog.Debug("Engine stream count", "container_id", engine.ContainerID, "active_streams", activeStreams, "host", engine.Host, "port", engine.Port, "max_allowed", c.maxStreamsPerEngine, "health_status", engine.HealthStatus, "last_health_check", engine.LastHealthCheck.Format(time.RFC3339), "last_stream_usage", engine.LastStreamUsage.Format(time.RFC3339))

		// Only consider engines that have capacity
		if activeStreams < c.maxStreamsPerEngine {
			availableEngines = append(availableEngines, engineWithLoad{
				engine:        engine,
				activeStreams: activeStreams,
			})
		}
	}

	// If no engines have capacity, provision a new one
	if len(availableEngines) == 0 {
		slog.Info("No available engines found (all at capacity), provisioning new acestream engine")
		provResp, err := c.ProvisionAcestream()
		if err != nil {
			return "", 0, fmt.Errorf("failed to provision new engine: %w", err)
		}

		// Wait a moment for the engine to be ready (it should be according to provisioner)
		time.Sleep(10 * time.Second)

		slog.Info("Provisioned new engine", "container_id", provResp.ContainerID, "container_name", provResp.ContainerName, "host_port", provResp.HostHTTPPort, "container_port", provResp.ContainerHTTPPort)
		
		// Use orchestrator-provided host port mapping directly
		return "localhost", provResp.HostHTTPPort, nil
	}

	// Sort engines by health status first (healthy engines prioritized),
	// then by stream count (ascending), then by last_stream_usage (ascending - oldest first)
	for i := 0; i < len(availableEngines); i++ {
		for j := i + 1; j < len(availableEngines); j++ {
			iEngine := availableEngines[i]
			jEngine := availableEngines[j]
			
			// Primary sort: by health status (healthy engines first)
			iHealthy := iEngine.engine.HealthStatus == "healthy"
			jHealthy := jEngine.engine.HealthStatus == "healthy"
			
			if iHealthy != jHealthy {
				// If one is healthy and other is not, prioritize healthy
				if jHealthy && !iHealthy {
					availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
				}
			} else {
				// Both have same health status, sort by active stream count
				if iEngine.activeStreams > jEngine.activeStreams {
					availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
				} else if iEngine.activeStreams == jEngine.activeStreams {
					// Same health and stream count, sort by last_stream_usage (ascending - oldest first)
					// This ensures that among engines with same health and stream count, we pick the one unused the longest
					if iEngine.engine.LastStreamUsage.After(jEngine.engine.LastStreamUsage) {
						availableEngines[i], availableEngines[j] = availableEngines[j], availableEngines[i]
					}
				}
			}
		}
	}

	// Select the engine with the least active streams (empty engines are prioritized)
	bestEngine := availableEngines[0]
	host := bestEngine.engine.Host
	port := bestEngine.engine.Port

	slog.Info("Selected best available engine", 
		"container_id", bestEngine.engine.ContainerID, 
		"container_name", bestEngine.engine.ContainerName, 
		"host", host, 
		"port", port, 
		"active_streams", bestEngine.activeStreams,
		"max_streams", c.maxStreamsPerEngine,
		"health_status", bestEngine.engine.HealthStatus,
		"last_health_check", bestEngine.engine.LastHealthCheck.Format(time.RFC3339),
		"last_stream_usage", bestEngine.engine.LastStreamUsage.Format(time.RFC3339))
	
	return host, port, nil
}
