package metrics

import (
	"context"
	"time"

	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"

	"github.com/acestream/orchestrator/internal/state"
)

var (
	activeStreams = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "orch_active_streams",
		Help: "Number of currently active streams.",
	})
	enginesTotal = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "orch_engines_total",
		Help: "Total number of known engines.",
	})
	enginesHealthy = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "orch_engines_healthy",
		Help: "Number of healthy engines.",
	})
	enginesUnhealthy = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "orch_engines_unhealthy",
		Help: "Number of unhealthy engines.",
	})
	enginesDraining = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "orch_engines_draining",
		Help: "Number of draining engines.",
	})
	vpnNodesTotal = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "orch_vpn_nodes_total",
		Help: "Total number of known VPN nodes.",
	})
	vpnNodesHealthy = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "orch_vpn_nodes_healthy",
		Help: "Number of healthy VPN nodes.",
	})
	vpnNodesDraining = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "orch_vpn_nodes_draining",
		Help: "Number of draining VPN nodes.",
	})
	enginesUsed = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "orch_engines_used",
		Help: "Number of engines with at least one active stream.",
	})
)

// RunCollector updates Prometheus gauges on every interval tick until ctx is done.
func RunCollector(ctx context.Context, st *state.Store, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			collect(st)
		}
	}
}

func collect(st *state.Store) {
	streams := st.ListStreams()
	engines := st.ListEngines()
	vpns := st.ListVPNNodes()

	enginesWithStream := make(map[string]bool, len(streams))
	for _, s := range streams {
		if s.EngineID != "" {
			enginesWithStream[s.EngineID] = true
		}
	}

	var healthy, unhealthy, draining int
	for _, e := range engines {
		switch {
		case e.Draining:
			draining++
		case e.HealthStatus == "healthy":
			healthy++
		case e.HealthStatus == "unhealthy":
			unhealthy++
		}
	}

	var vpnHealthy, vpnDraining int
	for _, n := range vpns {
		switch {
		case n.Lifecycle == "draining":
			vpnDraining++
		case n.Healthy:
			vpnHealthy++
		}
	}

	activeStreams.Set(float64(len(streams)))
	enginesTotal.Set(float64(len(engines)))
	enginesHealthy.Set(float64(healthy))
	enginesUnhealthy.Set(float64(unhealthy))
	enginesDraining.Set(float64(draining))
	enginesUsed.Set(float64(len(enginesWithStream)))
	vpnNodesTotal.Set(float64(len(vpns)))
	vpnNodesHealthy.Set(float64(vpnHealthy))
	vpnNodesDraining.Set(float64(vpnDraining))
}
