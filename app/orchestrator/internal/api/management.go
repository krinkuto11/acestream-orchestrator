package api

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/acestream/acestream/internal/config"
	cpdocker "github.com/acestream/acestream/internal/controlplane/docker"
	cpengine "github.com/acestream/acestream/internal/controlplane/engine"
	"github.com/acestream/acestream/internal/state"
)

const maxJSONBodyBytes = 10 << 20 // 10 MiB

func (s *ProxyServer) registerManagementRoutes() {
		// ── Events (proxy-plane POST) ─────────────────────────────────────────────
	s.mux.HandleFunc("POST /api/v1/events/stream_started", s.mgHandleEventStreamStarted)
	s.mux.HandleFunc("POST /api/v1/events/stream_ended", s.mgHandleEventStreamEnded)

	// ── Engines ───────────────────────────────────────────────────────────────
	s.mux.HandleFunc("GET /api/v1/engines", s.mgHandleListEngines)
	s.mux.HandleFunc("GET /api/v1/engines/with-metrics", s.mgHandleEnginesWithMetrics)
	s.mux.HandleFunc("GET /api/v1/engines/stats/all", s.mgHandleAllEngineStats)
	s.mux.HandleFunc("GET /api/v1/engines/stats/total", s.mgHandleEngineStatsTotal)
	s.mux.HandleFunc("GET /api/v1/engines/stats/{id}", s.mgHandleEngineStatsSingle)
	s.mux.HandleFunc("GET /api/v1/engines/{id}", s.mgHandleGetEngine)
	s.mux.HandleFunc("DELETE /api/v1/engines/{id}", requireAPIKey(s.mgHandleDeleteEngine))

	// ── Containers ────────────────────────────────────────────────────────────
	s.mux.HandleFunc("GET /api/v1/containers/{id}", s.mgHandleContainerInspect)
	s.mux.HandleFunc("DELETE /api/v1/containers/{id}", requireAPIKey(s.mgHandleDeleteContainer))
	s.mux.HandleFunc("GET /api/v1/containers/{id}/logs", requireAPIKey(s.mgHandleContainerLogs))

	// ── Streams ───────────────────────────────────────────────────────────────
	s.mux.HandleFunc("GET /api/v1/streams", s.mgHandleListStreams)
	s.mux.HandleFunc("DELETE /api/v1/streams/{id}", requireAPIKey(s.mgHandleDeleteStream))
	s.mux.HandleFunc("POST /api/v1/streams/batch-stop", requireAPIKey(s.mgHandleBatchStopStreams))
	s.mux.HandleFunc("GET /api/v1/streams/{id}/stats", s.mgHandleStreamStats)
	s.mux.HandleFunc("GET /api/v1/streams/{id}/extended-stats", s.mgHandleStreamExtendedStats)
	s.mux.HandleFunc("GET /api/v1/streams/{id}/livepos", s.mgHandleStreamLivepos)

	// ── Provisioning ──────────────────────────────────────────────────────────
	s.mux.HandleFunc("POST /api/v1/provision", requireAPIKey(s.mgHandleProvision))
	s.mux.HandleFunc("POST /api/v1/provision/acestream", requireAPIKey(s.mgHandleProvisionAcestream))
	s.mux.HandleFunc("POST /api/v1/scale/{demand}", requireAPIKey(s.mgHandleScale))
	s.mux.HandleFunc("POST /api/v1/gc", requireAPIKey(s.mgHandleGC))
	s.mux.HandleFunc("POST /api/v1/reconcile", requireAPIKey(s.mgHandleReconcile))
	s.mux.HandleFunc("GET /api/v1/by-label", requireAPIKey(s.mgHandleByLabel))

	// ── VPN nodes ─────────────────────────────────────────────────────────────
	s.mux.HandleFunc("GET /api/v1/vpn-nodes", s.mgHandleListVPNNodes)
	s.mux.HandleFunc("POST /api/v1/vpn-nodes/provision", requireAPIKey(s.mgHandleProvisionVPNNode))
	s.mux.HandleFunc("POST /api/v1/vpn-nodes/{name}/drain", requireAPIKey(s.mgHandleDrainVPNNode))
	s.mux.HandleFunc("DELETE /api/v1/vpn-nodes/{name}", requireAPIKey(s.mgHandleDestroyVPNNode))
	s.mux.HandleFunc("GET /api/v1/vpn-credentials", s.mgHandleVPNCredentials)
	s.mux.HandleFunc("POST /api/v1/vpn-servers/refresh", requireAPIKey(s.mgHandleVPNServersRefresh))
	s.mux.HandleFunc("POST /api/v1/vpn/servers/refresh", requireAPIKey(s.mgHandleVPNServersRefresh))

	// ── VPN settings & status ─────────────────────────────────────────────────
	s.mux.HandleFunc("GET /api/v1/vpn/publicip", s.mgHandleVPNPublicIP)
	s.mux.HandleFunc("GET /api/v1/vpn/status", s.mgHandleVPNStatus)
	s.mux.HandleFunc("GET /api/v1/vpn/leases", s.mgHandleVPNCredentials)
	s.mux.HandleFunc("GET /api/v1/vpn/config", requireAPIKey(s.mgHandleGetVPNConfig))
	s.mux.HandleFunc("POST /api/v1/vpn/config", requireAPIKey(s.mgHandleSetSettingsCategory("vpn_settings")))
	s.mux.HandleFunc("GET /api/v1/settings/vpn", requireAPIKey(s.mgHandleGetVPNConfig))
	s.mux.HandleFunc("POST /api/v1/settings/vpn", requireAPIKey(s.mgHandleSetSettingsCategory("vpn_settings")))
	s.mux.HandleFunc("POST /api/v1/vpn/parse-wireguard", requireAPIKey(s.mgHandleParseWireGuard))
	s.mux.HandleFunc("POST /api/v1/vpn/proton/refresh", requireAPIKey(s.mgHandleProtonRefresh))
	s.mux.HandleFunc("GET /api/v1/vpn/servers/refresh/status", s.mgHandleVPNServersRefreshStatus)

	// ── Settings ──────────────────────────────────────────────────────────────
	s.mux.HandleFunc("GET /api/v1/settings", requireAPIKey(s.mgHandleGetAllSettings))
	s.mux.HandleFunc("POST /api/v1/settings", requireAPIKey(s.mgHandleUpdateAllSettings))
	s.mux.HandleFunc("GET /api/v1/settings/export", requireAPIKey(s.mgHandleExportSettings))
	s.mux.HandleFunc("POST /api/v1/settings/import", requireAPIKey(s.mgHandleImportSettings))
	s.mux.HandleFunc("GET /api/v1/settings/engine/config", s.mgHandleGetSettingsCategory("engine_config"))
	s.mux.HandleFunc("POST /api/v1/settings/engine/config", requireAPIKey(s.mgHandleSetSettingsCategory("engine_config")))
	s.mux.HandleFunc("GET /api/v1/settings/orchestrator", s.mgHandleGetSettingsCategory("orchestrator_settings"))
	s.mux.HandleFunc("POST /api/v1/settings/orchestrator", requireAPIKey(s.mgHandleSetSettingsCategory("orchestrator_settings")))
	s.mux.HandleFunc("GET /api/v1/settings/engine", s.mgHandleGetSettingsCategory("engine_settings"))
	s.mux.HandleFunc("POST /api/v1/settings/engine", requireAPIKey(s.mgHandleSetSettingsCategory("engine_settings")))
	s.mux.HandleFunc("GET /api/v1/engine/config", s.mgHandleGetSettingsCategory("engine_config"))
	s.mux.HandleFunc("POST /api/v1/engine/config", requireAPIKey(s.mgHandleSetSettingsCategory("engine_config")))
	s.mux.HandleFunc("GET /api/v1/custom-variant/config", s.mgHandleGetSettingsCategory("custom_variant_config"))
	s.mux.HandleFunc("POST /api/v1/custom-variant/config", requireAPIKey(s.mgHandleSetSettingsCategory("custom_variant_config")))
	s.mux.HandleFunc("GET /api/v1/proxy/config", s.mgHandleGetSettingsCategory("proxy_settings"))
	s.mux.HandleFunc("POST /api/v1/proxy/config", requireAPIKey(s.mgHandleSetProxyConfig))
	s.mux.HandleFunc("POST /api/v1/settings/vpn/credentials", requireAPIKey(s.mgHandleAddVPNCredential))
	s.mux.HandleFunc("DELETE /api/v1/settings/vpn/credentials/{id}", requireAPIKey(s.mgHandleDeleteVPNCredential))

	// ── Custom variant reprovision ─────────────────────────────────────────────
	s.mux.HandleFunc("GET /api/v1/custom-variant/reprovision/status", s.mgHandleReprovisionStatus)
	s.mux.HandleFunc("POST /api/v1/custom-variant/reprovision", requireAPIKey(s.mgHandleReprovision))
	s.mux.HandleFunc("GET /api/v1/settings/engine/reprovision/status", s.mgHandleReprovisionStatus)
	s.mux.HandleFunc("POST /api/v1/settings/engine/reprovision", requireAPIKey(s.mgHandleReprovision))
	s.mux.HandleFunc("GET /api/v1/custom-variant/platform", s.mgHandlePlatform)

	// ── Observability ─────────────────────────────────────────────────────────
	s.mux.HandleFunc("GET /api/v1/health/status", s.mgHandleHealthStatus)
	s.mux.HandleFunc("POST /api/v1/health/circuit-breaker/reset", requireAPIKey(s.mgHandleCircuitBreakerReset))
	s.mux.HandleFunc("GET /api/v1/orchestrator/status", s.mgHandleOrchestratorStatus)
	s.mux.Handle("GET /api/v1/metrics", promhttp.Handler())
	s.mux.HandleFunc("GET /api/v1/metrics/dashboard", s.mgHandleMetricsDashboard)
	s.mux.HandleFunc("GET /api/v1/metrics/performance", s.mgHandleMetricsPerformance)
	s.mux.HandleFunc("GET /api/v1/events", s.mgHandleEvents)
	s.mux.HandleFunc("GET /api/v1/events/stats", s.mgHandleEventsStats)
	s.mux.HandleFunc("POST /api/v1/events/cleanup", requireAPIKey(s.mgHandleEventsCleanup))
	s.mux.HandleFunc("GET /api/v1/cache/stats", s.mgHandleCacheStats)
	s.mux.HandleFunc("POST /api/v1/cache/clear", requireAPIKey(s.mgHandleCacheClear))
	s.mux.HandleFunc("GET /api/v1/engine-cache/stats", s.mgHandleCacheStats)
	s.mux.HandleFunc("POST /api/v1/engine-cache/purge", requireAPIKey(s.mgHandleCacheClear))

	// ── Circuit breaker ───────────────────────────────────────────────────────
	s.mux.HandleFunc("GET /api/v1/circuit-breaker", s.mgHandleCircuitBreakerStatus)
	s.mux.HandleFunc("POST /api/v1/circuit-breaker/reset", requireAPIKey(s.mgHandleCircuitBreakerReset))

	// ── M3U ────────────────────────────────────────────────────────────────────
	s.mux.HandleFunc("GET /api/v1/modify_m3u", s.mgHandleModifyM3U)

	// ── Debug ──────────────────────────────────────────────────────────────────
	s.mux.HandleFunc("GET /api/v1/version", s.mgHandleVersion)
	s.mux.HandleFunc("GET /api/v1/auth/status", s.mgHandleAuthStatus)

	// ── SSE streams ───────────────────────────────────────────────────────────
	s.registerSSERoutes()

	// ── Static files (React panel + favicons) ─────────────────────────────────
	s.registerStaticRoutes()
}

