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
func jobActiveProbe(ctx context.Context, db *sql.DB, re *ReputationEngine) {
	const minInterval = 60 * time.Second
	cfg := config.C.Load()
	interval := time.Duration(cfg.ReputationActiveProbeIntervalSecs) * time.Second
	if interval < minInterval {
		interval = 300 * time.Second
	}
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	slog.Info("active probe job started", "interval_s", int(interval.Seconds()), "enabled", cfg.ReputationActiveProbingEnabled)

	for {
		select {
		case <-ctx.Done():
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
				continue
			}

			if re.prov == nil || re.spawner == nil || re.probes == nil {
				slog.Warn("active probe: not ready (prov/spawner/probes nil), skipping tick")
				continue
			}

			// Guard: keep at least MinIdleCreds available for demand spikes.
			idle := re.prov.creds.AvailableCount()
			min := cfg.ReputationActiveProbeMinIdleCreds
			if idle <= min {
				slog.Info("active probe: skipping — insufficient idle creds", "idle", idle, "min_required", min+1)
				continue
			}

			if err := runOneActiveProbe(ctx, db, re, cfg); err != nil {
				slog.Warn("active probe: failed", "err", err)
			}
		}
	}
}

func runOneActiveProbe(ctx context.Context, db *sql.DB, re *ReputationEngine, cfg *config.Config) error {
	// Pick the underprobed target: lowest probes_n server not currently active.
	activeHostnames := map[string]bool{}
	for _, n := range state.Global.ListVPNNodes() {
		if n.AssignedHostname != "" {
			activeHostnames[strings.ToLower(n.AssignedHostname)] = true
		}
	}

	candidates, err := persistence.GetScoredCandidates(ctx, db, "_overall", activeHostnames)
	if err != nil || len(candidates) == 0 {
		return fmt.Errorf("no candidates: %w", err)
	}

	// Filter candidates to servers reachable by the available credentials,
	// respecting each credential's provider and regions. This prevents using
	// ProtonVPN credentials against TorGuard servers (or mismatched regions).
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

	var eligible []persistence.ScoredServer
	for _, c := range candidates {
		if _, ok := allowedHostnames[strings.ToLower(c.Hostname)]; ok {
			eligible = append(eligible, c)
		}
	}
	if len(eligible) == 0 {
		return fmt.Errorf("no candidates reachable by available credentials")
	}

	// Find the candidate with the fewest probes (not pinned, not active).
	target := eligible[0]
	for _, c := range eligible[1:] {
		if !c.Pinned && c.ProbesN < target.ProbesN {
			target = c
		}
	}
	if target.Hostname == "" {
		return fmt.Errorf("no suitable target server")
	}

	targetProvider := allowedHostnames[strings.ToLower(target.Hostname)]

	// Pick a recent content ID to probe.
	contentIDs, err := persistence.GetRecentProbedContentIDs(ctx, db, 24, 20)
	if err != nil || len(contentIDs) == 0 {
		return fmt.Errorf("no recent content IDs: %w", err)
	}
	contentID := contentIDs[rand.Intn(len(contentIDs))]

	slog.Info("active probe: starting", "target", target.Hostname, "provider", targetProvider, "content_id", contentID)

	// Provision the probe VPN node.
	vpnResult, err := re.prov.ProvisionNodeForProbe(ctx, target.Hostname, targetProvider)
	if err != nil {
		return fmt.Errorf("provision vpn: %w", err)
	}

	// Mark active probe on the VPN node state.
	if node, ok := state.Global.GetVPNNode(vpnResult.ContainerName); ok {
		node.ActiveProbe = &state.ActiveProbeInfo{
			ContentID:  contentID,
			StartedAt:  time.Now().UTC(),
			TargetHost: target.Hostname,
		}
		state.Global.UpsertVPNNode(node)
	}

	cleanup := func() {
		// Clear probe marker before removing node.
		if node, ok := state.Global.GetVPNNode(vpnResult.ContainerName); ok {
			node.ActiveProbe = nil
			state.Global.UpsertVPNNode(node)
		}
		if err := re.prov.DestroyNode(context.Background(), vpnResult.ContainerName); err != nil {
			slog.Warn("active probe: destroy vpn node failed", "err", err)
		}
	}

	// Provision probe engine on the VPN node.
	engineName, apiPort, engineContainerID, err := re.spawner.SpawnProbeEngine(ctx, vpnResult.ContainerName)
	if err != nil {
		cleanup()
		return fmt.Errorf("spawn engine: %w", err)
	}
	engineCleanup := func() {
		if err := re.spawner.StopEngine(context.Background(), engineContainerID); err != nil {
			slog.Warn("active probe: stop engine failed", "err", err)
		}
		cleanup()
	}

	// Wait briefly for the engine API port to become ready (up to 30 s).
	engineHost := engineName
	if err := waitForEngineAPI(ctx, engineHost, apiPort, 30*time.Second); err != nil {
		engineCleanup()
		return fmt.Errorf("engine not ready: %w", err)
	}

	// Connect via aceapi telnet client and run the probe stream.
	client := aceapi.New(engineHost, apiPort)
	if err := client.Connect(); err != nil {
		engineCleanup()
		return fmt.Errorf("aceapi connect: %w", err)
	}
	defer client.Close()

	probeStart := time.Now()
	startInfo, err := client.LoadAndStart(contentID, "content_id", "0", 0)
	if err != nil {
		// Record a failed probe outcome.
		serverID, _ := re.ResolveServerID(ctx, vpnResult.Provider, target.Hostname)
		outcome := "engine_error"
		elapsed := int(time.Since(probeStart).Milliseconds())
		re.probes.Record(persistence.VPNProbeRow{
			ServerID:  serverID,
			ContentID: contentID,
			Category:  "_overall",
			StartedAt: probeStart,
			Outcome:   outcome,
			DurationMs: &elapsed,
			Meta:      map[string]any{"probe": true},
		})
		engineCleanup()
		return fmt.Errorf("LoadAndStart: %w", err)
	}

	// Drain HLS stream to measure TTFB and sustained delivery.
	maxDur := time.Duration(cfg.ReputationActiveProbeMaxSecs) * time.Second
	ttfbMs, durationMs, outcome := drainProbeStream(ctx, startInfo.PlaybackURL, maxDur)

	_ = client.StopPlayback()

	serverID, _ := re.ResolveServerID(ctx, vpnResult.Provider, target.Hostname)
	row := persistence.VPNProbeRow{
		ServerID:   serverID,
		ContentID:  contentID,
		Category:   "_overall",
		StartedAt:  probeStart,
		Outcome:    outcome,
		TtfbMs:     ttfbMs,
		DurationMs: durationMs,
		Meta:       map[string]any{"probe": true},
	}
	re.probes.Record(row)

	slog.Info("active probe: done",
		"target", target.Hostname,
		"content_id", contentID,
		"outcome", outcome,
		"ttfb_ms", ttfbMs,
		"duration_ms", durationMs,
	)

	engineCleanup()
	return nil
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
