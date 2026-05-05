package vpn

import (
	"context"
	"database/sql"
	"log/slog"
	"time"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/persistence"
)

// runReputationJobs starts all background reputation maintenance goroutines.
func runReputationJobs(ctx context.Context, db *sql.DB) {
	go jobReputationRefresh(ctx, db)
	go jobAutoQuarantine(ctx, db)
	go jobDailyHistorySnapshot(ctx, db)
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