// ─── Engines ─────────────────────────────────────────────────────────────────

func (s *ProxyServer) mgHandleListEngines(w http.ResponseWriter, r *http.Request) {
	engines := s.st.ListEngines()
	streamCounts := s.st.GetStreamCounts()
	monCounts := s.st.GetMonitorCounts()

	type engineOut struct {
		ContainerID        string            `json:"container_id"`
		ContainerName      string            `json:"container_name"`
		Host               string            `json:"host"`
		Port               int               `json:"port"`
		APIPort            int               `json:"api_port"`
		Labels             map[string]string `json:"labels"`
		Forwarded          bool              `json:"forwarded"`
		VPNContainer       string            `json:"vpn_container,omitempty"`
		HealthStatus       string            `json:"health_status"`
		P2PPort            int               `json:"p2p_port,omitempty"`
		FirstSeen          time.Time         `json:"first_seen"`
		LastSeen           time.Time         `json:"last_seen"`
		Draining           bool              `json:"draining"`
		DrainReason        string            `json:"drain_reason,omitempty"`
		StreamCount        int               `json:"stream_count"`
		MonitorCount       int               `json:"monitor_count"`
		TotalPeers         int               `json:"total_peers"`
		TotalSpeedDown     int               `json:"total_speed_down"`
		TotalSpeedUp       int               `json:"total_speed_up"`
		MonitorStreamCount int               `json:"monitor_stream_count"`
		MonitorSpeedDown   int               `json:"monitor_speed_down"`
		MonitorSpeedUp     int               `json:"monitor_speed_up"`
		LastHealthCheck    *time.Time        `json:"last_health_check,omitempty"`
		LastStreamUsage    *time.Time        `json:"last_stream_usage,omitempty"`
		EngineVariant      string            `json:"engine_variant,omitempty"`
		Platform           string            `json:"platform,omitempty"`
		Version            string            `json:"version,omitempty"`
		ForwardedPort      *int              `json:"forwarded_port,omitempty"`
		CPUPercent         float64           `json:"cpu_percent"`
		MemoryUsage        int64             `json:"memory_usage"`
		MemoryPercent      float64           `json:"memory_percent"`
		Streams            []string          `json:"streams"`
	}

	out := make([]engineOut, 0, len(engines))
	for _, e := range engines {
		labels := e.Labels
		if labels == nil {
			labels = map[string]string{}
		}
		streams := e.Streams
		if streams == nil {
			streams = []string{}
		}
		out = append(out, engineOut{
			ContainerID:        e.ContainerID,
			ContainerName:      e.ContainerName,
			Host:               e.Host,
			Port:               e.Port,
			APIPort:            e.APIPort,
			Labels:             labels,
			Forwarded:          e.Forwarded,
			VPNContainer:       e.VPNContainer,
			HealthStatus:       string(e.HealthStatus),
			P2PPort:            e.P2PPort,
			FirstSeen:          e.FirstSeen,
			LastSeen:           e.LastSeen,
			Draining:           e.Draining,
			DrainReason:        e.DrainReason,
			StreamCount:        streamCounts[e.ContainerID],
			MonitorCount:       monCounts[e.ContainerID],
			TotalPeers:         e.TotalPeers,
			TotalSpeedDown:     e.TotalSpeedDown,
			TotalSpeedUp:       e.TotalSpeedUp,
			MonitorStreamCount: e.MonitorStreamCount,
			MonitorSpeedDown:   e.MonitorSpeedDown,
			MonitorSpeedUp:     e.MonitorSpeedUp,
			LastHealthCheck:    e.LastHealthCheck,
			LastStreamUsage:    e.LastStreamUsage,
			EngineVariant:      e.EngineVariant,
			Platform:           e.Platform,
			Version:            e.Version,
			ForwardedPort:      e.ForwardedPort,
			CPUPercent:         e.CPUPercent,
			MemoryUsage:        e.MemoryUsage,
			MemoryPercent:      e.MemoryPercent,
			Streams:            streams,
		})
	}
	mgWriteJSON(w, http.StatusOK, out)
}

