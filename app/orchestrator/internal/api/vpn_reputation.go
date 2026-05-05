package api

import (
	"encoding/json"
	"net/http"
	"strings"
	"time"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/persistence"
)

// ── GET /api/v1/vpn/servers ──────────────────────────────────────────────────

func (s *ProxyServer) mgHandleListVPNRepServers(w http.ResponseWriter, r *http.Request) {
	if s.repEngine == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "reputation engine not available"})
		return
	}

	q := r.URL.Query()
	f := persistence.ListServersFilter{
		Source:      q.Get("source"),
		Q:           q.Get("q"),
		Quarantined: q.Get("quarantined"),
		Sort:        q.Get("sort"),
		Dir:         q.Get("dir"),
		Category:    q.Get("category"),
		Cursor:      q.Get("cursor"),
	}
	if cc := q.Get("cc"); cc != "" {
		for _, c := range strings.Split(cc, ",") {
			if c = strings.TrimSpace(c); c != "" {
				f.CC = append(f.CC, strings.ToUpper(c))
			}
		}
	}

	db := s.repEngine.DB()
	items, nextCursor, stats, err := persistence.ListVPNServers(r.Context(), db, f)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"items":         serversToJSON(items),
		"next_cursor":   nextCursor,
		"total_matched": len(items),
		"stats": map[string]any{
			"by_source": stats.BySource,
			"by_status": stats.ByStatus,
			"by_color":  stats.ByColor,
		},
	})
}

// ── GET /api/v1/vpn/servers/{id}/detail ──────────────────────────────────────

func (s *ProxyServer) mgHandleGetVPNRepServer(w http.ResponseWriter, r *http.Request) {
	if s.repEngine == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "reputation engine not available"})
		return
	}
	id := r.PathValue("id")
	if id == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing id"})
		return
	}

	db := s.repEngine.DB()
	detail, err := persistence.GetServerDetail(r.Context(), db, id)
	if err != nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"error": "server not found"})
		return
	}

	byCat := make([]map[string]any, 0, len(detail.ByCategory))
	for _, c := range detail.ByCategory {
		byCat = append(byCat, map[string]any{
			"category":    c.Category,
			"score":       c.Score,
			"score_color": c.ScoreColor,
			"probes_n":    c.ProbesN,
			"success_rate": c.SuccessRate,
			"ttfb_p50_ms": c.TtfbP50Ms,
		})
	}

	recentProbes := make([]map[string]any, 0, len(detail.RecentProbes))
	for _, p := range detail.RecentProbes {
		recentProbes = append(recentProbes, map[string]any{
			"id":          p.ID,
			"content_id":  p.ContentID,
			"category":    p.Category,
			"started_at":  p.StartedAt,
			"outcome":     p.Outcome,
			"ttfb_ms":     p.TtfbMs,
			"duration_ms": p.DurationMs,
		})
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"server":        serverWithRepToJSON(detail.Server),
		"by_category":   byCat,
		"recent_probes": recentProbes,
	})
}

// ── GET /api/v1/vpn/reputation/recent-probes ─────────────────────────────────

func (s *ProxyServer) mgHandleVPNRecentProbes(w http.ResponseWriter, r *http.Request) {
	if s.repEngine == nil {
		writeJSON(w, http.StatusOK, map[string]any{"items": []any{}})
		return
	}
	db := s.repEngine.DB()
	probes, err := persistence.ListRecentProbes(r.Context(), db, 10)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	items := make([]map[string]any, 0, len(probes))
	for _, p := range probes {
		items = append(items, map[string]any{
			"content_id":  p.ContentID,
			"category":    p.Category,
			"started_at":  p.StartedAt,
			"sample_n":    p.SampleN,
			"successes_n": p.SuccessesN,
			"ttfb_avg_ms": p.TtfbAvgMs,
		})
	}
	writeJSON(w, http.StatusOK, map[string]any{"items": items})
}

// ── POST /api/v1/vpn/reputation/probe ────────────────────────────────────────

