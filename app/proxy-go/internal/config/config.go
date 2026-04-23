package config

import (
	"bytes"
	"os"
	"strconv"
	"strings"
	"time"
)

// C holds all proxy configuration, loaded once from environment at startup.
var C = load()

type Config struct {
	// Redis
	RedisHost string
	RedisPort int
	RedisDB   int

	// Timeouts
	UpstreamConnectTimeout time.Duration
	UpstreamReadTimeout    time.Duration
	ClientWaitTimeout      time.Duration
	StreamTimeout          time.Duration
	ChunkTimeout           time.Duration

	// Buffer
	BufferChunkSize    int // bytes, aligned to TS packet size
	InitialBehindChunks int
	RedisChunkTTL      time.Duration

	// Cleanup
	CleanupInterval      time.Duration
	CleanupCheckInterval time.Duration

	// Heartbeat / client TTL
	ClientHeartbeatInterval time.Duration
	ClientRecordTTL         time.Duration
	KeepaliveInterval       time.Duration

	// Shutdown / grace
	ChannelShutdownDelay   time.Duration
	ChannelInitGracePeriod time.Duration

	// Starvation detection
	NoDataTimeoutChecks  int
	NoDataCheckInterval  time.Duration

	// Initial data wait
	InitialDataWaitTimeout   time.Duration
	InitialDataCheckInterval time.Duration

	// Pacing
	PacingBurstSeconds       float64
	PacingBitrateMultiplier  float64
	ProxyMaxCatchupMultiplier float64
	ProxyPrebufferSeconds    int

	// Retries
	MaxRetries        int
	RetryWaitInterval time.Duration

	// Stream / control mode
	StreamMode  string
	ControlMode string

	// HLS
	HLSMaxSegments          int
	HLSInitialSegments      int
	HLSWindowSize           int
	HLSBufferReadyTimeout   time.Duration
	HLSFirstSegmentTimeout  time.Duration
	HLSInitialBufferSeconds int
	HLSMaxInitialSegments   int
	HLSSegmentFetchInterval float64
	HLSClientIdleTimeout    time.Duration

	// Auth
	APIKey string

	// Orchestrator internal API base URL (for engine list, failover signals)
	OrchestratorURL string

	// Listen address for the Go proxy HTTP server
	ListenAddr string

	// Max clients per stream
	MaxClientsPerStreamCount int

	// Global limits
	MaxTotalStreams int
	MaxMemoryMB     int
}

func (c *Config) MaxClientsPerStream() int {
	if c.MaxClientsPerStreamCount <= 0 {
		return 100
	}
	return c.MaxClientsPerStreamCount
}