func (s *ProxyServer) mgHandleEnginesWithMetrics(w http.ResponseWriter, r *http.Request) {
	s.mgHandleListEngines(w, r)
}

func (s *ProxyServer) mgHandleGetEngine(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	e, ok := s.st.GetEngine(id)
	if !ok {
		mgWriteJSON(w, http.StatusNotFound, map[string]string{"error": "engine not found"})
		return
	}
	mgWriteJSON(w, http.StatusOK, e)
}

func (s *ProxyServer) mgHandleDeleteEngine(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if _, ok := s.st.GetEngine(id); !ok {
		mgWriteJSON(w, http.StatusNotFound, map[string]string{"error": "engine not found"})
		return
	}
	s.st.MarkEngineDraining(id, "manual deletion")
	go func() {
		if err := cpengine.StopContainer(context.Background(), id, true); err != nil {
			slog.Warn("stop container error during manual delete", "id", id, "err", err)
		}
	}()
	mgWriteJSON(w, http.StatusAccepted, map[string]string{"status": "deleting"})
}

func (s *ProxyServer) mgHandleAllEngineStats(w http.ResponseWriter, r *http.Request) {
	engines := s.st.ListEngines()
	ids := make([]string, 0, len(engines))
	for _, e := range engines {
		ids = append(ids, e.ContainerID)
	}
	stats := cpdocker.GetAllContainerStats(r.Context(), ids)
	mgWriteJSON(w, http.StatusOK, stats)
}

func (s *ProxyServer) mgHandleEngineStatsTotal(w http.ResponseWriter, r *http.Request) {
	engines := s.st.ListEngines()
	streams := s.st.ListStreams()
	vpns := s.st.ListVPNNodes()
	var healthy, draining int
	for _, e := range engines {
		if e.HealthStatus == state.HealthHealthy {
			healthy++
		}
		if e.Draining {
			draining++
		}
	}
	mgWriteJSON(w, http.StatusOK, map[string]any{
		"engines_total":    len(engines),
		"engines_healthy":  healthy,
		"engines_draining": draining,
		"streams_active":   len(streams),
		"vpn_nodes_total":  len(vpns),
	})
}

func (s *ProxyServer) mgHandleEngineStatsSingle(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	st, err := cpdocker.GetContainerStats(r.Context(), id)
	if err != nil {
		mgWriteJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	mgWriteJSON(w, http.StatusOK, st)
}

// ─── Containers ──────────────────────────────────────────────────────────────

func (s *ProxyServer) mgHandleContainerInspect(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	cli, err := cpengine.NewDockerClientExported()
	if err != nil {
		mgWriteJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	defer cli.Close()
	info, err := cli.ContainerInspect(r.Context(), id)
	if err != nil {
		mgWriteJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	mgWriteJSON(w, http.StatusOK, info)
}

func (s *ProxyServer) mgHandleDeleteContainer(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	s.st.MarkEngineDraining(id, "manual container deletion")
	go func() {
		if err := cpengine.StopContainer(context.Background(), id, true); err != nil {
			slog.Warn("stop container error during manual container delete", "id", id, "err", err)
		}
	}()
	mgWriteJSON(w, http.StatusAccepted, map[string]string{"status": "deleting"})
}

func (s *ProxyServer) mgHandleContainerLogs(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	tail := r.URL.Query().Get("tail")
	timestamps := r.URL.Query().Get("timestamps") == "true"
	var sinceUnix int64
	if ss := r.URL.Query().Get("since_seconds"); ss != "" {
		if n, err := strconv.Atoi(ss); err == nil && n > 0 {
			sinceUnix = time.Now().Unix() - int64(n)
		}
	}
	lines, err := cpdocker.GetContainerLogs(r.Context(), id, tail, timestamps, sinceUnix)
	if err != nil {
		mgWriteJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	mgWriteJSON(w, http.StatusOK, map[string]any{
		"logs":         strings.Join(lines, "\n"),
		"container_id": id,
		"fetched_at":   time.Now().UTC(),
	})
}

// ─── Streams ─────────────────────────────────────────────────────────────────

func (s *ProxyServer) mgHandleListStreams(w http.ResponseWriter, r *http.Request) {
	streams := s.st.ListStreams()
	mgWriteJSON(w, http.StatusOK, streams)
}

func (s *ProxyServer) mgHandleDeleteStream(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	st, ok := s.st.GetStream(id)
	if !ok {
		mgWriteJSON(w, http.StatusNotFound, map[string]string{"error": "stream not found"})
		return
	}
	if st.CommandURL != "" {
		stopURL := st.CommandURL + "?method=stop"
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		defer cancel()
		if req, err := http.NewRequestWithContext(ctx, http.MethodGet, stopURL, nil); err == nil {
			resp, err := http.DefaultClient.Do(req)
			if err != nil {
				slog.Warn("stop command failed for stream", "id", id, "err", err)
			} else {
				resp.Body.Close()
			}
		}
	}
	s.st.OnStreamEnded(state.StreamEndedEvent{ContentID: id, Reason: "manual_stop_via_api"})
	mgWriteJSON(w, http.StatusOK, map[string]string{"message": "Stream stopped successfully", "stream_id": id})
}

func (s *ProxyServer) mgHandleBatchStopStreams(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxJSONBodyBytes)
	var commandURLs []string
	if err := json.NewDecoder(r.Body).Decode(&commandURLs); err != nil {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json: expected array of command_urls"})
		return
	}
	type result struct {
		CommandURL string `json:"command_url"`
		Success    bool   `json:"success"`
		Message    string `json:"message"`
		StreamID   string `json:"stream_id,omitempty"`
	}
	results := make([]result, 0, len(commandURLs))
	successCount := 0
	for _, cmdURL := range commandURLs {
		res := result{CommandURL: cmdURL}
		var found *state.StreamState
		for _, s := range s.st.ListStreams() {
			if s.CommandURL == cmdURL {
				found = s
				break
			}
		}
		if found == nil {
			res.Message = "Stream not found"
			results = append(results, res)
			continue
		}
		res.StreamID = found.ID
		if found.Status != "started" {
			res.Message = fmt.Sprintf("Stream is not active (status: %s)", found.Status)
			results = append(results, res)
			continue
		}
		stopURL := cmdURL + "?method=stop"
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		if req, err := http.NewRequestWithContext(ctx, http.MethodGet, stopURL, nil); err == nil {
			if resp, err := http.DefaultClient.Do(req); err != nil {
				slog.Warn("batch stop command failed", "url", cmdURL, "err", err)
			} else {
				resp.Body.Close()
			}
		}
		cancel()
		s.st.OnStreamEnded(state.StreamEndedEvent{
			ContentID:   found.ID,
			ContainerID: found.ContainerID,
			Reason:      "batch_stop_via_api",
		})
		res.Success = true
		res.Message = "Stream stopped successfully"
		successCount++
		results = append(results, res)
	}
	mgWriteJSON(w, http.StatusOK, map[string]any{
		"total":         len(commandURLs),
		"success_count": successCount,
		"failure_count": len(commandURLs) - successCount,
		"results":       results,
	})
}

func (s *ProxyServer) mgHandleStreamStats(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if _, ok := s.st.GetStream(id); !ok {
		mgWriteJSON(w, http.StatusNotFound, map[string]string{"error": "stream not found"})
		return
	}
	snaps := s.st.GetStats(id)
	if snaps == nil {
		snaps = []*state.StatSnapshot{}
	}
	mgWriteJSON(w, http.StatusOK, snaps)
}

func (s *ProxyServer) mgHandleStreamExtendedStats(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	st, ok := s.st.GetStream(id)
	if !ok {
		mgWriteJSON(w, http.StatusNotFound, map[string]string{"error": "stream not found"})
		return
	}
	mgWriteJSON(w, http.StatusOK, map[string]any{
		"available":      true,
		"stream_id":      id,
		"peers":          st.Peers,
		"speed_down":     st.SpeedDown,
		"speed_up":       st.SpeedUp,
		"downloaded":     st.Downloaded,
		"uploaded":       st.Uploaded,
		"bitrate":        st.Bitrate,
		"livepos":        st.Livepos,
		"active_clients": st.ActiveClients,
		"status":         st.Status,
	})
}

func (s *ProxyServer) mgHandleStreamLivepos(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	st, ok := s.st.GetStream(id)
	if !ok {
		mgWriteJSON(w, http.StatusNotFound, map[string]string{"error": "stream not found"})
		return
	}
	mgWriteJSON(w, http.StatusOK, map[string]any{"content_id": id, "livepos": st.Livepos})
}

// ─── Provisioning ────────────────────────────────────────────────────────────

func (s *ProxyServer) mgHandleProvision(w http.ResponseWriter, r *http.Request) {
	if s.ctrl == nil {
		mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "controller not available"})
		return
	}
	s.ctrl.EnsureMinimum()
	mgWriteJSON(w, http.StatusOK, map[string]string{"status": "reconciling"})
}

func (s *ProxyServer) mgHandleProvisionAcestream(w http.ResponseWriter, r *http.Request) {
	if s.ctrl == nil {
		mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "controller not available"})
		return
	}
	current := s.st.GetDesiredReplicas()
	s.ctrl.ScaleTo(current + 1)
	mgWriteJSON(w, http.StatusOK, map[string]string{"status": "provisioning"})
}

