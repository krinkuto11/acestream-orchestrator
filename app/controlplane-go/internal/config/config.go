package config

import (
	"os"
	"strconv"
	"strings"
	"time"
)

// C is the global config, populated once at startup from env vars.
var C Config

func init() {
	C = load()
}

type PortRange struct {
	Min int
	Max int
}

type Config struct {
	// Network / listen
	ListenAddr string

	// Redis
	RedisHost string
	RedisPort int
	RedisDB   int

	// Python orchestrator (for stream counts)
	OrchestratorURL string

	// Docker
	ContainerLabelKey string
	ContainerLabelVal string
	DockerNetwork     string
	DockerHost        string

	// Scaling
	MinReplicas      int
	MinFreeReplicas  int
	MaxReplicas      int
	MaxStreamsPerEng int

	// Timing
	AutoscaleInterval  time.Duration
	MonitorInterval    time.Duration
	GracePeriod        time.Duration
	StartupTimeout     time.Duration

	// Health
	HealthCheckInterval       time.Duration
	HealthFailureThreshold    int
	HealthUnhealthyGracePeriod time.Duration
	HealthReplacementCooldown  time.Duration

	// Circuit breaker
	CBFailureThreshold      int
	CBRecoveryTimeout       time.Duration
	CBReplacementThreshold  int
	CBReplacementTimeout    time.Duration

	// Port ranges
	PortRangeHost  PortRange
	ACEHTTPRange   PortRange
	ACEHTTPSRange  PortRange
	ACEMapHTTPS    bool

	// VPN / Gluetun
	GluetunAPIPort          int
	GluetunHealthCheckInterval time.Duration
	GluetunPortCacheTTL     time.Duration
	PreferredEnginesPerVPN  int
	GluetunPortRange1       string
	GluetunPortRange2       string

	// VPN lifecycle
	VPNUnhealthyGracePeriod    time.Duration // how long unhealthy before auto-drain (dynamic only)
	VPNDrainingHardTimeout     time.Duration // force-stop VPN even if engines still have streams
	VPNDrainingCheckInterval   time.Duration // how often the lifecycle loop ticks

	// VPN provisioning (dynamic node management)
	VPNEnabled              bool
	VPNImage                string
	VPNCredentialsFile      string
	VPNProvider             string
	VPNProtocol             string
	VPNRegions              []string // comma-separated in env
	ServersJSONDir          string   // directory for servers.json / servers-official.json
	VPNControllerInterval   time.Duration
	VPNHealGracePeriod      time.Duration // how long notready before auto-drain
	VPNServersAutoRefresh   bool
	VPNServersRefreshPeriod time.Duration
	VPNServersRefreshSource string // "gluetun_official" | "proton_paid"

	// Engine
	EngineVariant       string
	EngineMemoryLimit   string
	EngineARM32Version  string
	EngineARM64Version  string

	// Misc
	AutoDelete bool
	APIKey     string
}

