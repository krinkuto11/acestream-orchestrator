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

	"github.com/acestream/orchestrator/internal/api"
	"github.com/acestream/orchestrator/internal/bridge"
	"github.com/acestream/orchestrator/internal/config"
	"github.com/acestream/orchestrator/internal/metrics"
	"github.com/acestream/orchestrator/internal/persistence"
	"github.com/acestream/orchestrator/internal/state"
)

func main() {
	setupLogger()

	cfg := config.C
	slog.Info("AceStream orchestrator starting", "listen", cfg.ListenAddr)

	rdb := goredis.NewClient(&goredis.Options{
		Addr: fmt.Sprintf("%s:%d", cfg.RedisHost, cfg.RedisPort),
		DB:   cfg.RedisDB,
	})

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	// ── SQLite settings store ──────────────────────────────────────────────
	db, err := persistence.Open(cfg.DBPath)
	if err != nil {
		slog.Warn("SQLite unavailable; settings will not be persisted", "err", err)
	}
	var settingsStore *persistence.SettingsStore
	if db != nil {
		if s, err := persistence.NewSettingsStore(db); err != nil {
			slog.Warn("SettingsStore init failed", "err", err)
		} else {
			settingsStore = s
		}
	}

	// ── State + Redis bridge ───────────────────────────────────────────────
	st := state.Global
	br := bridge.New(rdb, st)

	if err := br.Bootstrap(ctx); err != nil {
		slog.Warn("RedisBridge bootstrap failed", "err", err)
	}

	go br.Run(ctx)
	go br.RunCountsPublisher(ctx, cfg.CountsPublishInterval)
	go metrics.RunCollector(ctx, st, 5*time.Second)

	srv := api.NewServer(st, br, settingsStore)
	go func() {
		if err := srv.Start(); err != nil && err != http.ErrServerClosed {
			slog.Error("Orchestrator API server error", "err", err)
		}
	}()

	slog.Info("Orchestrator ready")
	<-ctx.Done()
	slog.Info("Shutdown signal received")

	shutCtx, shutCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutCancel()
	_ = srv.Shutdown(shutCtx)

	slog.Info("Orchestrator stopped")
}

func setupLogger() {
	level := slog.LevelInfo
	if os.Getenv("LOG_LEVEL") == "debug" {
		level = slog.LevelDebug
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level})))
}