func load() *Config {
	tsPacketSize := 188
	rawChunk := envInt("BUFFER_CHUNK_SIZE", tsPacketSize*5644) // ~1MB
	aligned := (rawChunk / tsPacketSize) * tsPacketSize

	return &Config{
		RedisHost: envStr("REDIS_HOST", "localhost"),
		RedisPort: envInt("REDIS_PORT", 6379),
		RedisDB:   envInt("REDIS_DB", 0),

		UpstreamConnectTimeout: envDuration("UPSTREAM_CONNECT_TIMEOUT_S", 3*time.Second),
		UpstreamReadTimeout:    envDuration("UPSTREAM_READ_TIMEOUT_S", 90*time.Second),
		ClientWaitTimeout:      envDuration("CLIENT_WAIT_TIMEOUT_S", 30*time.Second),
		StreamTimeout:          envDuration("STREAM_TIMEOUT_S", 60*time.Second),
		ChunkTimeout:           envDuration("CHUNK_TIMEOUT_S", 5*time.Second),

		BufferChunkSize:     aligned,
		InitialBehindChunks: envInt("INITIAL_BEHIND_CHUNKS", 4),
		RedisChunkTTL:       envDuration("REDIS_CHUNK_TTL_S", 60*time.Second),

		CleanupInterval:      envDuration("CLEANUP_INTERVAL_S", 5*time.Second),
		CleanupCheckInterval: envDuration("CLEANUP_CHECK_INTERVAL_S", 2*time.Second),

		ClientHeartbeatInterval: envDuration("CLIENT_HEARTBEAT_INTERVAL_S", 10*time.Second),
		ClientRecordTTL:         envDuration("CLIENT_RECORD_TTL_S", 60*time.Second),
		KeepaliveInterval:       envDuration("KEEPALIVE_INTERVAL_MS", 500*time.Millisecond),

		ChannelShutdownDelay:   envDuration("CHANNEL_SHUTDOWN_DELAY_S", 5*time.Second),
		ChannelInitGracePeriod: envDuration("CHANNEL_INIT_GRACE_PERIOD_S", 30*time.Second),

		NoDataTimeoutChecks: envInt("NO_DATA_TIMEOUT_CHECKS", 60),
		NoDataCheckInterval: envDuration("NO_DATA_CHECK_INTERVAL_S", 1*time.Second),

		InitialDataWaitTimeout:   envDuration("INITIAL_DATA_WAIT_TIMEOUT_S", 10*time.Second),
		InitialDataCheckInterval: envDuration("INITIAL_DATA_CHECK_INTERVAL_MS", 200*time.Millisecond),

		PacingBurstSeconds:        envFloat("PACING_BURST_SECONDS", 15.0),
		PacingBitrateMultiplier:   envFloat("PACING_BITRATE_MULTIPLIER", 1.5),
		ProxyMaxCatchupMultiplier: envFloat("PROXY_MAX_CATCHUP_MULTIPLIER", 2.0),
		ProxyPrebufferSeconds:     envInt("PROXY_PREBUFFER_SECONDS", 3),

		MaxRetries:        envInt("MAX_RETRIES", 3),
		RetryWaitInterval: envDuration("RETRY_WAIT_INTERVAL_MS", 500*time.Millisecond),

		StreamMode:  envStr("STREAM_MODE", "TS"),
		ControlMode: envStr("CONTROL_MODE", "api"),

		HLSMaxSegments:          envInt("HLS_MAX_SEGMENTS", 20),
		HLSInitialSegments:      envInt("HLS_INITIAL_SEGMENTS", 3),
		HLSWindowSize:           envInt("HLS_WINDOW_SIZE", 6),
		HLSBufferReadyTimeout:   envDuration("HLS_BUFFER_READY_TIMEOUT_S", 30*time.Second),
		HLSFirstSegmentTimeout:  envDuration("HLS_FIRST_SEGMENT_TIMEOUT_S", 30*time.Second),
		HLSInitialBufferSeconds: envInt("HLS_INITIAL_BUFFER_SECONDS", 10),
		HLSMaxInitialSegments:   envInt("HLS_MAX_INITIAL_SEGMENTS", 10),
		HLSSegmentFetchInterval: envFloat("HLS_SEGMENT_FETCH_INTERVAL", 0.5),
		HLSClientIdleTimeout:    envDuration("HLS_CLIENT_IDLE_TIMEOUT_S", 10*time.Second),

		APIKey:          envStr("API_KEY", ""),
		OrchestratorURL: envStr("ORCHESTRATOR_URL", "http://localhost:8001"),
		ListenAddr:      envStr("PROXY_LISTEN_ADDR", ":8000"),

		MaxClientsPerStreamCount: envInt("MAX_CLIENTS_PER_STREAM", 100),

		MaxTotalStreams: envInt("MAX_TOTAL_STREAMS", 150),
		MaxMemoryMB:     envInt("MAX_MEMORY_MB", dynamicMemoryLimitMB(2048)),
	}
}

func envStr(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func envInt(key string, def int) int {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envFloat(key string, def float64) float64 {
	if v := os.Getenv(key); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func envDuration(key string, def time.Duration) time.Duration {
	if v := os.Getenv(key); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
		// Also accept plain integer seconds for backwards compat
		if n, err := strconv.Atoi(v); err == nil {
			return time.Duration(n) * time.Second
		}
	}
	return def
}

func dynamicMemoryLimitMB(fallback int) int {
	// Try cgroups v2
	if data, err := os.ReadFile("/sys/fs/cgroup/memory.max"); err == nil {
		s := strings.TrimSpace(string(data))
		if s != "max" {
			if v, err := strconv.ParseInt(s, 10, 64); err == nil {
				return int(float64(v/1024/1024) * 0.85) // 85% of container limit
			}
		}
	}

	// Try cgroups v1
	if data, err := os.ReadFile("/sys/fs/cgroup/memory/memory.limit_in_bytes"); err == nil {
		s := strings.TrimSpace(string(data))
		if v, err := strconv.ParseInt(s, 10, 64); err == nil {
			// Ignore artificially high un-bounded limits (e.g. 9223372036854771712)
			if v < 9000000000000000000 {
				return int(float64(v/1024/1024) * 0.85)
			}
		}
	}

	// Try bare-metal Linux
	if data, err := os.ReadFile("/proc/meminfo"); err == nil {
		lines := bytes.Split(data, []byte("\n"))
		for _, line := range lines {
			if bytes.HasPrefix(line, []byte("MemTotal:")) {
				fields := bytes.Fields(line)
				if len(fields) >= 2 {
					if kb, err := strconv.ParseInt(string(fields[1]), 10, 64); err == nil {
						return int(float64(kb/1024) * 0.85)
					}
				}
			}
		}
	}

	return fallback
}
