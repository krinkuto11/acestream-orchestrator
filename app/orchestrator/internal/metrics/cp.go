package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	CPEnginesTotal = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "cp_engines_total",
		Help: "Total managed engines by health status",
	}, []string{"status"})

	CPDesiredReplicas = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "cp_desired_replicas",
		Help: "Desired engine replica count",
	})

	CPProvisioningTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "cp_provisioning_total",
		Help: "Engine provisioning attempts by result",
	}, []string{"result"})

	CPReconcileTotal = promauto.NewCounter(prometheus.CounterOpts{
		Name: "cp_reconcile_total",
		Help: "Total reconciliation loop executions",
	})

	CPHealthCheckTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "cp_health_check_total",
		Help: "Health probe results by status",
	}, []string{"status"})

	CPVPNNodesTotal = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "cp_vpn_nodes_total",
		Help: "Total VPN nodes by condition",
	}, []string{"condition"})

	CPIntentQueueDepth = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "cp_intent_queue_depth",
		Help: "Number of pending intents in the engine controller queue",
	})

	CPCircuitBreakerOpen = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "cp_circuit_breaker_open",
		Help: "1 if a circuit breaker is open, 0 if closed",
	}, []string{"name"})
)
