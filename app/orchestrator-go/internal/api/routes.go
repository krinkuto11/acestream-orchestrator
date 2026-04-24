package api

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"math/rand"
	"net/http"
	"strings"

	"github.com/prometheus/client_golang/prometheus/promhttp"

	"github.com/acestream/orchestrator/internal/bridge"
	"github.com/acestream/orchestrator/internal/config"
	"github.com/acestream/orchestrator/internal/persistence"
	"github.com/acestream/orchestrator/internal/state"
)

// Server holds all subsystem references needed to serve the API.
type Server struct {
	st       *state.Store
	bridge   *bridge.RedisBridge
	settings *persistence.SettingsStore
	httpSrv  *http.Server
}

func NewServer(st *state.Store, br *bridge.RedisBridge, settings *persistence.SettingsStore) *Server {
	s := &Server{st: st, bridge: br, settings: settings}

	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.handleHealth)
	mux.HandleFunc("GET /status", s.handleStatus)
	mux.HandleFunc("GET /streams", s.handleListStreams)
	mux.HandleFunc("GET /streams/{id}", s.handleGetStream)
	mux.HandleFunc("DELETE /streams/{id}", s.handleDeleteStream)
	mux.HandleFunc("POST /events/stream_started", s.handleStreamStarted)
	mux.HandleFunc("POST /events/stream_ended", s.handleStreamEnded)
	mux.HandleFunc("GET /engines", s.handleListEngines)
	mux.HandleFunc("GET /vpn-nodes", s.handleListVPNNodes)
	// Settings
	mux.HandleFunc("GET /settings", s.handleGetSettings)
	mux.HandleFunc("POST /settings", s.handleUpdateSettings)
	mux.HandleFunc("GET /settings/{category}", s.handleGetSettingsCategory)
	mux.HandleFunc("POST /settings/{category}", s.handleUpdateSettingsCategory)
	// VPN credential lifecycle
	mux.HandleFunc("POST /settings/vpn/credentials", s.handleAddVPNCredential)
	mux.HandleFunc("DELETE /settings/vpn/credentials/{id}", s.handleDeleteVPNCredential)
	mux.Handle("GET /metrics", promhttp.Handler())

	handler := apiKeyMiddleware(mux)
	s.httpSrv = &http.Server{
		Addr:    config.C.ListenAddr,
		Handler: handler,
	}
	return s
}

func (s *Server) Start() error {
	slog.Info("Orchestrator API listening", "addr", config.C.ListenAddr)
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
	streams := s.st.ListStreams()
	engines := s.st.ListEngines()
	vpns := s.st.ListVPNNodes()
	writeJSON(w, http.StatusOK, map[string]any{
		"streams_active": len(streams),
		"engines_total":  len(engines),
		"vpn_nodes":      len(vpns),
	})
}

func (s *Server) handleListStreams(w http.ResponseWriter, r *http.Request) {
	streams := s.st.ListStreams()
	writeJSON(w, http.StatusOK, map[string]any{
		"streams": streams,
		"total":   len(streams),
	})
}

func (s *Server) handleGetStream(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	st, ok := s.st.GetStream(id)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "stream not found"})
		return
	}
	writeJSON(w, http.StatusOK, st)
}

func (s *Server) handleDeleteStream(w http.ResponseWriter, r *http.Request) {
	id := r.PathValue("id")
	_, ok := s.st.GetStream(id)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "stream not found"})
		return
	}
	s.st.OnStreamEnded(state.StreamEndedEvent{ContentID: id})
	s.bridge.PublishCounts(r.Context())
	writeJSON(w, http.StatusOK, map[string]string{"status": "deleted"})
}

func (s *Server) handleStreamStarted(w http.ResponseWriter, r *http.Request) {
	var ev state.StreamStartedEvent
	if err := json.NewDecoder(r.Body).Decode(&ev); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	if ev.ContentID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "content_id required"})
		return
	}
	st := s.st.OnStreamStarted(ev)
	s.bridge.PublishCounts(r.Context())
	slog.Info("Stream started", "content_id", ev.ContentID, "engine_id", ev.EngineID)
	writeJSON(w, http.StatusCreated, st)
}