func (s *ProxyServer) mgHandleScale(w http.ResponseWriter, r *http.Request) {
	demand := r.PathValue("demand")
	var desired int
	if _, err := fmt.Sscanf(demand, "%d", &desired); err != nil {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid demand"})
		return
	}
	if s.ctrl == nil {
		mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "controller not available"})
		return
	}
	s.ctrl.ScaleTo(desired)
	mgWriteJSON(w, http.StatusOK, map[string]any{"desired": desired, "status": "scaling"})
}

func (s *ProxyServer) mgHandleGC(w http.ResponseWriter, r *http.Request) {
	if s.ctrl != nil {
		s.ctrl.Nudge("manual gc")
	}
	mgWriteJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *ProxyServer) mgHandleReconcile(w http.ResponseWriter, r *http.Request) {
	if s.ctrl != nil {
		s.ctrl.Nudge("manual reconcile")
	}
	mgWriteJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *ProxyServer) mgHandleByLabel(w http.ResponseWriter, r *http.Request) {
	// Python uses `key` param, not `label`
	key := r.URL.Query().Get("key")
	if key == "" {
		key = r.URL.Query().Get("label")
	}
	value := r.URL.Query().Get("value")
	engines := s.st.ListEngines()
	if key == "" {
		mgWriteJSON(w, http.StatusOK, engines)
		return
	}
	var matched []*state.Engine
	for _, e := range engines {
		if v, ok := e.Labels[key]; ok && (value == "" || v == value) {
			matched = append(matched, e)
		}
	}
	if matched == nil {
		matched = []*state.Engine{}
	}
	mgWriteJSON(w, http.StatusOK, map[string]any{"engines": matched, "key": key, "value": value})
}

// ─── VPN nodes ───────────────────────────────────────────────────────────────

func (s *ProxyServer) mgHandleListVPNNodes(w http.ResponseWriter, r *http.Request) {
	mgWriteJSON(w, http.StatusOK, s.st.ListVPNNodes())
}

func (s *ProxyServer) mgHandleProvisionVPNNode(w http.ResponseWriter, r *http.Request) {
	if s.prov == nil {
		mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "VPN provisioner not available"})
		return
	}
	result, err := s.prov.ProvisionNode(r.Context())
	if err != nil {
		mgWriteJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	mgWriteJSON(w, http.StatusCreated, result)
}

func (s *ProxyServer) mgHandleDrainVPNNode(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	if _, ok := s.st.GetVPNNode(name); !ok {
		mgWriteJSON(w, http.StatusNotFound, map[string]string{"error": "VPN node not found"})
		return
	}
	s.st.SetVPNNodeDraining(name)
	if s.pub != nil {
		if node, ok := s.st.GetVPNNode(name); ok {
			s.pub.PublishVPNNode(r.Context(), node)
		}
	}
	mgWriteJSON(w, http.StatusOK, map[string]string{"status": "draining"})
}

func (s *ProxyServer) mgHandleDestroyVPNNode(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	if s.prov == nil {
		mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "VPN provisioner not available"})
		return
	}
	s.st.SetVPNNodeDraining(name)
	go func() {
		if err := s.prov.DestroyNode(context.Background(), name); err != nil {
			slog.Warn("failed to destroy VPN node asynchronously", "name", name, "err", err)
		}
	}()
	mgWriteJSON(w, http.StatusAccepted, map[string]string{"status": "destroying"})
}

func (s *ProxyServer) mgHandleVPNCredentials(w http.ResponseWriter, r *http.Request) {
	if s.creds == nil {
		mgWriteJSON(w, http.StatusOK, map[string]any{"total": 0, "available": 0, "leased": 0})
		return
	}
	mgWriteJSON(w, http.StatusOK, s.creds.Summary())
}

