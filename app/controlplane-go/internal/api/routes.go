package api

import (
	"context"
	"encoding/json"
	"log/slog"
	"net/http"
	"strings"

	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/acestream/controlplane/internal/circuitbreaker"
	"github.com/acestream/controlplane/internal/config"
	"github.com/acestream/controlplane/internal/docker"
	"github.com/acestream/controlplane/internal/engine"
	"github.com/acestream/controlplane/internal/state"
	vpnpkg "github.com/acestream/controlplane/internal/vpn"
)

// Server holds references to all subsystems needed to serve the API.
type Server struct {
	ctrl    *engine.Controller
	health  *engine.HealthManager
	cb      *circuitbreaker.Manager
	pub     *state.RedisPublisher
	prov    *vpnpkg.Provisioner
	creds   *vpnpkg.CredentialManager
	svcRefresh *vpnpkg.ServersRefreshService
	httpSrv *http.Server
}

func NewServer(
	ctrl *engine.Controller,
	hm *engine.HealthManager,
	cb *circuitbreaker.Manager,
	pub *state.RedisPublisher,
	prov *vpnpkg.Provisioner,
	creds *vpnpkg.CredentialManager,
	svcRefresh *vpnpkg.ServersRefreshService,
) *Server {
	s := &Server{
		ctrl:       ctrl,
		health:     hm,
		cb:         cb,
		pub:        pub,
		prov:       prov,
		creds:      creds,
		svcRefresh: svcRefresh,
	}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /status", s.handleStatus)
	mux.HandleFunc("GET /engines", s.handleListEngines)
	mux.HandleFunc("POST /scale", s.handleScale)
	mux.HandleFunc("POST /reconcile", s.handleReconcile)
	mux.HandleFunc("DELETE /engines/{id}", s.handleDeleteEngine)
	mux.HandleFunc("GET /vpn-nodes", s.handleListVPNNodes)
	mux.HandleFunc("POST /vpn-nodes/{name}/drain", s.handleDrainVPNNode)
	mux.HandleFunc("POST /vpn-nodes/provision", s.handleProvisionVPNNode)
	mux.HandleFunc("DELETE /vpn-nodes/{name}", s.handleDeleteVPNNode)
	mux.HandleFunc("GET /vpn-credentials", s.handleListVPNCredentials)
	mux.HandleFunc("POST /vpn-servers/refresh", s.handleRefreshVPNServers)
	mux.HandleFunc("GET /circuit-breaker", s.handleCircuitBreaker)
	mux.HandleFunc("POST /circuit-breaker/reset", s.handleCircuitBreakerReset)
	mux.Handle("GET /metrics", promhttp.Handler())

	handler := apiKeyMiddleware(mux)
	s.httpSrv = &http.Server{
		Addr:    config.C.ListenAddr,
		Handler: handler,
	}
	return s
}

func (s *Server) Start() error {
	slog.Info("API server listening", "addr", config.C.ListenAddr)
	return s.httpSrv.ListenAndServe()
}

func (s *Server) Shutdown(ctx context.Context) error {
	return s.httpSrv.Shutdown(ctx)
}

// ─── Middleware ───────────────────────────────────────────────────────────────

func apiKeyMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		key := config.C.APIKey
		if key == "" {
			next.ServeHTTP(w, r)
			return
		}
		// Allow /metrics and /health without auth
		if r.URL.Path == "/metrics" || r.URL.Path == "/health" {
			next.ServeHTTP(w, r)
			return
		}
		provided := r.Header.Get("X-API-Key")
		if provided == "" {
			if auth := r.Header.Get("Authorization"); strings.HasPrefix(auth, "Bearer ") {
				provided = strings.TrimPrefix(auth, "Bearer ")
			}
		}
		if provided != key {
			writeJSON(w, http.StatusUnauthorized, map[string]string{"error": "unauthorized"})
			return
		}
		next.ServeHTTP(w, r)
	})
}

// ─── Handlers ─────────────────────────────────────────────────────────────────

func (s *Server) handleHealth(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (s *Server) handleStatus(w http.ResponseWriter, r *http.Request) {
	cfg := config.C
	st := state.Global
	engines := st.ListEngines()
	vpnNodes := st.ListVPNNodes()

	var healthyCount, unhealthyCount int
	for _, e := range engines {
		if e.HealthStatus == state.HealthHealthy {
			healthyCount++
		} else if e.HealthStatus == state.HealthUnhealthy {
			unhealthyCount++
		}
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"engines_total":    len(engines),
		"engines_healthy":  healthyCount,
		"engines_unhealthy": unhealthyCount,
		"vpn_nodes":        len(vpnNodes),
		"desired_replicas": st.GetDesiredReplicas(),
		"min_replicas":     cfg.MinReplicas,
		"max_replicas":     cfg.MaxReplicas,
		"health":           s.health.GetSummary(),
		"circuit_breaker":  s.cb.GetStatus(),
	})
}

