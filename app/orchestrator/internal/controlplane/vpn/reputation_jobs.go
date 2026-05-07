package vpn

import (
	"context"
	"database/sql"
	"fmt"
	"io"
	"log/slog"
	"math/rand"
	"net/http"
	"strings"
	"time"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/persistence"
	"github.com/acestream/acestream/internal/proxy/aceapi"
	"github.com/acestream/acestream/internal/state"
)

// runReputationJobs starts all background reputation maintenance goroutines.
func runReputationJobs(ctx context.Context, db *sql.DB, re *ReputationEngine) {
	go jobReputationRefresh(ctx, db)
	go jobAutoQuarantine(ctx, db)
	go jobDailyHistorySnapshot(ctx, db)
	go jobActiveProbe(ctx, db, re)
}

// jobReputationRefresh recomputes vpn_reputation rows for servers with new probes.
func jobReputationRefresh(ctx context.Context, db *sql.DB) {
	cfg := config.C.Load()
	interval := time.Duration(cfg.ReputationRefreshSeconds) * time.Second
	if interval <= 0 {
		interval = 60 * time.Second
	}

	lastRun := time.Now().UTC().Add(-interval)
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			cfg = config.C.Load()
			if !cfg.ReputationEnabled {
				continue
			}
			interval = time.Duration(cfg.ReputationRefreshSeconds) * time.Second
			ticker.Reset(interval)

			repCfg := persistence.ReputationConfig{
				WSuccess:        cfg.ReputationWSuccess,
				WTtfb:           cfg.ReputationWTtfb,
				WDuration:       cfg.ReputationWDuration,
				LowConfProbes:   cfg.ReputationLowConfProbes,
				ColorThresholds: [4]float64{0.85, 0.65, 0.40, 0},
			}

			since := lastRun
			lastRun = time.Now().UTC()

			dirtyServers, err := persistence.FindDirtyServers(ctx, db, since)
			if err != nil {
				slog.Warn("reputation refresh: find dirty servers", "err", err)
				continue
			}
			if len(dirtyServers) == 0 {
				continue
			}

			refreshed := 0
			for _, serverID := range dirtyServers {
				cats, _ := persistence.FindDistinctCategories(ctx, db, serverID)
				allCats := append(cats, "_overall")
				for _, cat := range allCats {
					_, err := persistence.RefreshReputation(ctx, db, serverID, cat, repCfg)
					if err != nil {
						slog.Warn("reputation refresh: compute", "server_id", serverID, "category", cat, "err", err)
					} else {
						refreshed++
					}
				}
			}

			if refreshed > 0 {
				slog.Debug("reputation refresh: done", "servers", len(dirtyServers), "rows_refreshed", refreshed)
			}
		}
	}
}

// jobAutoQuarantine quarantines servers with consecutive all-failures in last 30min.
func jobAutoQuarantine(ctx context.Context, db *sql.DB) {
	ticker := time.NewTicker(5 * time.Minute)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			cfg := config.C.Load()
			if !cfg.ReputationEnabled || cfg.ReputationAutoQuarantineN <= 0 {
				continue
			}

			candidates, err := persistence.FindAutoQuarantineCandidates(ctx, db, cfg.ReputationAutoQuarantineN)
			if err != nil {
				slog.Warn("auto-quarantine: find candidates", "err", err)
				continue
			}

			for _, serverID := range candidates {
				until := time.Now().UTC().Add(cfg.ReputationAutoQuarantineFor)
				if err := persistence.SetQuarantine(ctx, db, serverID, &until, "auto: consecutive failures"); err != nil {
					slog.Warn("auto-quarantine: set", "server_id", serverID, "err", err)
					continue
				}
				slog.Info("VPN server auto-quarantined", "server_id", serverID, "until", until)
			}
		}
	}
}

// jobActiveProbe periodically provisions a temporary VPN+engine pair to probe
// underprobed servers using recently-seen content IDs, building reputation
// data independently of user demand.
// probeSession holds the live VPN node and engine containers from a completed
// active probe so they can be reused on the next tick if the target is the same
// and the interval is short enough to make reuse worthwhile.
type probeSession struct {
	TargetHostname    string
	TargetProvider    string
	VPNResult         *ProvisionResult
	EngineName        string
	EngineHTTPPort    int
	EngineAPIPort     int
	EngineContainerID string
}