func (s *ProxyServer) mgHandleVPNServersRefresh(w http.ResponseWriter, r *http.Request) {
	if s.svcRefresh == nil {
		mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "servers refresh service not available"})
		return
	}
	go func() {
		// Use a background context because this goroutine outlives the HTTP request.
		if err := s.svcRefresh.RefreshOfficial(context.Background()); err != nil {
			slog.Warn("VPN servers manual refresh failed", "err", err)
		}
	}()
	mgWriteJSON(w, http.StatusAccepted, map[string]string{"status": "refresh started"})
}

// ─── VPN ─────────────────────────────────────────────────────────────────────

func (s *ProxyServer) mgHandleVPNStatus(w http.ResponseWriter, r *http.Request) {
	nodes := s.st.ListVPNNodes()
	healthy := 0
	for _, n := range nodes {
		if n.Healthy {
			healthy++
		}
	}
	mgWriteJSON(w, http.StatusOK, map[string]any{
		"vpn_enabled":   config.C.Load().VPNEnabled,
		"nodes_total":   len(nodes),
		"nodes_healthy": healthy,
		"vpn_nodes":     nodes,
	})
}

func (s *ProxyServer) mgHandleGetVPNConfig(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		mgWriteJSON(w, http.StatusOK, map[string]any{})
		return
	}
	mgWriteJSON(w, http.StatusOK, s.settings.Get("vpn_settings"))
}

func (s *ProxyServer) mgHandleSetVPNConfig(w http.ResponseWriter, r *http.Request) {
	s.mgHandleSetSettingsCategory("vpn_settings")(w, r)
}

