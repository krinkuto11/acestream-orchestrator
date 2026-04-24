package state

import (
	"context"
	"encoding/json"
	"log/slog"
	"strconv"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/acestream/controlplane/internal/rediskeys"
)

// RedisPublisher writes control plane state to Redis and notifies Python bridge.
type RedisPublisher struct {
	rdb *redis.Client
}

func NewRedisPublisher(rdb *redis.Client) *RedisPublisher {
	return &RedisPublisher{rdb: rdb}
}

// PublishEngine writes/updates a single engine entry in Redis and notifies listeners.
func (p *RedisPublisher) PublishEngine(ctx context.Context, e *Engine) {
	data, err := json.Marshal(e)
	if err != nil {
		slog.Error("failed to marshal engine for redis", "err", err)
		return
	}

	pipe := p.rdb.Pipeline()
	pipe.Set(ctx, rediskeys.CPEngineKey(e.ContainerID), data, 24*time.Hour)
	pipe.SAdd(ctx, rediskeys.CPEnginesIndex, e.ContainerID)
	pipe.Publish(ctx, rediskeys.CPStateChanged, "engine_updated:"+e.ContainerID)

	if _, err := pipe.Exec(ctx); err != nil {
		slog.Error("redis publish engine failed", "err", err)
	}
}

// RemoveEngine removes a single engine from Redis.
func (p *RedisPublisher) RemoveEngine(ctx context.Context, containerID string) {
	pipe := p.rdb.Pipeline()
	pipe.Del(ctx, rediskeys.CPEngineKey(containerID))
	pipe.SRem(ctx, rediskeys.CPEnginesIndex, containerID)
	pipe.Publish(ctx, rediskeys.CPStateChanged, "engine_removed:"+containerID)

	if _, err := pipe.Exec(ctx); err != nil {
		slog.Error("redis remove engine failed", "err", err)
	}
}

// PublishVPNNode writes/updates a VPN node entry in Redis.
func (p *RedisPublisher) PublishVPNNode(ctx context.Context, n *VPNNode) {
	data, err := json.Marshal(n)
	if err != nil {
		return
	}

	pipe := p.rdb.Pipeline()
	pipe.Set(ctx, rediskeys.CPVPNNodeKey(n.ContainerName), data, 24*time.Hour)
	pipe.SAdd(ctx, rediskeys.CPVPNNodesIndex, n.ContainerName)
	pipe.Publish(ctx, rediskeys.CPStateChanged, "vpn_updated:"+n.ContainerName)

	if _, err := pipe.Exec(ctx); err != nil {
		slog.Error("redis publish vpn node failed", "err", err)
	}
}

// RemoveVPNNode removes a VPN node from Redis.
func (p *RedisPublisher) RemoveVPNNode(ctx context.Context, name string) {
	pipe := p.rdb.Pipeline()
	pipe.Del(ctx, rediskeys.CPVPNNodeKey(name))
	pipe.SRem(ctx, rediskeys.CPVPNNodesIndex, name)
	pipe.Publish(ctx, rediskeys.CPStateChanged, "vpn_removed:"+name)

	if _, err := pipe.Exec(ctx); err != nil {
		slog.Error("redis remove vpn node failed", "err", err)
	}
}

// SetDesiredReplicas persists desired count to Redis for Python to observe.
func (p *RedisPublisher) SetDesiredReplicas(ctx context.Context, n int) {
	if err := p.rdb.Set(ctx, rediskeys.CPDesiredReplicas, n, 0).Err(); err != nil {
		slog.Error("redis set desired replicas failed", "err", err)
	}
}

// PollStreamCounts reads Python-written stream counts into our local store.
// Returns true if any count changed, so callers can nudge the controller.
func PollStreamCounts(ctx context.Context, rdb *redis.Client, st *Store) bool {
	result, err := rdb.HGetAll(ctx, rediskeys.CPStreamCounts).Result()
	if err != nil {
		return false
	}
	changed := false
	for cid, countStr := range result {
		if n, err := strconv.Atoi(countStr); err == nil {
			if st.GetStreamCount(cid) != n {
				st.SetStreamCount(cid, n)
				changed = true
			}
		}
	}
	return changed
}

// PollMonitorCounts reads Python-written monitor session counts into our local store.
// Returns true if any count changed.
func PollMonitorCounts(ctx context.Context, rdb *redis.Client, st *Store) bool {
	result, err := rdb.HGetAll(ctx, rediskeys.CPMonitorCounts).Result()
	if err != nil {
		return false
	}
	changed := false
	for cid, countStr := range result {
		if n, err := strconv.Atoi(countStr); err == nil {
			prev := st.GetAllMonitorCounts()[cid]
			if prev != n {
				st.SetMonitorCount(cid, n)
				changed = true
			}
		}
	}
	return changed
}

// SetForwardedPending writes the forwarded-pending election flag to Redis.
func (p *RedisPublisher) SetForwardedPending(ctx context.Context, vpn string, pending bool) {
	key := rediskeys.CPForwardedPending(vpn)
	if pending {
		p.rdb.Set(ctx, key, "1", 5*time.Minute)
	} else {
		p.rdb.Del(ctx, key)
	}
}

// IsForwardedPending checks the Redis flag for a forwarded-pending election.
func IsForwardedPendingRedis(ctx context.Context, rdb *redis.Client, vpn string) bool {
	key := rediskeys.CPForwardedPending(vpn)
	val, err := rdb.Get(ctx, key).Result()
	return err == nil && val == "1"
}