func (s *Server) handleStreamEnded(w http.ResponseWriter, r *http.Request) {
	var ev state.StreamEndedEvent
	if err := json.NewDecoder(r.Body).Decode(&ev); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	if ev.ContentID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "content_id required"})
		return
	}
	s.st.OnStreamEnded(ev)
	s.bridge.PublishCounts(r.Context())
	slog.Info("Stream ended", "content_id", ev.ContentID)
	writeJSON(w, http.StatusOK, map[string]string{"status": "ended"})
}

func (s *Server) handleListEngines(w http.ResponseWriter, r *http.Request) {
	engines := s.st.ListEngines()
	writeJSON(w, http.StatusOK, map[string]any{
		"engines": engines,
		"total":   len(engines),
	})
}

func (s *Server) handleListVPNNodes(w http.ResponseWriter, r *http.Request) {
	nodes := s.st.ListVPNNodes()
	writeJSON(w, http.StatusOK, map[string]any{
		"vpn_nodes": nodes,
		"total":     len(nodes),
	})
}

// ─── Settings handlers ────────────────────────────────────────────────────────

var validCategories = map[string]bool{
	"engine_config":        true,
	"engine_settings":      true,
	"orchestrator_settings": true,
	"proxy_settings":       true,
	"vpn_settings":         true,
}

func (s *Server) handleGetSettings(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "settings not available"})
		return
	}
	writeJSON(w, http.StatusOK, s.settings.GetAll())
}

func (s *Server) handleUpdateSettings(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "settings not available"})
		return
	}
	var body map[string]map[string]any
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	applied := map[string]bool{}
	for cat, payload := range body {
		if !validCategories[cat] {
			continue
		}
		if err := s.settings.Save(cat, payload); err != nil {
			slog.Error("settings save failed", "category", cat, "err", err)
			applied[cat] = false
		} else {
			applied[cat] = true
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"applied":  applied,
		"settings": s.settings.GetAll(),
	})
}

func (s *Server) handleGetSettingsCategory(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "settings not available"})
		return
	}
	cat := r.PathValue("category")
	if !validCategories[cat] {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "unknown category"})
		return
	}
	writeJSON(w, http.StatusOK, s.settings.Get(cat))
}

func (s *Server) handleUpdateSettingsCategory(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "settings not available"})
		return
	}
	cat := r.PathValue("category")
	if !validCategories[cat] {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "unknown category"})
		return
	}
	var payload map[string]any
	if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	if err := s.settings.Save(cat, payload); err != nil {
		slog.Error("settings save failed", "category", cat, "err", err)
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, s.settings.Get(cat))
}

// ─── VPN credential handlers ─────────────────────────────────────────────────

func (s *Server) handleAddVPNCredential(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "settings not available"})
		return
	}
	var credential map[string]any
	if err := json.NewDecoder(r.Body).Decode(&credential); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid json"})
		return
	}
	if credential["id"] == nil || credential["id"] == "" {
		credential["id"] = generateID()
	}

	vpnSettings := s.settings.Get("vpn_settings")
	creds, _ := vpnSettings["credentials"].([]any)
	creds = append(creds, credential)
	vpnSettings["credentials"] = creds

	if err := s.settings.Save("vpn_settings", vpnSettings); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusCreated, map[string]any{
		"credential":        credential,
		"credentials_count": len(creds),
	})
}

func (s *Server) handleDeleteVPNCredential(w http.ResponseWriter, r *http.Request) {
	if s.settings == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "settings not available"})
		return
	}
	id := r.PathValue("id")
	if id == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing id"})
		return
	}

	vpnSettings := s.settings.Get("vpn_settings")
	creds, _ := vpnSettings["credentials"].([]any)
	var remaining []any
	found := false
	for _, raw := range creds {
		if item, ok := raw.(map[string]any); ok {
			if credID, _ := item["id"].(string); credID == id {
				found = true
				continue
			}
		}
		remaining = append(remaining, raw)
	}
	if !found {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "credential not found"})
		return
	}
	vpnSettings["credentials"] = remaining
	if err := s.settings.Save("vpn_settings", vpnSettings); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"credentials_count": len(remaining),
	})
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

func generateID() string {
	return fmt.Sprintf("cred-%016x", rand.Uint64())
}

func writeJSON(w http.ResponseWriter, code int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(code)
	if err := json.NewEncoder(w).Encode(v); err != nil {
		slog.Error("writeJSON encode failed", "err", err)
	}
}
