package config

import (
	"os"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
)

// C is the live config, atomically swapped on every settings update.
var C atomic.Pointer[Config]

func init() { C.Store(load()) }

type PortRange struct {
	Min int
	Max int
}

// Config holds all runtime configuration for the unified binary.
type Config struct {
	// ── Infrastructure ───────────────────────────────────────────────────────
	RedisHost string
	RedisPort int
	RedisDB   int
	DBPath    string
	APIKey    string

	// ── Listen addresses ─────────────────────────────────────────────────────
	OrchestratorListenAddr string // :8083
	ProxyListenAddr        string // :8000

	// ── Orchestrator intervals ────────────────────────────────────────────────
	StreamRetentionPeriod time.Duration
	CountsPublishInterval time.Duration

	// ── Proxy: timeouts ──────────────────────────────────────────────────────
	UpstreamConnectTimeout  time.Duration
	UpstreamReadTimeout     time.Duration
	ClientWaitTimeout       time.Duration
	StreamTimeout           time.Duration
	ChunkTimeout            time.Duration
	ChannelShutdownDelay    time.Duration
	ChannelInitGracePeriod  time.Duration
	KeepaliveInterval       time.Duration

	// ── Proxy: buffer ────────────────────────────────────────────────────────
	BufferChunkSize     int
	InitialBehindChunks int
	RedisChunkTTL       time.Duration

	// ── Proxy: cleanup ───────────────────────────────────────────────────────
	CleanupInterval      time.Duration
	CleanupCheckInterval time.Duration

	// ── Proxy: client TTL ────────────────────────────────────────────────────
	ClientHeartbeatInterval time.Duration
	ClientRecordTTL         time.Duration
	HLSClientIdleTimeout    time.Duration

	// ── Proxy: starvation detection ───────────────────────────────────────────
	NoDataTimeoutChecks int
	NoDataCheckInterval time.Duration

	// ── Proxy: initial data wait ──────────────────────────────────────────────
	InitialDataWaitTimeout   time.Duration
	InitialDataCheckInterval time.Duration

	// ── Proxy: pacing ────────────────────────────────────────────────────────
	PacingBurstSeconds        float64
	PacingBitrateMultiplier   float64
	ProxyMaxCatchupMultiplier float64
	ProxyPrebufferSeconds     int

	// ── Proxy: retries ───────────────────────────────────────────────────────
	MaxRetries        int
	RetryWaitInterval time.Duration

	// ── Proxy: stream/control mode ────────────────────────────────────────────
	StreamMode  string // "TS" or "HLS"
	ControlMode string // "http" or "api"

	// ── Proxy: HLS ───────────────────────────────────────────────────────────
	HLSMaxSegments          int
	HLSInitialSegments      int
	HLSWindowSize           int
	HLSBufferReadyTimeout   time.Duration
	HLSFirstSegmentTimeout  time.Duration
	HLSInitialBufferSeconds int
	HLSMaxInitialSegments   int
	HLSSegmentFetchInterval float64

	// ── Proxy: limits ────────────────────────────────────────────────────────
	MaxClientsPerStreamCount int
	MaxTotalStreams           int
	MaxMemoryMB              int

	// ── Control plane: Docker ────────────────────────────────────────────────
	ContainerLabelKey string
	ContainerLabelVal string
	DockerNetwork     string
	DockerHost        string

	// ── Control plane: scaling ───────────────────────────────────────────────
	MinReplicas      int
	MinFreeReplicas  int
	MaxReplicas      int
	MaxStreamsPerEngine int

	// ── Control plane: timing ────────────────────────────────────────────────
	AutoscaleInterval time.Duration
	MonitorInterval   time.Duration
	GracePeriod       time.Duration
	StartupTimeout    time.Duration

	// ── Control plane: health ────────────────────────────────────────────────
	HealthCheckInterval        time.Duration
	HealthFailureThreshold     int
	HealthUnhealthyGracePeriod time.Duration
	HealthReplacementCooldown  time.Duration

	// ── Control plane: circuit breaker ───────────────────────────────────────
	CBFailureThreshold     int
	CBRecoveryTimeout      time.Duration
	CBReplacementThreshold int
	CBReplacementTimeout   time.Duration

	// ── Control plane: port ranges ────────────────────────────────────────────
	PortRangeHost PortRange
	ACEHTTPRange  PortRange
	ACEHTTPSRange PortRange
	ACEMapHTTPS   bool

	// ── Control plane: Gluetun / VPN ─────────────────────────────────────────
	GluetunAPIPort             int
	GluetunHealthCheckInterval time.Duration
	GluetunPortCacheTTL        time.Duration
	PreferredEnginesPerVPN     int
	GluetunPortRange1          string
	GluetunPortRange2          string

	VPNUnhealthyGracePeriod  time.Duration
	VPNDrainingHardTimeout   time.Duration
	VPNDrainingCheckInterval time.Duration

	VPNEnabled              bool
	VPNImage                string
	VPNCredentialsFile      string
	VPNProvider             string
	VPNProtocol             string
	VPNRegions              []string
	ServersJSONDir          string
	VPNControllerInterval   time.Duration
	VPNHealGracePeriod      time.Duration
	VPNServersAutoRefresh   bool
	VPNServersRefreshPeriod time.Duration
	VPNServersRefreshSource string

	// ── Control plane: engine variant ────────────────────────────────────────
	EngineVariant      string
	EngineMemoryLimit  string
	EngineARM32Version string
	EngineARM64Version string

	// ── AceStream Engine Config ──────────────────────────────────────────────
	EngineMaxDownloadRate int
	EngineMaxUploadRate   int
	EngineLiveCacheType   string
	EngineBufferTime      int

	// ── Misc ────────────────────────────────────────────────────────────────
	AutoDelete bool
	ManualMode bool
}

