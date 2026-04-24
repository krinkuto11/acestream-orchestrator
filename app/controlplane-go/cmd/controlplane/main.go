package main

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	goredis "github.com/redis/go-redis/v9"

	"github.com/acestream/controlplane/internal/api"
	"github.com/acestream/controlplane/internal/circuitbreaker"
	"github.com/acestream/controlplane/internal/config"
	"github.com/acestream/controlplane/internal/docker"
	"github.com/acestream/controlplane/internal/engine"
	"github.com/acestream/controlplane/internal/state"
	"github.com/acestream/controlplane/internal/vpn"
)

func main() {
	setupLogger()

	cfg := config.C
	slog.Info("AceStream control plane starting",
		"listen", cfg.ListenAddr,
		"min_replicas", cfg.MinReplicas,
		"max_replicas", cfg.MaxReplicas,
		"vpn_enabled", cfg.VPNEnabled,
	)

	// ── Redis ──────────────────────────────────────────────────────────────
	rdb := goredis.NewClient(&goredis.Options{
		Addr: fmt.Sprintf("%s:%d", cfg.RedisHost, cfg.RedisPort),
		DB:   cfg.RedisDB,
	})

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	pingCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
	if err := rdb.Ping(pingCtx).Err(); err != nil {
		slog.Error("Redis unreachable", "err", err)
		cancel()
		os.Exit(1)
	}
	cancel()

	pub := state.NewRedisPublisher(rdb)

	// ── Core subsystems ────────────────────────────────────────────────────
	cb := circuitbreaker.NewManager(
		cfg.CBFailureThreshold, cfg.CBRecoveryTimeout,
		cfg.CBReplacementThreshold, cfg.CBReplacementTimeout,
	)
	ctrl := engine.NewController(cb)
	hm := engine.NewHealthManager(ctrl)
	mon := docker.NewMonitor(pub, ctrl)
	eventWatcher := docker.NewEventWatcher(pub, ctrl, mon)

	// ── VPN subsystems (optional) ──────────────────────────────────────────
	creds := vpn.NewCredentialManager()
	if f := cfg.VPNCredentialsFile; f != "" {
		if err := creds.LoadFromFile(f); err != nil {
			slog.Warn("VPN credentials file not loaded", "path", f, "err", err)
		} else {
			slog.Info("VPN credentials loaded", "path", f, "total", creds.TotalCount())
		}
	}

	rep := vpn.NewReputationManager(rdb, cfg.ServersJSONDir)
	prov := vpn.NewProvisioner(creds, rep)
	svcRefresh := vpn.NewServersRefreshService(cfg.ServersJSONDir, rep)
	vpnLC := vpn.NewLifecycleManager(pub, prov)

	// Restore credential leases from running Docker containers on startup.
	if cfg.VPNEnabled {
		slog.Info("Restoring VPN credential leases from running containers")
		restoreCtx, restoreCancel := context.WithTimeout(ctx, 15*time.Second)
		nodes, err := prov.ListManagedNodes(restoreCtx, true)
		restoreCancel()
		if err != nil {
			slog.Warn("Failed to list managed VPN nodes on startup", "err", err)
		} else {
			creds.RestoreLeases(nodes)
			slog.Info("VPN leases restored", "nodes", len(nodes))
		}
	}

	// ── Background goroutines ──────────────────────────────────────────────
	go eventWatcher.Run(ctx)
	go mon.Run(ctx)
	go vpnLC.Run(ctx)
	ctrl.Start(ctx)
	hm.Start(ctx)

	if cfg.VPNServersAutoRefresh || cfg.VPNEnabled {
		go svcRefresh.Run(ctx, cfg.VPNServersAutoRefresh, cfg.VPNServersRefreshPeriod)
	}

	// ── Load pollers — nudge controller when counts change ─────────────────
	go pollCounts(ctx, rdb, ctrl)

	// ── HTTP API ───────────────────────────────────────────────────────────
	srv := api.NewServer(ctrl, hm, cb, pub, prov, creds, svcRefresh)
	go func() {
		if err := srv.Start(); err != nil && err != http.ErrServerClosed {
			slog.Error("API server error", "err", err)
		}
	}()

	slog.Info("Control plane ready")

	<-ctx.Done()
	slog.Info("Shutdown signal received; stopping subsystems")

	shutCtx, shutCancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer shutCancel()

	_ = srv.Shutdown(shutCtx)
	hm.Stop()
	ctrl.Stop()

	slog.Info("Control plane stopped")
}

// pollCounts reads stream and monitor counts from Redis and nudges the
// controller whenever anything changes so autoscaling reacts immediately.
func pollCounts(ctx context.Context, rdb *goredis.Client, ctrl *engine.Controller) {
	ticker := time.NewTicker(10 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			st := state.Global
			streamChanged := state.PollStreamCounts(ctx, rdb, st)
			monChanged := state.PollMonitorCounts(ctx, rdb, st)
			if streamChanged || monChanged {
				ctrl.Nudge("load_counts_changed")
			}
		}
	}
}

func setupLogger() {
	level := slog.LevelInfo
	if v := os.Getenv("LOG_LEVEL"); v == "debug" {
		level = slog.LevelDebug
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level})))
}
