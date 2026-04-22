package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	HttpRequestsTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "acestream_proxy_http_requests_total",
		Help: "The total number of HTTP requests processed by the proxy.",
	}, []string{"mode", "endpoint", "status_code"})

	HttpRequestDurationSeconds = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "acestream_proxy_http_request_duration_seconds",
		Help:    "Histogram of response latency (seconds) of HTTP requests.",
		Buckets: prometheus.DefBuckets,
	}, []string{"mode", "endpoint"})

	HttpTtfbSeconds = promauto.NewHistogramVec(prometheus.HistogramOpts{
		Name:    "acestream_proxy_http_ttfb_seconds",
		Help:    "Histogram of Time To First Byte (seconds) of HTTP requests.",
		Buckets: prometheus.DefBuckets,
	}, []string{"mode", "endpoint"})

	ActiveSessions = promauto.NewGaugeVec(prometheus.GaugeOpts{
		Name: "acestream_proxy_active_sessions",
		Help: "The number of active stream sessions.",
	}, []string{"mode"})

	BytesIngressTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "acestream_proxy_bytes_ingress_total",
		Help: "The total number of bytes received from engines.",
	}, []string{"mode"})

	BytesEgressTotal = promauto.NewCounterVec(prometheus.CounterOpts{
		Name: "acestream_proxy_bytes_egress_total",
		Help: "The total number of bytes sent to clients.",
	}, []string{"mode"})
)