func (c *Config) MaxClientsPerStream() int {
	if c.MaxClientsPerStreamCount <= 0 {
		return 100
	}
	return c.MaxClientsPerStreamCount
}

// ApplySettings merges a proxy_settings map (from SQLite) into the live config.
func ApplySettings(m map[string]any) {
	old := C.Load()
	n := *old
	for k, v := range m {
		switch k {
		case "initial_data_wait_timeout":
			n.InitialDataWaitTimeout = toDur(v, time.Second)
		case "initial_data_check_interval":
			n.InitialDataCheckInterval = toDur(v, time.Second)
		case "no_data_timeout_checks":
			n.NoDataTimeoutChecks = toInt(v)
		case "no_data_check_interval":
			n.NoDataCheckInterval = toDur(v, time.Second)
		case "connection_timeout":
			n.ClientWaitTimeout = toDur(v, time.Second)
		case "upstream_connect_timeout":
			n.UpstreamConnectTimeout = toDur(v, time.Second)
		case "upstream_read_timeout":
			n.UpstreamReadTimeout = toDur(v, time.Second)
		case "stream_timeout":
			n.StreamTimeout = toDur(v, time.Second)
		case "channel_shutdown_delay":
			n.ChannelShutdownDelay = toDur(v, time.Second)
		case "proxy_prebuffer_seconds":
			n.ProxyPrebufferSeconds = toInt(v)
		case "stream_mode":
			if s, ok := v.(string); ok {
				n.StreamMode = strings.ToUpper(s)
			}
		case "control_mode":
			if s, ok := v.(string); ok {
				n.ControlMode = strings.ToLower(s)
			}
		case "hls_max_segments":
			n.HLSMaxSegments = toInt(v)
		case "hls_initial_segments":
			n.HLSInitialSegments = toInt(v)
		case "hls_window_size":
			n.HLSWindowSize = toInt(v)
		case "hls_buffer_ready_timeout":
			n.HLSBufferReadyTimeout = toDur(v, time.Second)
		case "hls_first_segment_timeout":
			n.HLSFirstSegmentTimeout = toDur(v, time.Second)
		case "hls_initial_buffer_seconds":
			n.HLSInitialBufferSeconds = toInt(v)
		case "hls_max_initial_segments":
			n.HLSMaxInitialSegments = toInt(v)
		case "hls_segment_fetch_interval":
			n.HLSSegmentFetchInterval = toFloat(v)
		case "pacing_bitrate_multiplier":
			n.PacingBitrateMultiplier = toFloat(v)
		case "pacing_burst_seconds":
			n.PacingBurstSeconds = toFloat(v)
		case "max_streams_per_engine":
			n.MaxStreamsPerEngine = toInt(v)
		case "max_clients_per_stream":
			n.MaxClientsPerStreamCount = toInt(v)
		case "max_total_streams":
			n.MaxTotalStreams = toInt(v)
		}
	}
	C.Store(&n)
}

