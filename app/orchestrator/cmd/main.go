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

	"github.com/acestream/acestream/internal/api"
	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/controlplane/circuitbreaker"
	cpdocker "github.com/acestream/acestream/internal/controlplane/docker"
	cpengine "github.com/acestream/acestream/internal/controlplane/engine"
	vpnpkg "github.com/acestream/acestream/internal/controlplane/vpn"
	"github.com/acestream/acestream/internal/metrics"
	"github.com/acestream/acestream/internal/persistence"
	"github.com/acestream/acestream/internal/proxy/hls"
	proxymonitor "github.com/acestream/acestream/internal/proxy/monitor"
	proxystream "github.com/acestream/acestream/internal/proxy/stream"
	"github.com/acestream/acestream/internal/state"
)

func main() {
	setupLogger()

	cfg := config.C.Load()
	slog.Info("AceStream unified binary starting",
		"orchestrator", cfg.OrchestratorListenAddr,
		"proxy", cfg.ProxyListenAddr,
	)

	// ── Redis ──────────────────────────────────────────────────────────────────
	rdb := goredis.NewClient(&goredis.Options{
		Addr: fmt.Sprintf("%s:%d", cfg.RedisHost, cfg.RedisPort),
		DB:   cfg.RedisDB,
	})

	pingCtx, pingCancel := context.WithTimeout(context.Background(), 5*time.Second)
	if err := rdb.Ping(pingCtx).Err(); err != nil {
		slog.Error("redis connection failed", "err", err)
		os.Exit(1)
	}
	pingCancel()

	// ── SQLite settings store ──────────────────────────────────────────────────
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
			// Rehydrate all persisted settings into the live config atomically,
			// so runtime behaviour matches what was last saved in the dashboard.
			if ps := s.Get("proxy_settings"); len(ps) > 0 {
				config.ApplySettings(ps)
			}
			if es := s.Get("engine_settings"); len(es) > 0 {
				config.ApplyEngineSettings(es)
			}
			if os := s.Get("orchestrator_settings"); len(os) > 0 {
				config.ApplyOrchestratorSettings(os)
			}
			if ec := s.Get("engine_config"); len(ec) > 0 {
				config.ApplyEngineConfig(ec)
			}
			if vs := s.Get("vpn_settings"); len(vs) > 0 {
				config.ApplyVPNSettings(vs)
			}
			cfg = config.C.Load()
			slog.Info("persisted settings applied to live config")
		}
	}

	// ── Docker Network Detection ───────────────────────────────────────────────
	if cfg.DockerNetwork == "" {
		if net := cpdocker.DetectSelfNetwork(context.Background()); net != "" {
			slog.Info("Docker network not specified; detected self network", "network", net)
			config.UpdateDockerNetwork(net)
			cfg = config.C.Load()
		}
	}

	// ── Application context ────────────────────────────────────────────────────
	appCtx, appCancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer appCancel()

	// ── State + Redis publisher ────────────────────────────────────────────────
	st := state.Global
	pub := state.NewRedisPublisher(rdb)

	// ── Controlplane: circuit breaker + engine controller ─────────────────────
	cb := circuitbreaker.NewManager(
		cfg.CBFailureThreshold, cfg.CBRecoveryTimeout,
		cfg.CBReplacementThreshold, cfg.CBReplacementTimeout,
	)
	ctrl := cpengine.NewController(cb)

	// ── Controlplane: VPN subsystem ────────────────────────────────────────────
	var (
		creds      *vpnpkg.CredentialManager
		rep        *vpnpkg.ReputationManager
		prov       *vpnpkg.Provisioner
		svcRefresh *vpnpkg.ServersRefreshService
	)
	// ── VPN subsystems ────────────────────────────────────────────────────────
	serversDir := cfg.ServersJSONDir
	if serversDir == "" {
		serversDir = "/app/app/config/vpn-servers"
	}
	creds = vpnpkg.NewCredentialManager()
	if settingsStore != nil {
		vpnSettings := settingsStore.Get("vpn_settings")
		if rawCreds, ok := vpnSettings["credentials"].([]any); ok {
			credList := make([]map[string]interface{}, 0, len(rawCreds))
			for _, c := range rawCreds {
				if m, ok := c.(map[string]interface{}); ok {
					credList = append(credList, m)
				}
			}
			if err := creds.Configure(credList); err != nil {
				slog.Warn("VPN credential configure failed", "err", err)
			}
		}
	}
	rep = vpnpkg.NewReputationManager(rdb, serversDir)
	prov = vpnpkg.NewProvisioner(creds, rep)
	svcRefresh = vpnpkg.NewServersRefreshService(serversDir, rep)
	go svcRefresh.Run(appCtx, cfg.VPNServersAutoRefresh, cfg.VPNServersRefreshPeriod)

	vpnManager := vpnpkg.NewLifecycleManager(pub, prov)
	vpnManager.SetNudger(ctrl.Nudge)
	go vpnManager.Run(appCtx)

	// ── Controlplane: Docker monitor + event watcher ───────────────────────────
	dockerMon := cpdocker.NewMonitor(pub, ctrl)
	eventWatcher := cpdocker.NewEventWatcher(pub, ctrl, dockerMon)

	// Bootstrap Docker state before starting the controller.
	// Reindex logs its own errors internally; the bool only indicates whether any
	// containers were found, so we discard it here.
	cpdocker.Reindex(appCtx)

	// Restore VPN leases from discovered Docker state.
	if creds != nil && prov != nil {
		nodes, _ := prov.ListManagedNodes(appCtx, false)
		creds.RestoreLeases(nodes)
	}

	// Clean up any managed containers left from a previous run before the
	// lifecycle manager starts. This prevents config-drift replacement churn and
	// VPN health-check failures that would block engine provisioning.
	cleanupCtx, cleanupCancel := context.WithTimeout(context.Background(), 30*time.Second)
	cpengine.CleanupManaged(cleanupCtx, prov)
	cleanupCancel()

	ctrl.Start(appCtx)
	ctrl.EnsureMinimum()

	go dockerMon.Run(appCtx)
	go eventWatcher.Run(appCtx)

	// ── Proxy plane ────────────────────────────────────────────────────────────
	sink := &stateSink{st: st}
	hub := proxystream.NewHub(rdb, sink)
	monSvc := proxymonitor.New(rdb, st, settingsStore)

	go monSvc.RunCountsPublisher(appCtx, 5*time.Second)
	go metrics.RunCollector(appCtx, st, 5*time.Second)

	proxySrv := api.NewProxyServer(hub, monSvc, st, settingsStore, ctrl, cb, pub, prov, creds, svcRefresh, vpnManager)
	proxyHTTP := &http.Server{
		Addr:    cfg.ProxyListenAddr,
		Handler: proxySrv,
	}

	// ── Orchestrator plane ─────────────────────────────────────────────────────
	orchSrv := api.NewOrchestratorServer(st, settingsStore)

	// ── Start HTTP servers ─────────────────────────────────────────────────────
	go func() {
		slog.Info("proxy listening", "addr", cfg.ProxyListenAddr)
		if err := proxyHTTP.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			slog.Error("proxy server error", "err", err)
			appCancel()
		}
	}()
	go proxySrv.RunSSEPublisher(appCtx)

	go func() {
		if err := orchSrv.Start(); err != nil && err != http.ErrServerClosed {
			slog.Error("orchestrator server error", "err", err)
			appCancel()
		}
	}()

	slog.Info("AceStream unified binary ready")
	<-appCtx.Done()
	slog.Info("shutdown signal received")

	ctrl.Stop()
	monSvc.StopAll()
	hub.Stop()
	hls.DefaultCache.Stop()
 
	// Ensure all managed containers are stopped on exit.
	stopCtx, stopCancel := context.WithTimeout(context.Background(), 20*time.Second)
	cpengine.StopAllManaged(stopCtx)
	stopCancel()

	shutCtx, shutCancel := context.WithTimeout(context.Background(), 10*time.Second)
	defer shutCancel()

	if err := proxyHTTP.Shutdown(shutCtx); err != nil {
		slog.Error("proxy graceful shutdown failed", "err", err)
	}
	if err := orchSrv.Shutdown(shutCtx); err != nil {
		slog.Error("orchestrator graceful shutdown failed", "err", err)
	}

	slog.Info("AceStream stopped")
}

// stateSink wires proxy stream lifecycle events directly into the unified state store.
type stateSink struct {
	st *state.Store
}

func (s *stateSink) OnStreamStarted(contentID, engineID string) {
	s.st.OnStreamStarted(state.StreamStartedEvent{
		ContentID: contentID,
		EngineID:  engineID,
	})
}

func (s *stateSink) OnStreamEnded(contentID string) {
	s.st.OnStreamEnded(state.StreamEndedEvent{ContentID: contentID})
}

func setupLogger() {
	level := slog.LevelInfo
	if os.Getenv("LOG_LEVEL") == "debug" {
		level = slog.LevelDebug
	}
	slog.SetDefault(slog.New(slog.NewJSONHandler(os.Stdout, &slog.HandlerOptions{Level: level})))
}
