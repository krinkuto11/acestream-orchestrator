package config

import (
	"os"
	"strconv"
	"time"
)

type Config struct {
	ListenAddr string
	APIKey     string

	RedisHost string
	RedisPort int
	RedisDB   int

	DBPath string

	// How long to keep stream state after a stream ends.
	StreamRetentionPeriod time.Duration

	// How often to publish stream/monitor counts to Redis for the autoscaler.
	CountsPublishInterval time.Duration
}

var C = load()

func load() Config {
	return Config{
		ListenAddr: envStr("ORCHESTRATOR_LISTEN_ADDR", ":8082"),
		APIKey:     envStr("API_KEY", ""),

		RedisHost: envStr("REDIS_HOST", "localhost"),
		RedisPort: envInt("REDIS_PORT", 6379),
		RedisDB:   envInt("REDIS_DB", 0),

		DBPath: envStr("ORCHESTRATOR_DB_PATH", "/data/orchestrator.db"),

		StreamRetentionPeriod: envDuration("STREAM_RETENTION_S", 300*time.Second),
		CountsPublishInterval: envDuration("COUNTS_PUBLISH_INTERVAL_S", 5*time.Second),
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

func envDuration(key string, def time.Duration) time.Duration {
	if v := os.Getenv(key); v != "" {
		if n, err := strconv.ParseFloat(v, 64); err == nil {
			return time.Duration(n * float64(time.Second))
		}
	}
	return def
}