// ApplyEngineSettings merges an engine_settings map into the live config.
func ApplyEngineSettings(m map[string]any) {
	old := C.Load()
	n := *old
	for k, v := range m {
		switch k {
		case "min_replicas":
			n.MinReplicas = toInt(v)
		case "max_replicas":
			n.MaxReplicas = toInt(v)
		case "auto_delete":
			if b, ok := v.(bool); ok {
				n.AutoDelete = b
			}
		case "manual_mode":
			if b, ok := v.(bool); ok {
				n.ManualMode = b
			}
		}
	}
	C.Store(&n)
}

// ApplyOrchestratorSettings merges an orchestrator_settings map into the live config.
func ApplyOrchestratorSettings(m map[string]any) {
	old := C.Load()
	n := *old
	for k, v := range m {
		switch k {
		case "monitor_interval_s":
			n.MonitorInterval = toDur(v, time.Second)
		case "engine_grace_period_s":
			n.GracePeriod = toDur(v, time.Second)
		case "autoscale_interval_s":
			n.AutoscaleInterval = toDur(v, time.Second)
		case "startup_timeout_s":
			n.StartupTimeout = toDur(v, time.Second)
		case "health_check_interval_s":
			n.HealthCheckInterval = toDur(v, time.Second)
		case "health_failure_threshold":
			n.HealthFailureThreshold = toInt(v)
		case "health_unhealthy_grace_period_s":
			n.HealthUnhealthyGracePeriod = toDur(v, time.Second)
		case "health_replacement_cooldown_s":
			n.HealthReplacementCooldown = toDur(v, time.Second)
		case "circuit_breaker_failure_threshold":
			n.CBFailureThreshold = toInt(v)
		case "circuit_breaker_recovery_timeout_s":
			n.CBRecoveryTimeout = toDur(v, time.Second)
		case "circuit_breaker_replacement_threshold":
			n.CBReplacementThreshold = toInt(v)
		case "circuit_breaker_replacement_timeout_s":
			n.CBReplacementTimeout = toDur(v, time.Second)
		case "port_range_host":
			if s, ok := v.(string); ok {
				n.PortRangeHost = parseRange(s)
			}
		case "ace_http_range":
			if s, ok := v.(string); ok {
				n.ACEHTTPRange = parseRange(s)
			}
		case "ace_https_range":
			if s, ok := v.(string); ok {
				n.ACEHTTPSRange = parseRange(s)
			}
		case "docker_network":
			if s, ok := v.(string); ok {
				n.DockerNetwork = s
			}
		}
	}
	C.Store(&n)
}

func UpdateDockerNetwork(n string) {
	old := C.Load()
	ncfg := *old
	ncfg.DockerNetwork = n
	C.Store(&ncfg)
}

// ApplyEngineConfig merges an engine_config map into the live config.
func ApplyEngineConfig(m map[string]any) {
	old := C.Load()
	n := *old
	for k, v := range m {
		switch k {
		case "total_max_download_rate":
			n.EngineMaxDownloadRate = toInt(v)
		case "total_max_upload_rate":
			n.EngineMaxUploadRate = toInt(v)
		case "live_cache_type":
			if s, ok := v.(string); ok {
				n.EngineLiveCacheType = s
			}
		case "buffer_time":
			n.EngineBufferTime = toInt(v)
		}
	}
	C.Store(&n)
}

