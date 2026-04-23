package config

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"net/http"
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"time"

	"github.com/redis/go-redis/v9"
)

// C holds all proxy configuration, loaded once from environment at startup
// and updated dynamically via Orchestrator API and Redis.
var C atomic.Pointer[Config]

func init() {
	C.Store(load())
}

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
	BufferChunkSize     int // bytes, aligned to TS packet size
	InitialBehindChunks int
	RedisChunkTTL       time.Duration

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
	PacingBurstSeconds        float64
	PacingBitrateMultiplier   float64
	ProxyMaxCatchupMultiplier float64
	ProxyPrebufferSeconds     int

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

// UpdateFromAPI fetches the current configuration from the Orchestrator.
func UpdateFromAPI(orchestratorURL string) error {
	url := fmt.Sprintf("%s/proxy/config", orchestratorURL)
	slog.Info("Fetching configuration from Orchestrator", "url", url)

	var lastErr error
	for i := 0; i < 10; i++ {
		ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
		if err != nil {
			cancel()
			return err
		}

		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			lastErr = err
			cancel()
			time.Sleep(1 * time.Second)
			continue
		}
		defer resp.Body.Close()

		if resp.StatusCode != http.StatusOK {
			lastErr = fmt.Errorf("orchestrator returned status %d", resp.StatusCode)
			cancel()
			time.Sleep(1 * time.Second)
			continue
		}

		var updates map[string]interface{}
		if err := json.NewDecoder(resp.Body).Decode(&updates); err != nil {
			cancel()
			return err
		}

		ApplyUpdates(updates)
		slog.Info("Configuration updated from API")
		cancel()
		return nil
	}

	return fmt.Errorf("failed to fetch config after retries: %v", lastErr)
}

// SubscribeRedisUpdates listens for configuration changes on a Redis channel.
func SubscribeRedisUpdates(rdb *redis.Client) {
	ctx := context.Background()
	pubsub := rdb.Subscribe(ctx, "proxy_config_updates")

	go func() {
		defer pubsub.Close()
		slog.Info("Subscribed to proxy_config_updates Redis channel")

		for {
			msg, err := pubsub.ReceiveMessage(ctx)
			if err != nil {
				slog.Error("Redis Pub/Sub error", "err", err)
				time.Sleep(5 * time.Second)
				continue
			}

			var updates map[string]interface{}
			if err := json.Unmarshal([]byte(msg.Payload), &updates); err != nil {
				slog.Error("Failed to unmarshal config update from Redis", "err", err)
				continue
			}

			ApplyUpdates(updates)
			slog.Info("Configuration updated via Redis Pub/Sub")
		}
	}()
}

// ApplyUpdates maps JSON fields to Config fields and applies them safely.
func ApplyUpdates(updates map[string]interface{}) {
	// Start with a copy of current config
	old := C.Load()
	newCfg := *old

	for k, v := range updates {
		switch k {
		case "initial_data_wait_timeout":
			newCfg.InitialDataWaitTimeout = toDuration(v, time.Second)
		case "initial_data_check_interval":
			newCfg.InitialDataCheckInterval = toDuration(v, time.Second)
		case "no_data_timeout_checks":
			newCfg.NoDataTimeoutChecks = toInt(v)
		case "no_data_check_interval":
			newCfg.NoDataCheckInterval = toDuration(v, time.Second)
		case "connection_timeout":
			newCfg.ClientWaitTimeout = toDuration(v, time.Second)
		case "upstream_connect_timeout":
			newCfg.UpstreamConnectTimeout = toDuration(v, time.Second)
		case "upstream_read_timeout":
			newCfg.UpstreamReadTimeout = toDuration(v, time.Second)
		case "stream_timeout":
			newCfg.StreamTimeout = toDuration(v, time.Second)
		case "channel_shutdown_delay":
			newCfg.ChannelShutdownDelay = toDuration(v, time.Second)
		case "proxy_prebuffer_seconds":
			newCfg.ProxyPrebufferSeconds = toInt(v)
		case "stream_mode":
			if s, ok := v.(string); ok {
				newCfg.StreamMode = strings.ToUpper(s)
			}
		case "control_mode":
			if s, ok := v.(string); ok {
				newCfg.ControlMode = strings.ToLower(s)
			}
		case "hls_max_segments":
			newCfg.HLSMaxSegments = toInt(v)
		case "hls_initial_segments":
			newCfg.HLSInitialSegments = toInt(v)
		case "hls_window_size":
			newCfg.HLSWindowSize = toInt(v)
		case "hls_buffer_ready_timeout":
			newCfg.HLSBufferReadyTimeout = toDuration(v, time.Second)
		case "hls_first_segment_timeout":
			newCfg.HLSFirstSegmentTimeout = toDuration(v, time.Second)
		case "hls_initial_buffer_seconds":
			newCfg.HLSInitialBufferSeconds = toInt(v)
		case "hls_max_initial_segments":
			newCfg.HLSMaxInitialSegments = toInt(v)
		case "hls_segment_fetch_interval":
			newCfg.HLSSegmentFetchInterval = toFloat(v)
		}
	}

	C.Store(&newCfg)
}



func toDuration(v interface{}, unit time.Duration) time.Duration {
	switch val := v.(type) {
	case float64:
		return time.Duration(val * float64(unit))
	case int:
		return time.Duration(val) * unit
	case string:
		if d, err := time.ParseDuration(val); err == nil {
			return d
		}
		if n, err := strconv.Atoi(val); err == nil {
			return time.Duration(n) * unit
		}
	}
	return 0
}

func toInt(v interface{}) int {
	switch val := v.(type) {
	case float64:
		return int(val)
	case int:
		return val
	case string:
		n, _ := strconv.Atoi(val)
		return n
	}
	return 0
}

func toFloat(v interface{}) float64 {
	switch val := v.(type) {
	case float64:
		return val
	case int:
		return float64(val)
	case string:
		f, _ := strconv.ParseFloat(val, 64)
		return f
	}
	return 0
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