func load() Config {
	labelRaw := envStr("CONTAINER_LABEL", "ondemand.app=myservice")
	labelKey, labelVal, _ := strings.Cut(labelRaw, "=")

	return Config{
		ListenAddr: envStr("CP_LISTEN_ADDR", ":8082"),

		RedisHost: envStr("REDIS_HOST", "localhost"),
		RedisPort: envInt("REDIS_PORT", 6379),
		RedisDB:   envInt("REDIS_DB", 0),

		OrchestratorURL: envStr("ORCHESTRATOR_URL", "http://localhost:8000"),

		ContainerLabelKey: labelKey,
		ContainerLabelVal: labelVal,
		DockerNetwork:     envStr("DOCKER_NETWORK", ""),
		DockerHost:        envStr("DOCKER_HOST", ""),

		MinReplicas:      envInt("MIN_REPLICAS", 2),
		MinFreeReplicas:  envInt("MIN_FREE_REPLICAS", 1),
		MaxReplicas:      envInt("MAX_REPLICAS", 6),
		MaxStreamsPerEng: envInt("MAX_STREAMS_PER_ENGINE", 3),

		AutoscaleInterval: envDuration("AUTOSCALE_INTERVAL_S", 30*time.Second),
		MonitorInterval:   envDuration("MONITOR_INTERVAL_S", 10*time.Second),
		GracePeriod:       envDuration("ENGINE_GRACE_PERIOD_S", 30*time.Second),
		StartupTimeout:    envDuration("STARTUP_TIMEOUT_S", 25*time.Second),

		HealthCheckInterval:        envDuration("HEALTH_CHECK_INTERVAL_S", 20*time.Second),
		HealthFailureThreshold:     envInt("HEALTH_FAILURE_THRESHOLD", 3),
		HealthUnhealthyGracePeriod: envDuration("HEALTH_UNHEALTHY_GRACE_PERIOD_S", 60*time.Second),
		HealthReplacementCooldown:  envDuration("HEALTH_REPLACEMENT_COOLDOWN_S", 60*time.Second),

		CBFailureThreshold:     envInt("CIRCUIT_BREAKER_FAILURE_THRESHOLD", 5),
		CBRecoveryTimeout:      envDuration("CIRCUIT_BREAKER_RECOVERY_TIMEOUT_S", 300*time.Second),
		CBReplacementThreshold: envInt("CIRCUIT_BREAKER_REPLACEMENT_THRESHOLD", 3),
		CBReplacementTimeout:   envDuration("CIRCUIT_BREAKER_REPLACEMENT_TIMEOUT_S", 180*time.Second),

		PortRangeHost: parseRange(envStr("PORT_RANGE_HOST", "19000-19999")),
		ACEHTTPRange:  parseRange(envStr("ACE_HTTP_RANGE", "40000-44999")),
		ACEHTTPSRange: parseRange(envStr("ACE_HTTPS_RANGE", "45000-49999")),
		ACEMapHTTPS:   envBool("ACE_MAP_HTTPS", false),

		GluetunAPIPort:             envInt("GLUETUN_API_PORT", 8001),
		GluetunHealthCheckInterval: envDuration("GLUETUN_HEALTH_CHECK_INTERVAL_S", 5*time.Second),
		GluetunPortCacheTTL:        envDuration("GLUETUN_PORT_CACHE_TTL_S", 60*time.Second),
		PreferredEnginesPerVPN:     envInt("PREFERRED_ENGINES_PER_VPN", 10),
		GluetunPortRange1:          envStr("GLUETUN_PORT_RANGE_1", ""),
		GluetunPortRange2:          envStr("GLUETUN_PORT_RANGE_2", ""),

		VPNUnhealthyGracePeriod:  envDuration("VPN_UNHEALTHY_GRACE_PERIOD_S", 5*time.Minute),
		VPNDrainingHardTimeout:   envDuration("VPN_DRAINING_HARD_TIMEOUT_S", 10*time.Minute),
		VPNDrainingCheckInterval: envDuration("VPN_DRAINING_CHECK_INTERVAL_S", 15*time.Second),

		VPNEnabled:              envBool("VPN_ENABLED", false),
		VPNImage:                envStr("VPN_IMAGE", "qmcgaw/gluetun"),
		VPNCredentialsFile:      envStr("VPN_CREDENTIALS_FILE", ""),
		VPNProvider:             envStr("VPN_PROVIDER", "protonvpn"),
		VPNProtocol:             envStr("VPN_PROTOCOL", "wireguard"),
		VPNRegions:              envStrSlice("VPN_REGIONS", nil),
		ServersJSONDir:          envStr("VPN_SERVERS_JSON_DIR", ""),
		VPNControllerInterval:   envDuration("VPN_CONTROLLER_INTERVAL_S", 5*time.Second),
		VPNHealGracePeriod:      envDuration("VPN_NOTREADY_HEAL_GRACE_S", 45*time.Second),
		VPNServersAutoRefresh:   envBool("VPN_SERVERS_AUTO_REFRESH", false),
		VPNServersRefreshPeriod: envDuration("VPN_SERVERS_REFRESH_PERIOD_S", 86400*time.Second),
		VPNServersRefreshSource: envStr("VPN_SERVERS_REFRESH_SOURCE", "gluetun_official"),

		EngineVariant:      envStr("ENGINE_VARIANT", "AceServe-amd64"),
		EngineMemoryLimit:  envStr("ENGINE_MEMORY_LIMIT", ""),
		EngineARM32Version: envStr("ENGINE_ARM32_VERSION", "arm32-v3.2.13"),
		EngineARM64Version: envStr("ENGINE_ARM64_VERSION", "arm64-v3.2.13"),

		AutoDelete: envBool("AUTO_DELETE", true),
		APIKey:     envStr("API_KEY", ""),
	}
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

func envBool(key string, def bool) bool {
	if v := os.Getenv(key); v != "" {
		b, err := strconv.ParseBool(v)
		if err == nil {
			return b
		}
	}
	return def
}

func envStrSlice(key string, def []string) []string {
	if v := os.Getenv(key); v != "" {
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

func envDuration(key string, def time.Duration) time.Duration {
	if v := os.Getenv(key); v != "" {
		if d, err := time.ParseDuration(v); err == nil {
			return d
		}
		if n, err := strconv.Atoi(v); err == nil {
			return time.Duration(n) * time.Second
		}
	}
	return def
}