// ApplyVPNSettings merges a vpn_settings map into the live config.
// Keys match what the settings store persists (not the legacy env-var names).
func ApplyVPNSettings(m map[string]any) {
	old := C.Load()
	n := *old
	for k, v := range m {
		switch k {
		case "enabled":
			if b, ok := v.(bool); ok {
				n.VPNEnabled = b
			}
		case "provider":
			if s, ok := v.(string); ok && s != "" {
				n.VPNProvider = s
			}
		case "protocol":
			if s, ok := v.(string); ok && s != "" {
				n.VPNProtocol = s
			}
		case "regions":
			switch rv := v.(type) {
			case []string:
				n.VPNRegions = rv
			case []any:
				var regions []string
				for _, r := range rv {
					if s, ok := r.(string); ok && s != "" {
						regions = append(regions, s)
					}
				}
				n.VPNRegions = regions
			}
		case "api_port":
			if p := toInt(v); p > 0 {
				n.GluetunAPIPort = p
			}
		case "preferred_engines_per_vpn":
			if p := toInt(v); p > 0 {
				n.PreferredEnginesPerVPN = p
			}
		case "dynamic_vpn_management":
			// informational only; not a config field
		case "vpn_servers_auto_refresh":
			if b, ok := v.(bool); ok {
				n.VPNServersAutoRefresh = b
			}
		case "vpn_servers_refresh_period_s":
			if d := toDur(v, time.Second); d > 0 {
				n.VPNServersRefreshPeriod = d
			}
		case "vpn_servers_refresh_source":
			if s, ok := v.(string); ok {
				n.VPNServersRefreshSource = s
			}
		}
	}
	C.Store(&n)
}