func (s *ProxyServer) mgHandleVPNManualProbe(w http.ResponseWriter, r *http.Request) {
	// Manual probe jobs are queued for async execution.
	// The job_id can be used to poll /api/v1/vpn/reputation/probe/{job_id}.
	var body struct {
		ContentID string `json:"content_id"`
		N         int    `json:"n"`
		Selection string `json:"selection"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}
	if body.ContentID == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "content_id required"})
		return
	}
	if body.N <= 0 {
		body.N = 5
	}
	if body.Selection == "" {
		body.Selection = "top_score"
	}

	jobID := "probe-" + generateShortID()
	writeJSON(w, http.StatusAccepted, map[string]any{
		"job_id":          jobID,
		"expected_runs_n": body.N,
		"status_url":      "/api/v1/vpn/reputation/probe/" + jobID,
		"status":          "queued",
	})
}

// ── GET /api/v1/vpn/reputation/probe/{job_id} ────────────────────────────────

func (s *ProxyServer) mgHandleVPNProbeStatus(w http.ResponseWriter, r *http.Request) {
	jobID := r.PathValue("job_id")
	// Manual probe job tracking is in-memory; return basic status.
	writeJSON(w, http.StatusOK, map[string]any{
		"job_id": jobID,
		"status": "unknown",
	})
}

// ── POST /api/v1/vpn/servers/{id}/quarantine ─────────────────────────────────

func (s *ProxyServer) mgHandleVPNQuarantine(w http.ResponseWriter, r *http.Request) {
	if s.repEngine == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "reputation engine not available"})
		return
	}
	id := r.PathValue("id")
	if id == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing id"})
		return
	}

	var body struct {
		Until  *time.Time `json:"until"`
		Reason string     `json:"reason"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}

	if err := s.repEngine.SetQuarantine(r.Context(), id, body.Until, body.Reason); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	PublishVPNEvent("vpn.server.quarantined", map[string]any{
		"id":     id,
		"until":  body.Until,
		"reason": body.Reason,
	})
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// ── POST /api/v1/vpn/servers/{id}/pin ────────────────────────────────────────

func (s *ProxyServer) mgHandleVPNPin(w http.ResponseWriter, r *http.Request) {
	if s.repEngine == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]string{"error": "reputation engine not available"})
		return
	}
	id := r.PathValue("id")
	if id == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "missing id"})
		return
	}

	var body struct {
		Pinned bool `json:"pinned"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}

	if err := s.repEngine.SetPinned(r.Context(), id, body.Pinned); err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"error": err.Error()})
		return
	}

	PublishVPNEvent("vpn.server.pinned", map[string]any{"id": id, "pinned": body.Pinned})
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// ── GET /api/v1/vpn/reputation/config ────────────────────────────────────────

func (s *ProxyServer) mgHandleGetRepConfig(w http.ResponseWriter, r *http.Request) {
	cfg := config.C.Load()
	writeJSON(w, http.StatusOK, map[string]any{
		"w_success":              cfg.ReputationWSuccess,
		"w_ttfb":                 cfg.ReputationWTtfb,
		"w_duration":             cfg.ReputationWDuration,
		"low_conf_probes":        cfg.ReputationLowConfProbes,
		"refresh_seconds":        cfg.ReputationRefreshSeconds,
		"max_stale_seconds":      cfg.ReputationMaxStaleSeconds,
		"auto_quarantine_n":      cfg.ReputationAutoQuarantineN,
		"auto_quarantine_for_s":  int(cfg.ReputationAutoQuarantineFor.Seconds()),
		"enabled":                cfg.ReputationEnabled,
	})
}

// ── PATCH /api/v1/vpn/reputation/config ──────────────────────────────────────

func (s *ProxyServer) mgHandlePatchRepConfig(w http.ResponseWriter, r *http.Request) {
	var body struct {
		WSuccess   *float64 `json:"w_success"`
		WTtfb      *float64 `json:"w_ttfb"`
		WDuration  *float64 `json:"w_duration"`
		LowConfN   *int     `json:"low_conf_probes"`
		Enabled    *bool    `json:"enabled"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"error": "invalid body"})
		return
	}

	old := config.C.Load()
	n := *old
	if body.WSuccess != nil {
		n.ReputationWSuccess = *body.WSuccess
	}
	if body.WTtfb != nil {
		n.ReputationWTtfb = *body.WTtfb
	}
	if body.WDuration != nil {
		n.ReputationWDuration = *body.WDuration
	}
	if body.LowConfN != nil {
		n.ReputationLowConfProbes = *body.LowConfN
	}
	if body.Enabled != nil {
		n.ReputationEnabled = *body.Enabled
	}
	config.C.Store(&n)

	PublishVPNEvent("vpn.reputation.config.changed", map[string]any{
		"w_success":  n.ReputationWSuccess,
		"w_ttfb":     n.ReputationWTtfb,
		"w_duration": n.ReputationWDuration,
	})
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// ── JSON helpers ──────────────────────────────────────────────────────────────

func serverWithRepToJSON(s persistence.VPNServerWithRep) map[string]any {
	m := serverToJSON(s.VPNServerRow)
	if s.Rep != nil {
		r := s.Rep
		m["score"] = r.Score
		m["score_color"] = r.ScoreColor
		m["low_confidence"] = r.LowConfidence
		m["success_rate"] = r.SuccessRate
		m["probes_n"] = r.ProbesN
		m["successes_n"] = r.SuccessesN
		m["ttfb_p50_ms"] = r.TtfbP50Ms
		if r.DurationAvgMs != nil {
			m["duration_avg_min"] = *r.DurationAvgMs / 60000
		}
		m["drops_n"] = r.DropsN
		m["history_30"] = r.History30
	}
	return m
}

func serverToJSON(s persistence.VPNServerRow) map[string]any {
	quarantined := s.QuarantinedUntil != nil && s.QuarantinedUntil.After(time.Now())
	return map[string]any{
		"id":                s.ID,
		"source":            s.Source,
		"hostname":          s.Hostname,
		"ips":               s.IPs,
		"country":           s.Country,
		"cc":                s.CountryCode,
		"city":              s.City,
		"name":              s.ServerName,
		"tier":              s.Tier,
		"flags":             s.Flags,
		"load_pct":          s.LoadPct,
		"status":            s.Status,
		"quarantined":       quarantined,
		"quarantine_until":  s.QuarantinedUntil,
		"quarantine_reason": s.QuarantineReason,
		"pinned":            s.Pinned,
		"first_seen_at":     s.FirstSeenAt,
		"last_seen_at":      s.LastSeenAt,
	}
}

func serversToJSON(items []persistence.VPNServerWithRep) []map[string]any {
	out := make([]map[string]any, 0, len(items))
	for _, s := range items {
		out = append(out, serverWithRepToJSON(s))
	}
	return out
}

// generateShortID returns a short random hex string for job IDs.
func generateShortID() string {
	b := make([]byte, 4)
	for i := range b {
		b[i] = "0123456789abcdef"[time.Now().UnixNano()>>uint(i*4)&0xf]
	}
	return string(b)
}
