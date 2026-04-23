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

	"github.com/redis/go-redis/v9"

	"github.com/acestream/proxy/internal/api"
	"github.com/acestream/proxy/internal/config"
	"github.com/acestream/proxy/internal/hls"
	"github.com/acestream/proxy/internal/stream"
)

func main() {
	slog.SetDefault(slog.New(slog.NewTextHandler(os.Stdout, &slog.HandlerOptions{
		Level: slog.LevelInfo,
	})))

	cfg := config.C.Load()

	rdb := redis.NewClient(&redis.Options{
		Addr: fmt.Sprintf("%s:%d", cfg.RedisHost, cfg.RedisPort),
		DB:   cfg.RedisDB,
	})
	defer rdb.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	if err := rdb.Ping(ctx).Err(); err != nil {
		slog.Error("redis connection failed", "err", err)
		os.Exit(1)
	}
	cancel()

	// 1. Initial fetch from Orchestrator API
	if err := config.UpdateFromAPI(cfg.OrchestratorURL); err != nil {
		slog.Warn("failed to fetch initial config from orchestrator, using env defaults", "err", err)
	}
	
	// Re-load cfg after initial fetch to get the latest values for startup
	cfg = config.C.Load()

	// 2. Subscribe to live updates via Redis
	config.SubscribeRedisUpdates(rdb)

	hub := stream.NewHub(rdb)
	srv := api.NewServer(hub, cfg.OrchestratorURL)

	httpServer := &http.Server{
		Addr:    cfg.ListenAddr,
		Handler: srv,
	}


	quit := make(chan os.Signal, 1)
	signal.Notify(quit, syscall.SIGINT, syscall.SIGTERM)

	go func() {
		slog.Info("proxy listening", "addr", cfg.ListenAddr, "orchestrator", cfg.OrchestratorURL)
		if err := httpServer.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("server error", "err", err)
			os.Exit(1)
		}
	}()

	<-quit
	slog.Info("shutting down")

	hub.Stop()
	hls.DefaultCache.Stop()

	shutdownCtx, shutdownCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutdownCancel()
	if err := httpServer.Shutdown(shutdownCtx); err != nil {
		slog.Error("graceful shutdown failed", "err", err)
	}
}