func load() *Config {
	const tsPacket = 188
	rawChunk := envInt("BUFFER_CHUNK_SIZE", tsPacket*5644)
	aligned := (rawChunk / tsPacket) * tsPacket

	labelRaw := envStr("CONTAINER_LABEL", "ondemand.app=myservice")
	labelKey, labelVal, _ := strings.Cut(labelRaw, "=")

	return &Config{
		RedisHost: envStr("REDIS_HOST", "localhost"),
		RedisPort: envInt("REDIS_PORT", 6379),
		RedisDB:   envInt("REDIS_DB", 0),
		DBPath:    envStr("ACESTREAM_DB_PATH", "/app/app/config/acestream.db"),
		APIKey:    envStr("API_KEY", ""),

		OrchestratorListenAddr: envStr("ORCHESTRATOR_LISTEN_ADDR", ":8083"),
		ProxyListenAddr:        envStr("PROXY_LISTEN_ADDR", ":8000"),

		StreamRetentionPeriod: envDur("STREAM_RETENTION_S", 300*time.Second),
		CountsPublishInterval: envDur("COUNTS_PUBLISH_INTERVAL_S", 5*time.Second),

		UpstreamConnectTimeout: envDur("UPSTREAM_CONNECT_TIMEOUT_S", 3*time.Second),
		UpstreamReadTimeout:    envDur("UPSTREAM_READ_TIMEOUT_S", 90*time.Second),
		ClientWaitTimeout:      envDur("CLIENT_WAIT_TIMEOUT_S", 30*time.Second),
		StreamTimeout:          envDur("STREAM_TIMEOUT_S", 60*time.Second),
		ChunkTimeout:           envDur("CHUNK_TIMEOUT_S", 5*time.Second),
		ChannelShutdownDelay:   envDur("CHANNEL_SHUTDOWN_DELAY_S", 5*time.Second),
		ChannelInitGracePeriod: envDur("CHANNEL_INIT_GRACE_PERIOD_S", 30*time.Second),
		KeepaliveInterval:      envDur("KEEPALIVE_INTERVAL_MS", 500*time.Millisecond),

		BufferChunkSize:     aligned,
		InitialBehindChunks: envInt("INITIAL_BEHIND_CHUNKS", 4),
		RedisChunkTTL:       envDur("REDIS_CHUNK_TTL_S", 60*time.Second),

		CleanupInterval:      envDur("CLEANUP_INTERVAL_S", 5*time.Second),
		CleanupCheckInterval: envDur("CLEANUP_CHECK_INTERVAL_S", 2*time.Second),

		ClientHeartbeatInterval: envDur("CLIENT_HEARTBEAT_INTERVAL_S", 10*time.Second),
		ClientRecordTTL:         envDur("CLIENT_RECORD_TTL_S", 60*time.Second),
		HLSClientIdleTimeout:    envDur("HLS_CLIENT_IDLE_TIMEOUT_S", 10*time.Second),

		NoDataTimeoutChecks: envInt("NO_DATA_TIMEOUT_CHECKS", 60),
		NoDataCheckInterval: envDur("NO_DATA_CHECK_INTERVAL_S", 1*time.Second),

		InitialDataWaitTimeout:   envDur("INITIAL_DATA_WAIT_TIMEOUT_S", 10*time.Second),
		InitialDataCheckInterval: envDur("INITIAL_DATA_CHECK_INTERVAL_MS", 200*time.Millisecond),

		PacingBurstSeconds:        envFloat("PACING_BURST_SECONDS", 15.0),
		PacingBitrateMultiplier:   envFloat("PACING_BITRATE_MULTIPLIER", 1.5),
		ProxyMaxCatchupMultiplier: envFloat("PROXY_MAX_CATCHUP_MULTIPLIER", 2.0),
		ProxyPrebufferSeconds:     envInt("PROXY_PREBUFFER_SECONDS", 3),

		MaxRetries:        envInt("MAX_RETRIES", 3),
		RetryWaitInterval: envDur("RETRY_WAIT_INTERVAL_MS", 500*time.Millisecond),

		StreamMode:  envStr("STREAM_MODE", "TS"),
		ControlMode: envStr("CONTROL_MODE", "api"),

		HLSMaxSegments:          envInt("HLS_MAX_SEGMENTS", 20),
		HLSInitialSegments:      envInt("HLS_INITIAL_SEGMENTS", 3),
		HLSWindowSize:           envInt("HLS_WINDOW_SIZE", 6),
		HLSBufferReadyTimeout:   envDur("HLS_BUFFER_READY_TIMEOUT_S", 30*time.Second),
		HLSFirstSegmentTimeout:  envDur("HLS_FIRST_SEGMENT_TIMEOUT_S", 30*time.Second),
		HLSInitialBufferSeconds: envInt("HLS_INITIAL_BUFFER_SECONDS", 10),
		HLSMaxInitialSegments:   envInt("HLS_MAX_INITIAL_SEGMENTS", 10),
		HLSSegmentFetchInterval: envFloat("HLS_SEGMENT_FETCH_INTERVAL", 0.5),

		MaxClientsPerStreamCount: envInt("MAX_CLIENTS_PER_STREAM", 100),
		MaxTotalStreams:           envInt("MAX_TOTAL_STREAMS", 150),
		MaxMemoryMB:              envInt("MAX_MEMORY_MB", 2048),

		ContainerLabelKey: labelKey,
		ContainerLabelVal: labelVal,
		DockerNetwork:     envStr("DOCKER_NETWORK", ""),
		DockerHost:        envStr("DOCKER_HOST", ""),

		MinReplicas:        envInt("MIN_REPLICAS", 2),
		MinFreeReplicas:    envInt("MIN_FREE_REPLICAS", 1),
		MaxReplicas:        envInt("MAX_REPLICAS", 6),
		MaxStreamsPerEngine: envInt("MAX_STREAMS_PER_ENGINE", 3),

		AutoscaleInterval: envDur("AUTOSCALE_INTERVAL_S", 30*time.Second),
		MonitorInterval:   envDur("MONITOR_INTERVAL_S", 10*time.Second),
		GracePeriod:       envDur("ENGINE_GRACE_PERIOD_S", 30*time.Second),
		StartupTimeout:    envDur("STARTUP_TIMEOUT_S", 25*time.Second),

		HealthCheckInterval:        envDur("HEALTH_CHECK_INTERVAL_S", 20*time.Second),
		HealthFailureThreshold:     envInt("HEALTH_FAILURE_THRESHOLD", 3),
		HealthUnhealthyGracePeriod: envDur("HEALTH_UNHEALTHY_GRACE_PERIOD_S", 60*time.Second),
		HealthReplacementCooldown:  envDur("HEALTH_REPLACEMENT_COOLDOWN_S", 60*time.Second),

		CBFailureThreshold:     envInt("CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5),
		CBRecoveryTimeout:      envDur("CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S", 300*time.Second),
		CBReplacementThreshold: envInt("CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD", 3),
		CBReplacementTimeout:   envDur("CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S", 180*time.Second),

		PortRangeHost: parseRange(envStr("PORT_RANGE_HOST", "19000-19999")),
		ACEHTTPRange:  parseRange(envStr("ACE_HTTP_RANGE", "40000-44999")),
		ACEHTTPSRange: parseRange(envStr("ACE_HTTPS_RANGE", "45000-49999")),
		ACEMapHTTPS:   envBool("ACE_MAP_HTTPS", false),

		GluetunAPIPort:             envInt("GLUETUN_API_PORT", 8001),
		GluetunHealthCheckInterval: envDur("GLUETUN_HEALTH_CHECK_INTERVAL_S", 5*time.Second),
		GluetunPortCacheTTL:        envDur("GLUETUN_PORT_CACHE_TTL_S", 60*time.Second),
		PreferredEnginesPerVPN:     envInt("PREFERRED_ENGINES_PER_VPN", 10),
		GluetunPortRange1:          envStr("GLUETUN_PORT_RANGE_1", ""),
		GluetunPortRange2:          envStr("GLUETUN_PORT_RANGE_2", ""),

		VPNUnhealthyGracePeriod:  envDur("VPN_UNHEALTHY_GRACE_PERIOD_S", 5*time.Minute),
		VPNDrainingHardTimeout:   envDur("VPN_DRAINING_HARD_TIMEOUT_S", 10*time.Minute),
		VPNDrainingCheckInterval: envDur("VPN_DRAINING_CHECK_INTERVAL_S", 15*time.Second),

		VPNEnabled:              envBool("VPN_ENABLED", false),
		VPNImage:                envStr("VPN_IMAGE", "qmcgaw/gluetun"),
		VPNCredentialsFile:      envStr("VPN_CREDENTIALS_FILE", ""),
		VPNProvider:             envStr("VPN_PROVIDER", "protonvpn"),
		VPNProtocol:             envStr("VPN_PROTOCOL", "wireguard"),
		VPNRegions:              envStrSlice("VPN_REGIONS", nil),
		ServersJSONDir:          envStr("VPN_SERVERS_JSON_DIR", ""),
		VPNControllerInterval:   envDur("VPN_CONTROLLER_INTERVAL_S", 5*time.Second),
		VPNHealGracePeriod:      envDur("VPN_NOTREADY_HEAL_GRACE_S", 45*time.Second),
		VPNServersAutoRefresh:   envBool("VPN_SERVERS_AUTO_REFRESH", false),
		VPNServersRefreshPeriod: envDur("VPN_SERVERS_REFRESH_PERIOD_S", 86400*time.Second),
		VPNServersRefreshSource: envStr("VPN_SERVERS_REFRESH_SOURCE", "gluetun_official"),

		EngineVariant:      envStr("ENGINE_VARIANT", ""),
		EngineMemoryLimit:  envStr("ENGINE_MEMORY_LIMIT", ""),
		EngineARM32Version: envStr("ENGINE_ARM32_VERSION", "arm32-v3.2.13"),
		EngineARM64Version: envStr("ENGINE_ARM64_VERSION", "arm64-v3.2.13"),

		AutoDelete: envBool("AUTO_DELETE", true),
	}
}

// ── Env helpers ───────────────────────────────────────────────────────────────

func envStr(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func envInt(k string, def int) int {
	if v := os.Getenv(k); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return def
}

func envFloat(k string, def float64) float64 {
	if v := os.Getenv(k); v != "" {
		if f, err := strconv.ParseFloat(v, 64); err == nil {
			return f
		}
	}
	return def
}

func envBool(k string, def bool) bool {
	if v := os.Getenv(k); v != "" {
		if b, err := strconv.ParseBool(v); err == nil {
			return b
		}
	}
	return def
}

func envStrSlice(k string, def []string) []string {
	if v := os.Getenv(k); v != "" {
		var out []string
		for _, part := range strings.Split(v, ",") {
			if s := strings.TrimSpace(part); s != "" {
				out = append(out, s)
			}
		}
		return out
	}
	return def
}

func envDur(k string, def time.Duration) time.Duration {
	if v := os.Getenv(k); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
		if n, err := strconv.Atoi(v); err == nil {
			return time.Duration(n) * time.Second
		}
	}
	return def
}

func parseRange(s string) PortRange {
	parts := strings.SplitN(s, "-", 2)
	if len(parts) != 2 {
		return PortRange{19000, 19999}
	}
	lo, _ := strconv.Atoi(parts[0])
	hi, _ := strconv.Atoi(parts[1])
	return PortRange{lo, hi}
}

// ── Settings-to-config helpers ────────────────────────────────────────────────

func toDur(v any, unit time.Duration) time.Duration {
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

func toInt(v any) int {
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

func toFloat(v any) float64 {
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