func (s *Server) handleListEngines(w http.ResponseWriter, r *http.Request) {
	engines := state.Global.ListEngines()
	writeJSON(w, http.StatusOK, map[string]any{
		"engines": engines,
		"total":   len(engines),
	})
}

func (s *Server) handleScale(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Desired int `json:"desired"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	cfg := config.C
	if body.Desired < cfg.MinReplicas || body.Desired > cfg.MaxReplicas {
		writeJSON(w, http.StatusBadRequest, map[string]string{
			"error": "desired out of range",
		})
		return
	}
	s.ctrl.ScaleTo(body.Desired)
	writeJSON(w, http.StatusAccepted, map[string]any{"desired": body.Desired})
}

func (s *Server) handleReconcile(w http.ResponseWriter, r *http.Request) {
	s.ctrl.Nudge("api_reconcile")
	docker.Reindex(r.Context())
	writeJSON(w, http.StatusAccepted, map[string]string{"status": "reconcile triggered"})
}

func (s *Server) handleDeleteEngine(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	if id == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing id"})
		return
	}

	ctx := r.Context()
	if err := engine.StopContainer(ctx, id, false); err != nil {
		slog.Error("API: failed to stop engine", "id", id, "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	state.Global.RemoveEngine(id)
	s.ctrl.Nudge("api_engine_deleted")
	writeJSON(w, http.StatusOK, map[string]string{"status": "stopped"})
}

func (s *Server) handleListVPNNodes(w http.ResponseWriter, r *http.Request) {
	nodes := state.Global.ListVPNNodes()
	writeJSON(w, http.StatusOK, map[string]any{
		"vpn_nodes": nodes,
		"total":     len(nodes),
	})
}

func (s *Server) handleDrainVPNNode(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	if name == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing name"})
		return
	}
	st := state.Global
	node, ok := st.GetVPNNode(name)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "vpn node not found"})
		return
	}
	if !st.SetVPNNodeDraining(name) {
		writeJSON(w, http.StatusConflict, map[string]string{"error": "already draining"})
		return
	}
	s.pub.PublishVPNNode(r.Context(), node)
	slog.Info("API: VPN node drain requested", "name", name)
	writeJSON(w, http.StatusAccepted, map[string]string{"status": "draining", "name": name})
}

func (s *Server) handleCircuitBreaker(w http.ResponseWriter, r *http.Request) {
	writeJSON(w, http.StatusOK, s.cb.GetStatus())
}

func (s *Server) handleCircuitBreakerReset(w http.ResponseWriter, r *http.Request) {
	s.cb.ForceReset("general")
	s.cb.ForceReset("replacement")
	writeJSON(w, http.StatusOK, map[string]string{"status": "reset"})
}

func (s *Server) handleProvisionVPNNode(w http.ResponseWriter, r *http.Request) {
	if s.prov == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "VPN provisioning not configured"})
		return
	}
	result, err := s.prov.ProvisionNode(r.Context())
	if err != nil {
		slog.Error("API: VPN provision failed", "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusCreated, result)
}

func (s *Server) handleDeleteVPNNode(w http.ResponseWriter, r *http.Request) {
	name := r.PathValue("name")
	if name == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing name"})
		return
	}
	if s.prov == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "VPN provisioning not configured"})
		return
	}
	if err := s.prov.DestroyNode(r.Context(), name); err != nil {
		slog.Error("API: VPN destroy failed", "name", name, "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]string{"status": "destroyed", "name": name})
}

func (s *Server) handleListVPNCredentials(w http.ResponseWriter, r *http.Request) {
	if s.creds == nil {
		writeJSON(w, http.StatusOK, map[string]interface{}{"total": 0, "available": 0, "leased": 0})
		return
	}
	writeJSON(w, http.StatusOK, s.creds.Summary())
}

func (s *Server) handleRefreshVPNServers(w http.ResponseWriter, r *http.Request) {
	if s.svcRefresh == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "servers refresh not configured"})
		return
	}
	go func() {
		if err := s.svcRefresh.RefreshOfficial(context.Background()); err != nil {
			slog.Warn("Manual VPN servers refresh failed", "err", err)
		}
	}()
	writeJSON(w, http.StatusAccepted, map[string]string{"status": "refresh triggered"})
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Error("writeJSON encode failed", "err", err)
	}
}