func (s *probeSession) destroy(re *ReputationEngine) {
	if err := re.spawner.StopEngine(context.Background(), s.EngineContainerID); err != nil {
		slog.Warn("active probe: stop engine failed", "err", err)
	}
	if node, ok := state.Global.GetVPNNode(s.VPNResult.ContainerName); ok {
		node.ActiveProbe = nil
		state.Global.UpsertVPNNode(node)
	}
	if err := re.prov.DestroyNode(context.Background(), s.VPNResult.ContainerName); err != nil {
		slog.Warn("active probe: destroy vpn node failed", "err", err)
	}
}

func jobActiveProbe(ctx context.Context, db *sql.DB, re *ReputationEngine) {
	const minInterval = 60 * time.Second
	// Sessions are reused across ticks when the target is the same and the
	// probe interval is below this threshold — avoids VPN+engine cold-start
	// overhead on short cycles.
	const reuseThreshold = 120 * time.Second

	cfg := config.C.Load()
	interval := time.Duration(cfg.ReputationActiveProbeIntervalSecs) * time.Second
	if interval < minInterval {
		interval = 300 * time.Second
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	slog.Info("active probe job started", "interval_s", int(interval.Seconds()), "enabled", cfg.ReputationActiveProbingEnabled)

	var session *probeSession

	for {
		select {
		case <-ctx.Done():
			if session != nil {
				session.destroy(re)
			}
			return
		case <-ticker.C:
			cfg = config.C.Load()

			newInterval := time.Duration(cfg.ReputationActiveProbeIntervalSecs) * time.Second
			if newInterval < minInterval {
				newInterval = 300 * time.Second
			}
			if newInterval != interval {
				interval = newInterval
				ticker.Reset(interval)
			}

			if !cfg.ReputationActiveProbingEnabled {
				slog.Debug("active probe: disabled, skipping tick")
				if session != nil {
					session.destroy(re)
					session = nil
				}
				continue
			}

			if re.prov == nil || re.spawner == nil || re.probes == nil {
				slog.Warn("active probe: not ready (prov/spawner/probes nil), skipping tick")
				continue
			}

			// Guard: keep at least MinIdleCreds available for demand spikes.
			// When reusing a session the credential is already leased, so skip the guard.
			if session == nil {
				idle := re.prov.creds.AvailableCount()
				min := cfg.ReputationActiveProbeMinIdleCreds
				if idle <= min {
					slog.Info("active probe: skipping — insufficient idle creds", "idle", idle, "min_required", min+1)
					continue
				}
			}

			// Select the next target, excluding all active hostnames except the
			// current session's target so it remains a valid candidate for reuse.
			sessionHostname := ""
			if session != nil {
				sessionHostname = session.TargetHostname
			}
			nextHostname, nextProvider, err := selectProbeTarget(ctx, db, re, cfg, sessionHostname)
			if err != nil {
				slog.Warn("active probe: target selection failed", "err", err)
				if session != nil {
					session.destroy(re)
					session = nil
				}
				continue
			}

			// Decide whether to reuse the current session.
			canReuse := session != nil &&
				strings.EqualFold(session.TargetHostname, nextHostname) &&
				interval < reuseThreshold

			if !canReuse && session != nil {
				slog.Info("active probe: releasing session", "old_target", session.TargetHostname, "next_target", nextHostname)
				session.destroy(re)
				session = nil
			}

			newSession, err := runOneActiveProbe(ctx, db, re, cfg, nextHostname, nextProvider, session)
			if err != nil {
				if newSession != nil {
					newSession.destroy(re)
				} else if session != nil {
					session.destroy(re)
				}
				session = nil
				slog.Warn("active probe: failed", "err", err)
			} else {
				session = newSession
			}
		}
	}
}

// selectProbeTarget picks the next server to probe. excludeSessionHostname is
// the current session's hostname — it is NOT excluded from active hostnames so
// it remains a candidate for reuse comparison.
func selectProbeTarget(ctx context.Context, db *sql.DB, re *ReputationEngine, cfg *config.Config, excludeSessionHostname string) (hostname, provider string, err error) {
	activeHostnames := map[string]bool{}
	for _, n := range state.Global.ListVPNNodes() {
		if n.AssignedHostname == "" {
			continue
		}
		hn := strings.ToLower(n.AssignedHostname)
		// Keep the current session's hostname visible to candidates so we can
		// detect same-target and reuse the session.
		if strings.EqualFold(hn, excludeSessionHostname) {
			continue
		}
		activeHostnames[hn] = true
	}

	candidates, err := persistence.GetScoredCandidates(ctx, db, "_overall", activeHostnames)
	if err != nil || len(candidates) == 0 {
		return "", "", fmt.Errorf("no candidates: %w", err)
	}

	availableCreds := re.prov.creds.AvailableCredentials()
	catalogFile := effectiveCatalogFile(map[string]interface{}{})
	allowedHostnames := make(map[string]string) // hostname -> provider
	for _, cred := range availableCreds {
		p := resolveProvider("", map[string]interface{}{}, cred, cfg.VPNProvider)
		regions := resolveRegions(nil, map[string]interface{}{}, cred, cfg.VPNRegions)
		for _, s := range re.candidateServers(p, regions, "", false, catalogFile) {
			hn := strings.ToLower(strings.TrimSpace(strVal(s["hostname"])))
			if hn != "" {
				allowedHostnames[hn] = p
			}
		}
	}
	// Always include the session hostname so it can be reused even though its
	// credential is already leased (not in AvailableCredentials).
	if excludeSessionHostname != "" {
		if _, ok := allowedHostnames[strings.ToLower(excludeSessionHostname)]; !ok {
			// Best-effort: include it with empty provider so reuse check still fires.
			allowedHostnames[strings.ToLower(excludeSessionHostname)] = ""
		}
	}

	var eligible []persistence.ScoredServer
	for _, c := range candidates {
		if _, ok := allowedHostnames[strings.ToLower(c.Hostname)]; ok {
			eligible = append(eligible, c)
		}
	}
	if len(eligible) == 0 {
		return "", "", fmt.Errorf("no candidates reachable by available credentials")
	}

	target := eligible[0]
	for _, c := range eligible[1:] {
		if !c.Pinned && c.ProbesN < target.ProbesN {
			target = c
		}
	}
	if target.Hostname == "" {
		return "", "", fmt.Errorf("no suitable target server")
	}

	p := allowedHostnames[strings.ToLower(target.Hostname)]
	return target.Hostname, p, nil
}

// runOneActiveProbe executes one probe cycle. If existingSession is non-nil the
// VPN node and engine are reused (no provision/spawn/wait overhead).
// Returns the session to persist across ticks on success, nil on failure.
func runOneActiveProbe(ctx context.Context, db *sql.DB, re *ReputationEngine, cfg *config.Config, targetHostname, targetProvider string, existingSession *probeSession) (*probeSession, error) {
	// Pick a recent content ID to probe.
	contentIDs, err := persistence.GetRecentProbedContentIDs(ctx, db, 24, 20)
	if err != nil || len(contentIDs) == 0 {
		return existingSession, fmt.Errorf("no recent content IDs: %w", err)
	}
	contentID := contentIDs[rand.Intn(len(contentIDs))]

	var sess *probeSession

	if existingSession != nil {
		slog.Info("active probe: reusing session", "target", targetHostname, "content_id", contentID)
		sess = existingSession
	} else {
		slog.Info("active probe: starting", "target", targetHostname, "provider", targetProvider, "content_id", contentID)

		vpnResult, err := re.prov.ProvisionNodeForProbe(ctx, targetHostname, targetProvider)
		if err != nil {
			return nil, fmt.Errorf("provision vpn: %w", err)
		}

		engineName, engineHTTPPort, engineAPIPort, engineContainerID, err := re.spawner.SpawnProbeEngine(ctx, vpnResult.ContainerName)
		if err != nil {
			_ = re.prov.DestroyNode(context.Background(), vpnResult.ContainerName)
			return nil, fmt.Errorf("spawn engine: %w", err)
		}

		if err := waitForEngineAPI(ctx, engineName, engineHTTPPort, 60*time.Second); err != nil {
			_ = re.spawner.StopEngine(context.Background(), engineContainerID)
			_ = re.prov.DestroyNode(context.Background(), vpnResult.ContainerName)
			return nil, fmt.Errorf("engine not ready: %w", err)
		}

		sess = &probeSession{
			TargetHostname:    targetHostname,
			TargetProvider:    targetProvider,
			VPNResult:         vpnResult,
			EngineName:        engineName,
			EngineHTTPPort:    engineHTTPPort,
			EngineAPIPort:     engineAPIPort,
			EngineContainerID: engineContainerID,
		}
	}

	// Update active probe marker on the VPN node.
	if node, ok := state.Global.GetVPNNode(sess.VPNResult.ContainerName); ok {
		node.ActiveProbe = &state.ActiveProbeInfo{
			ContentID:  contentID,
			StartedAt:  time.Now().UTC(),
			TargetHost: targetHostname,
		}
		state.Global.UpsertVPNNode(node)
	}

	client := aceapi.New(sess.EngineName, sess.EngineAPIPort)
	if err := client.Connect(); err != nil {
		return nil, fmt.Errorf("aceapi connect: %w", err)
	}
	defer client.Close()

	probeStart := time.Now()
	startInfo, err := client.LoadAndStart(contentID, "content_id", "0", 0)
	if err != nil {
		serverID, _ := re.ResolveServerID(ctx, sourceForProvider(sess.VPNResult.Provider), targetHostname)
		elapsed := int(time.Since(probeStart).Milliseconds())
		re.probes.Record(persistence.VPNProbeRow{
			ServerID:   serverID,
			ContentID:  contentID,
			Category:   "_overall",
			StartedAt:  probeStart,
			Outcome:    "engine_error",
			DurationMs: &elapsed,
			Meta:       map[string]any{"probe": true},
		})
		return nil, fmt.Errorf("LoadAndStart: %w", err)
	}

	maxDur := time.Duration(cfg.ReputationActiveProbeMaxSecs) * time.Second
	ttfbMs, durationMs, outcome := drainProbeStream(ctx, startInfo.PlaybackURL, maxDur)

	_ = client.StopPlayback()

	serverID, _ := re.ResolveServerID(ctx, sourceForProvider(sess.VPNResult.Provider), targetHostname)
	re.probes.Record(persistence.VPNProbeRow{
		ServerID:   serverID,
		ContentID:  contentID,
		Category:   "_overall",
		StartedAt:  probeStart,
		Outcome:    outcome,
		TtfbMs:     ttfbMs,
		DurationMs: durationMs,
		Meta:       map[string]any{"probe": true},
	})

	slog.Info("active probe: done",
		"target", targetHostname,
		"content_id", contentID,
		"outcome", outcome,
		"ttfb_ms", ttfbMs,
		"duration_ms", durationMs,
		"session_reused", existingSession != nil,
	)

	return sess, nil
}

// drainProbeStream fetches the HLS playback URL and reads data up to maxDur.
// Returns TTFB in ms, total duration in ms, and an outcome string.
func drainProbeStream(ctx context.Context, playbackURL string, maxDur time.Duration) (ttfbMs *int, durationMs *int, outcome string) {
	if playbackURL == "" {
		o := "engine_error"
		return nil, nil, o
	}

	reqCtx, cancel := context.WithTimeout(ctx, maxDur)
	defer cancel()

	req, err := http.NewRequestWithContext(reqCtx, http.MethodGet, playbackURL, nil)
	if err != nil {
		o := "engine_error"
		return nil, nil, o
	}

	start := time.Now()
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		o := "timeout"
		if strings.Contains(err.Error(), "context canceled") || strings.Contains(err.Error(), "context deadline exceeded") {
			o = "timeout"
		}
		return nil, nil, o
	}
	defer resp.Body.Close()

	buf := make([]byte, 32*1024)
	firstByte := false
	var ttfb int
	for {
		n, err := resp.Body.Read(buf)
		if n > 0 && !firstByte {
			ttfb = int(time.Since(start).Milliseconds())
			firstByte = true
		}
		if err == io.EOF || err != nil {
			break
		}
	}

	dur := int(time.Since(start).Milliseconds())
	if !firstByte {
		o := "peer_starved"
		return nil, &dur, o
	}

	return &ttfb, &dur, "success"
}

// waitForEngineAPI polls the engine's API port until it responds or times out.
func waitForEngineAPI(ctx context.Context, host string, port int, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	url := fmt.Sprintf("http://%s:%d/webui/api/service?method=get_version", host, port)
	for time.Now().Before(deadline) {
		req, _ := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		resp, err := http.DefaultClient.Do(req)
		if err == nil {
			resp.Body.Close()
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(2 * time.Second):
		}
	}
	return fmt.Errorf("engine API not ready after %s", timeout)
}

// jobDailyHistorySnapshot appends today's score to history_30, daily at 00:05 UTC.
func jobDailyHistorySnapshot(ctx context.Context, db *sql.DB) {
	for {
		now := time.Now().UTC()
		next := time.Date(now.Year(), now.Month(), now.Day()+1, 0, 5, 0, 0, time.UTC)
		select {
		case <-ctx.Done():
			return
		case <-time.After(time.Until(next)):
			cfg := config.C.Load()
			if !cfg.ReputationEnabled {
				continue
			}
			if err := persistence.SnapshotHistory(ctx, db); err != nil {
				slog.Warn("history snapshot failed", "err", err)
			} else {
				slog.Info("VPN reputation history snapshot done")
			}
		}
	}
}