func (s *ProxyServer) mgHandleParseWireGuard(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, maxJSONBodyBytes)
	var body struct {
		Config      string `json:"config"`
		FileContent string `json:"file_content"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	cfg := body.Config
	if cfg == "" {
		cfg = body.FileContent
	}
	result := parseWireGuardConfig(cfg)
	mgWriteJSON(w, http.StatusOK, result)
}

func (s *ProxyServer) mgHandleProtonRefresh(w http.ResponseWriter, r *http.Request) {
	protonURL := os.Getenv("PROTON_SERVICE_URL")
	if protonURL == "" {
		protonURL = "http://localhost:9099"
	}
	req, err := http.NewRequestWithContext(r.Context(), http.MethodPost, protonURL+"/refresh", r.Body)
	if err != nil {
		mgWriteJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		slog.Warn("proton refresh failed", "err", err)
		mgWriteJSON(w, http.StatusBadGateway, map[string]string{"error": "proton service unavailable: " + err.Error()})
		return
	}
	defer resp.Body.Close()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(resp.StatusCode)
	var payload any
	if err := json.NewDecoder(resp.Body).Decode(&payload); err == nil {
		json.NewEncoder(w).Encode(payload) //nolint:errcheck
	}
}

func (s *ProxyServer) mgHandleVPNServersRefreshStatus(w http.ResponseWriter, r *http.Request) {
	if s.svcRefresh == nil {
		mgWriteJSON(w, http.StatusOK, map[string]any{"in_progress": false})
		return
	}
	mgWriteJSON(w, http.StatusOK, s.svcRefresh.Status())
}

// ─── Settings ────────────────────────────────────────────────────────────────

var mgValidCategories = map[string]bool{
	"engine_config":         true,
	"engine_settings":       true,
	"orchestrator_settings": true,
	"proxy_settings":        true,
	"vpn_settings":          true,
	"custom_variant_config": true,
}

func (s *ProxyServer) mgHandleGetAllSettings(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		mgWriteJSON(w, http.StatusOK, map[string]any{})
		return
	}
	mgWriteJSON(w, http.StatusOK, s.settings.GetAll())
}

func (s *ProxyServer) mgHandleUpdateAllSettings(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "settings not available"})
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxJSONBodyBytes)
	var body map[string]map[string]any
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	applied := map[string]bool{}
	for cat, payload := range body {
		if !mgValidCategories[cat] {
			continue
		}
		if err := s.settings.Save(cat, payload); err != nil {
			applied[cat] = false
		} else {
			applied[cat] = true
			switch cat {
			case "proxy_settings":
				config.ApplySettings(payload)
			case "engine_settings":
				config.ApplyEngineSettings(payload)
				if s.ctrl != nil {
					s.ctrl.EnsureMinimum()
				}
			case "orchestrator_settings":
				config.ApplyOrchestratorSettings(payload)
				if s.ctrl != nil {
					s.ctrl.EnsureMinimum()
				}
			case "engine_config":
				config.ApplyEngineConfig(payload)
				go cpengine.PushEngineConfig(context.Background(), payload)
				if s.ctrl != nil {
					s.ctrl.EnsureMinimum()
				}
			case "vpn_settings":
				config.ApplyVPNSettings(payload)
				if s.creds != nil {
					if c, ok := payload["credentials"].([]any); ok {
						var mapped []map[string]any
						for _, item := range c {
							if m, ok := item.(map[string]any); ok {
								mapped = append(mapped, m)
							}
						}
						s.creds.Configure(mapped)
					}
				}
				if s.vpnMgr != nil {
					s.vpnMgr.Nudge("settings_change")
				}
				if s.ctrl != nil {
					s.ctrl.EnsureMinimum()
				}
			case "vpn_credentials":
				if s.vpnMgr != nil {
					s.vpnMgr.Nudge("credentials_change")
				}
				if s.ctrl != nil {
					s.ctrl.EnsureMinimum()
				}
			}
		}
	}
	mgWriteJSON(w, http.StatusOK, map[string]any{"applied": applied, "settings": s.settings.GetAll()})
}

func (s *ProxyServer) mgHandleExportSettings(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		mgWriteJSON(w, http.StatusOK, map[string]any{})
		return
	}
	w.Header().Set("Content-Disposition", "attachment; filename=acestream-settings.json")
	mgWriteJSON(w, http.StatusOK, s.settings.GetAll())
}

func (s *ProxyServer) mgHandleImportSettings(w http.ResponseWriter, r *http.Request) {
	s.mgHandleUpdateAllSettings(w, r)
}

func (s *ProxyServer) mgHandleGetSettingsCategory(cat string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if s.settings == nil {
			mgWriteJSON(w, http.StatusOK, map[string]any{})
			return
		}
		mgWriteJSON(w, http.StatusOK, s.settings.Get(cat))
	}
}

func (s *ProxyServer) mgHandleSetSettingsCategory(cat string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if s.settings == nil {
			mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "settings not available"})
			return
		}
		r.Body = http.MaxBytesReader(w, r.Body, maxJSONBodyBytes)
		var payload map[string]any
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
			return
		}
		if err := s.settings.Save(cat, payload); err != nil {
			mgWriteJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
			return
		}
		switch cat {
		case "proxy_settings":
			config.ApplySettings(payload)
		case "engine_settings":
			config.ApplyEngineSettings(payload)
			if s.ctrl != nil {
				s.ctrl.EnsureMinimum()
			}
		case "orchestrator_settings":
			config.ApplyOrchestratorSettings(payload)
			if s.ctrl != nil {
				s.ctrl.EnsureMinimum()
			}
		case "engine_config":
			config.ApplyEngineConfig(payload)
			// Push live-settable fields to running engines without restart.
			go cpengine.PushEngineConfig(context.Background(), payload)
			if s.ctrl != nil {
				s.ctrl.EnsureMinimum()
			}
		case "vpn_settings":
			config.ApplyVPNSettings(payload)
			if s.creds != nil {
				if c, ok := payload["credentials"].([]any); ok {
					var mapped []map[string]any
					for _, item := range c {
						if m, ok := item.(map[string]any); ok {
							mapped = append(mapped, m)
						}
					}
					s.creds.Configure(mapped)
				}
			}
			markedCount := 0
			// If VPN was toggled, mark engines for migration immediately.
			targetHash := cpengine.ComputeConfigHash()
			for _, e := range state.Global.ListEngines() {
				if (e.Labels["acestream.config_hash"] != targetHash) && !e.Draining {
					if state.Global.MarkEngineDraining(e.ContainerID, "config_drift_vpn") {
						markedCount++
					}
				}
			}

			if s.vpnMgr != nil {
				s.vpnMgr.Nudge("settings_change")
			}
			if s.ctrl != nil {
				s.ctrl.EnsureMinimum()
			}

			resp := s.settings.Get(cat)
			resp["migration_marked_engines"] = markedCount
			mgWriteJSON(w, http.StatusOK, resp)
			return

		case "vpn_credentials":
			if s.vpnMgr != nil {
				s.vpnMgr.Nudge("credentials_change")
			}
			if s.ctrl != nil {
				s.ctrl.EnsureMinimum()
			}
		}
		mgWriteJSON(w, http.StatusOK, s.settings.Get(cat))
	}
}

// mgHandleSetProxyConfig accepts both JSON body and query-string params (the
// React frontend posts proxy settings as ?key=value query params).
func (s *ProxyServer) mgHandleSetProxyConfig(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "settings not available"})
		return
	}
	payload := map[string]any{}
	// Try JSON body first; fall back to query params.
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil || len(payload) == 0 {
		q := r.URL.Query()
		for k, vals := range q {
			if len(vals) > 0 {
				payload[k] = vals[0]
			}
		}
	}
	if err := s.settings.Save("proxy_settings", payload); err != nil {
		mgWriteJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	config.ApplySettings(payload)
	mgWriteJSON(w, http.StatusOK, map[string]any{"message": "Proxy settings saved", "settings": s.settings.Get("proxy_settings")})
}

func (s *ProxyServer) mgHandleAddVPNCredential(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "settings not available"})
		return
	}
	r.Body = http.MaxBytesReader(w, r.Body, maxJSONBodyBytes)
	var cred map[string]any
	if err := json.NewDecoder(r.Body).Decode(&cred); err != nil {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	if cred["id"] == nil || cred["id"] == "" {
		cred["id"] = generateID()
	}
	vpnSettings := s.settings.Get("vpn_settings")
	creds, _ := vpnSettings["credentials"].([]any)
	creds = append(creds, cred)
	vpnSettings["credentials"] = creds
	if err := s.settings.Save("vpn_settings", vpnSettings); err != nil {
		mgWriteJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	// Reconfigure the in-memory credential manager if available.
	if s.creds != nil {
		rawCreds := make([]map[string]interface{}, 0, len(creds))
		for _, c := range creds {
			if m, ok := c.(map[string]interface{}); ok {
				rawCreds = append(rawCreds, m)
			}
		}
		_ = s.creds.Configure(rawCreds)
	}
	mgWriteJSON(w, http.StatusCreated, map[string]any{"credential": cred, "credentials_count": len(creds)})
}

func (s *ProxyServer) mgHandleDeleteVPNCredential(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "settings not available"})
		return
	}
	id := r.PathValue("id")
	vpnSettings := s.settings.Get("vpn_settings")
	creds, _ := vpnSettings["credentials"].([]any)
	var remaining []any
	found := false
	for _, raw := range creds {
		if item, ok := raw.(map[string]any); ok {
			if item["id"] == id {
				found = true
				continue
			}
		}
		remaining = append(remaining, raw)
	}
	if !found {
		mgWriteJSON(w, http.StatusNotFound, map[string]string{"error": "credential not found"})
		return
	}
	vpnSettings["credentials"] = remaining
	if err := s.settings.Save("vpn_settings", vpnSettings); err != nil {
		mgWriteJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	// Reconfigure the in-memory credential manager if available.
	if s.creds != nil {
		rawCreds := make([]map[string]interface{}, 0, len(remaining))
		for _, c := range remaining {
			if m, ok := c.(map[string]interface{}); ok {
				rawCreds = append(rawCreds, m)
			}
		}
		_ = s.creds.Configure(rawCreds)
	}
	mgWriteJSON(w, http.StatusOK, map[string]any{"credentials_count": len(remaining)})
}

// ─── Rolling reprovision ──────────────────────────────────────────────────────

var reprovisionStatus = map[string]any{
	"status":   "idle",
	"progress": 0,
}

func (s *ProxyServer) mgHandleReprovisionStatus(w http.ResponseWriter, r *http.Request) {
	mgWriteJSON(w, http.StatusOK, reprovisionStatus)
}

func (s *ProxyServer) mgHandleReprovision(w http.ResponseWriter, r *http.Request) {
	if s.ctrl != nil {
		s.ctrl.Nudge("reprovision")
	}
	mgWriteJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *ProxyServer) mgHandlePlatform(w http.ResponseWriter, r *http.Request) {
	mgWriteJSON(w, http.StatusOK, map[string]string{"platform": "linux/amd64"})
}

// ─── Observability ───────────────────────────────────────────────────────────

func (s *ProxyServer) mgHandleHealthStatus(w http.ResponseWriter, r *http.Request) {
	engines := s.st.ListEngines()
	var healthy, unhealthy, draining int
	for _, e := range engines {
		switch {
		case e.Draining:
			draining++
		case e.HealthStatus == state.HealthHealthy:
			healthy++
		default:
			unhealthy++
		}
	}
	mgWriteJSON(w, http.StatusOK, map[string]any{
		"status":            "healthy",
		"engines_total":     len(engines),
		"engines_healthy":   healthy,
		"engines_unhealthy": unhealthy,
		"engines_draining":  draining,
		"timestamp":         time.Now().UTC(),
	})
}

func (s *ProxyServer) mgHandleCircuitBreakerStatus(w http.ResponseWriter, r *http.Request) {
	if s.cb == nil {
		mgWriteJSON(w, http.StatusOK, map[string]any{"general": "closed", "replacement": "closed"})
		return
	}
	mgWriteJSON(w, http.StatusOK, s.cb.GetStatus())
}

func (s *ProxyServer) mgHandleCircuitBreakerReset(w http.ResponseWriter, r *http.Request) {
	if s.cb != nil {
		s.cb.ForceReset("general")
		s.cb.ForceReset("replacement")
	}
	mgWriteJSON(w, http.StatusOK, map[string]string{"status": "reset"})
}

func (s *ProxyServer) mgHandleOrchestratorStatus(w http.ResponseWriter, r *http.Request) {
	cfg := config.C.Load()
	engines := s.st.ListEngines()
	streams := s.st.ListStreams()
	vpns := s.st.ListVPNNodes()

	var healthy, unhealthy, draining int
	for _, e := range engines {
		switch {
		case e.Draining:
			draining++
		case e.HealthStatus == state.HealthHealthy:
			healthy++
		case e.HealthStatus == state.HealthUnhealthy:
			unhealthy++
		}
	}

	activeStreams := 0
	for _, st := range streams {
		if st.Status == "started" {
			activeStreams++
		}
	}

	vpnEnabled := cfg.VPNEnabled
	vpnHealthy := 0
	for _, v := range vpns {
		if v.Healthy {
			vpnHealthy++
		}
	}

	var cbState string
	var lastFailure any
	if s.cb != nil {
		cbStatus := s.cb.GetStatus()
		if gen, ok := cbStatus["general"].(map[string]any); ok {
			cbState, _ = gen["state"].(string)
			lastFailure = gen["last_failure_time"]
		}
	}
	if cbState == "" {
		cbState = "closed"
	}

	totalCapacity := len(engines)
	usedCapacity := 0
	streamCounts := s.st.GetStreamCounts()
	monCounts := s.st.GetMonitorCounts()
	for _, e := range engines {
		if streamCounts[e.ContainerID]+monCounts[e.ContainerID] > 0 {
			usedCapacity++
		}
	}
	availableCapacity := totalCapacity - usedCapacity
	if availableCapacity < 0 {
		availableCapacity = 0
	}

	canProvision := cbState == "closed"
	var blockedReason any
	if !canProvision {
		blockedReason = map[string]any{
			"code":    "circuit_breaker",
			"message": fmt.Sprintf("Provisioning circuit breaker is %s", cbState),
		}
	}

	var overallStatus string
	switch {
	case len(engines) == 0:
		overallStatus = "unavailable"
	case !canProvision:
		overallStatus = "degraded"
	default:
		overallStatus = "healthy"
	}

	mgWriteJSON(w, http.StatusOK, map[string]any{
		"status": overallStatus,
		"engines": map[string]any{
			"total":    len(engines),
			"healthy":  healthy,
			"unhealthy": unhealthy,
			"draining": draining,
		},
		"streams": map[string]any{
			"active": activeStreams,
			"total":  len(streams),
		},
		"capacity": map[string]any{
			"total":        totalCapacity,
			"used":         usedCapacity,
			"available":    availableCapacity,
			"max_replicas": cfg.MaxReplicas,
			"min_replicas": cfg.MinReplicas,
		},
		"vpn": map[string]any{
			"enabled":   vpnEnabled,
			"nodes_total": len(vpns),
			"healthy":   vpnHealthy,
		},
		"provisioning": map[string]any{
			"can_provision":        canProvision,
			"circuit_breaker_state": cbState,
			"last_failure":         lastFailure,
			"blocked_reason":       blockedReason,
		},
		"config": map[string]any{
			"proxy_listen":  cfg.ProxyListenAddr,
			"orchestrator":  cfg.OrchestratorListenAddr,
			"auto_delete":    cfg.AutoDelete,
			"grace_period_s": cfg.GracePeriod.Seconds(),
		},
		"timestamp": time.Now().UTC(),
	})
}

func (s *ProxyServer) mgHandleMetricsDashboard(w http.ResponseWriter, r *http.Request) {
	s.mgHandleOrchestratorStatus(w, r)
}

func (s *ProxyServer) mgHandleMetricsPerformance(w http.ResponseWriter, r *http.Request) {
	mgWriteJSON(w, http.StatusOK, map[string]any{"operations": map[string]any{}})
}

func (s *ProxyServer) mgHandleEvents(w http.ResponseWriter, r *http.Request) {
	mgWriteJSON(w, http.StatusOK, map[string]any{"events": []any{}, "total": 0})
}

func (s *ProxyServer) mgHandleEventsStats(w http.ResponseWriter, r *http.Request) {
	mgWriteJSON(w, http.StatusOK, map[string]any{"total": 0})
}

func (s *ProxyServer) mgHandleEventsCleanup(w http.ResponseWriter, r *http.Request) {
	mgWriteJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *ProxyServer) mgHandleCacheStats(w http.ResponseWriter, r *http.Request) {
	mgWriteJSON(w, http.StatusOK, map[string]any{"size": 0, "hits": 0, "misses": 0})
}

func (s *ProxyServer) mgHandleCacheClear(w http.ResponseWriter, r *http.Request) {
	mgWriteJSON(w, http.StatusOK, map[string]string{"status": "cleared"})
}

// ─── M3U ─────────────────────────────────────────────────────────────────────

func (s *ProxyServer) mgHandleModifyM3U(w http.ResponseWriter, r *http.Request) {
	// Accept both ?m3u_url= (Python compat) and ?url= (legacy)
	m3uURL := r.URL.Query().Get("m3u_url")
	if m3uURL == "" {
		m3uURL = r.URL.Query().Get("url")
	}
	if m3uURL == "" {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "m3u_url parameter required"})
		return
	}
	// Reject non-HTTP(S) schemes to prevent SSRF via file://, gopher://, etc.
	if parsed, err := url.Parse(m3uURL); err != nil || (parsed.Scheme != "http" && parsed.Scheme != "https") {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "m3u_url must use http or https scheme"})
		return
	}
	// Allow caller to override host and port
	host := r.URL.Query().Get("host")
	if host == "" {
		host = r.Host
	}
	if port := r.URL.Query().Get("port"); port != "" {
		// Replace or append port on host
		if h, _, hasPort := strings.Cut(host, ":"); hasPort {
			host = h + ":" + port
		} else {
			host = host + ":" + port
		}
	}
	if host == "" {
		host = "localhost:8000"
	}
	scheme := "http"
	if r.TLS != nil {
		scheme = "https"
	}
	baseURL := scheme + "://" + host

	req, err := http.NewRequestWithContext(r.Context(), http.MethodGet, m3uURL, nil)
	if err != nil {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid url"})
		return
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		mgWriteJSON(w, http.StatusBadGateway, map[string]string{"error": "failed to fetch m3u: " + err.Error()})
		return
	}
	defer resp.Body.Close()

	var buf strings.Builder
	var line string
	scanner := newLineScanner(resp.Body)
	for scanner.Scan() {
		line = scanner.Text()
		if strings.HasPrefix(line, "acestream://") {
			contentID := strings.TrimPrefix(line, "acestream://")
			line = baseURL + "/ace/getstream?id=" + contentID
		} else if strings.HasPrefix(line, "ace://") {
			contentID := strings.TrimPrefix(line, "ace://")
			line = baseURL + "/ace/getstream?id=" + contentID
		}
		buf.WriteString(line + "\n")
	}

	w.Header().Set("Content-Type", "application/x-mpegurl")
	w.WriteHeader(http.StatusOK)
	fmt.Fprint(w, buf.String())
}

// ─── Debug ───────────────────────────────────────────────────────────────────

func (s *ProxyServer) mgHandleVersion(w http.ResponseWriter, r *http.Request) {
	mgWriteJSON(w, http.StatusOK, map[string]any{
		"version":    versionString(),
		"go_unified": true,
		"timestamp":  time.Now().UTC(),
	})
}

func (s *ProxyServer) mgHandleAuthStatus(w http.ResponseWriter, r *http.Request) {
	apiKey := config.C.Load().APIKey
	mode := "none"
	if apiKey != "" {
		mode = "bearer"
	}
	mgWriteJSON(w, http.StatusOK, map[string]any{
		"required": apiKey != "",
		"mode":     mode,
	})
}

// ─── Static files ─────────────────────────────────────────────────────────────

func staticDir() string {
	if d := os.Getenv("STATIC_DIR"); d != "" {
		return d
	}
	return "/app/app/static/panel"
}

func (s *ProxyServer) registerStaticRoutes() {
	dir := staticDir()
	if _, err := os.Stat(dir); err != nil {
		slog.Debug("static dir not found, skipping static routes", "dir", dir)
		return
	}
	fileServer := http.FileServer(http.Dir(dir))

	for _, p := range []string{
		"/favicon.ico", "/favicon.svg",
		"/favicon-96x96.png", "/favicon-96x96-dark.png",
		"/apple-touch-icon.png",
	} {
		path := p
		s.mux.HandleFunc("GET "+path, func(w http.ResponseWriter, r *http.Request) {
			r.URL.Path = path
			fileServer.ServeHTTP(w, r)
		})
	}

	s.mux.HandleFunc("GET /panel/", func(w http.ResponseWriter, r *http.Request) {
		trimmedPath := strings.TrimPrefix(r.URL.Path, "/panel")
		if trimmedPath == "" || trimmedPath == "/" {
			r.URL.Path = "/"
			fileServer.ServeHTTP(w, r)
			return
		}

		// Check if file exists on disk.
		fpath := filepath.Join(dir, trimmedPath)
		info, err := os.Stat(fpath)
		if err != nil || info.IsDir() {
			// File not found or is a directory; serve index.html for SPA routing.
			r.URL.Path = "/index.html"
			fileServer.ServeHTTP(w, r)
			return
		}

		r.URL.Path = trimmedPath
		fileServer.ServeHTTP(w, r)
	})
}

// ─── WireGuard parser ─────────────────────────────────────────────────────────

func parseWireGuardConfig(cfg string) map[string]any {
	result := map[string]any{
		"private_key": "",
		"addresses":   []string{},
		"dns":         []string{},
		"peers":       []map[string]any{},
	}
	var currentPeer map[string]any
	for _, line := range strings.Split(cfg, "\n") {
		line = strings.TrimSpace(line)
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if line == "[Interface]" {
			currentPeer = nil
			continue
		}
		if line == "[Peer]" {
			currentPeer = map[string]any{}
			peers, _ := result["peers"].([]map[string]any)
			peers = append(peers, currentPeer)
			result["peers"] = peers
			continue
		}
		parts := strings.SplitN(line, "=", 2)
		if len(parts) != 2 {
			continue
		}
		key := strings.TrimSpace(parts[0])
		val := strings.TrimSpace(parts[1])
		if currentPeer != nil {
			currentPeer[strings.ToLower(key)] = val
		} else {
			switch key {
			case "PrivateKey":
				result["private_key"] = val
			case "Address":
				addrs, _ := result["addresses"].([]string)
				result["addresses"] = append(addrs, val)
			case "DNS":
				dns, _ := result["dns"].([]string)
				result["dns"] = append(dns, val)
			}
		}
	}
	return result
}

// ─── VPN public IP ────────────────────────────────────────────────────────────

func (s *ProxyServer) mgHandleVPNPublicIP(w http.ResponseWriter, r *http.Request) {
	nodes := s.st.ListVPNNodes()
	// Find the first healthy VPN node and query its Gluetun API for the public IP.
	for _, n := range nodes {
		if !n.Healthy || n.AssignedHostname == "" {
			continue
		}
		gluetunPort := config.C.Load().GluetunAPIPort
		url := fmt.Sprintf("http://%s:%d/v1/publicip/ip", n.AssignedHostname, gluetunPort)
		ctx, cancel := context.WithTimeout(r.Context(), 5*time.Second)
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		resp, err := http.DefaultClient.Do(req)
		cancel()
		if err != nil {
			continue
		}
		defer resp.Body.Close()
		var payload map[string]any
		if err := json.NewDecoder(resp.Body).Decode(&payload); err == nil {
			if ip, ok := payload["public_ip"].(string); ok && ip != "" {
				mgWriteJSON(w, http.StatusOK, map[string]string{"public_ip": ip})
				return
			}
		}
	}
	mgWriteJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "Unable to retrieve public IP"})
}

// ─── Proxy-plane event endpoints ─────────────────────────────────────────────

func (s *ProxyServer) mgHandleEventStreamStarted(w http.ResponseWriter, r *http.Request) {
	var ev state.StreamStartedEvent
	if err := json.NewDecoder(r.Body).Decode(&ev); err != nil {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	contentID := ev.ContentID
	if contentID == "" && ev.Stream != nil {
		contentID = ev.Stream.Key
	}
	if contentID == "" {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "content_id required"})
		return
	}
	st := s.st.OnStreamStarted(ev)
	mgWriteJSON(w, http.StatusOK, st)
}

func (s *ProxyServer) mgHandleEventStreamEnded(w http.ResponseWriter, r *http.Request) {
	var ev state.StreamEndedEvent
	if err := json.NewDecoder(r.Body).Decode(&ev); err != nil {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	if ev.ContentID == "" && ev.StreamID == "" {
		mgWriteJSON(w, http.StatusBadRequest, map[string]string{"error": "content_id or stream_id required"})
		return
	}
	s.st.OnStreamEnded(ev)
	mgWriteJSON(w, http.StatusOK, map[string]string{"status": "ended"})
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

func mgWriteJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Error("mgWriteJSON encode failed", "err", err)
	}
}

func versionString() string {
	if v := os.Getenv("APP_VERSION"); v != "" {
		return v
	}
	return "go-unified"
}

func newLineScanner(r interface{ Read([]byte) (int, error) }) *lineScanner {
	return &lineScanner{r: r, buf: make([]byte, 0, 4096)}
}

type lineScanner struct {
	r    interface{ Read([]byte) (int, error) }
	buf  []byte
	line string
	done bool
}

func (ls *lineScanner) Scan() bool {
	if ls.done {
		return false
	}
	for {
		if i := strings.IndexByte(string(ls.buf), '\n'); i >= 0 {
			ls.line = strings.TrimRight(string(ls.buf[:i]), "\r")
			ls.buf = ls.buf[i+1:]
			return true
		}
		tmp := make([]byte, 4096)
		n, err := ls.r.Read(tmp)
		if n > 0 {
			ls.buf = append(ls.buf, tmp[:n]...)
		}
		if err != nil {
			ls.done = true
			if len(ls.buf) > 0 {
				ls.line = strings.TrimRight(string(ls.buf), "\r\n")
				ls.buf = nil
				return ls.line != ""
			}
			return false
		}
	}
}

func (ls *lineScanner) Text() string { return ls.line }
