package bridge

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"strconv"
	"strings"
	"time"

	goredis "github.com/redis/go-redis/v9"

	"github.com/acestream/orchestrator/internal/state"
)

const (
	cpEnginesIndex  = "cp:engines:all"
	cpVPNNodesIndex = "cp:vpn_nodes:all"
	cpStateChanged  = "cp:state_changed"
	cpStreamCounts  = "cp:stream_counts"
	cpMonitorCounts = "cp:monitor_counts"
)

func cpEngineKey(id string) string  { return fmt.Sprintf("cp:engine:%s", id) }
func cpVPNNodeKey(n string) string  { return fmt.Sprintf("cp:vpn_node:%s", n) }

// RedisBridge subscribes to cp:state_changed and keeps orchestrator state in
// sync with control plane state.  It also publishes stream/monitor counts back
// to Redis so the autoscaler can read them.
type RedisBridge struct {
	rdb *goredis.Client
	st  *state.Store
}

func New(rdb *goredis.Client, st *state.Store) *RedisBridge {
	return &RedisBridge{rdb: rdb, st: st}
}

// Bootstrap loads the full current state from Redis before subscribing.
func (b *RedisBridge) Bootstrap(ctx context.Context) error {
	if err := b.bootstrapEngines(ctx); err != nil {
		return fmt.Errorf("bootstrap engines: %w", err)
	}
	if err := b.bootstrapVPNNodes(ctx); err != nil {
		return fmt.Errorf("bootstrap vpn nodes: %w", err)
	}
	slog.Info("RedisBridge bootstrap complete",
		"engines", len(b.st.ListEngines()),
		"vpn_nodes", len(b.st.ListVPNNodes()),
	)
	return nil
}

func (b *RedisBridge) bootstrapEngines(ctx context.Context) error {
	ids, err := b.rdb.SMembers(ctx, cpEnginesIndex).Result()
	if err != nil {
		return err
	}
	for _, id := range ids {
		data, err := b.rdb.Get(ctx, cpEngineKey(id)).Bytes()
		if err != nil {
			continue
		}
		e := b.unmarshalEngine(data)
		if e != nil {
			b.st.UpsertEngine(e)
		}
	}
	return nil
}

func (b *RedisBridge) bootstrapVPNNodes(ctx context.Context) error {
	names, err := b.rdb.SMembers(ctx, cpVPNNodesIndex).Result()
	if err != nil {
		return err
	}
	for _, name := range names {
		data, err := b.rdb.Get(ctx, cpVPNNodeKey(name)).Bytes()
		if err != nil {
			continue
		}
		n := b.unmarshalVPNNode(data)
		if n != nil {
			b.st.UpsertVPNNode(n)
		}
	}
	return nil
}

// Run subscribes to cp:state_changed and processes events until ctx is done.
func (b *RedisBridge) Run(ctx context.Context) {
	sub := b.rdb.Subscribe(ctx, cpStateChanged)
	defer sub.Close()
	ch := sub.Channel()

	slog.Info("RedisBridge: subscribed to cp:state_changed")
	for {
		select {
		case <-ctx.Done():
			slog.Info("RedisBridge stopped")
			return
		case msg, ok := <-ch:
			if !ok {
				slog.Warn("RedisBridge: pub/sub channel closed; resubscribing")
				time.Sleep(2 * time.Second)
				sub = b.rdb.Subscribe(ctx, cpStateChanged)
				ch = sub.Channel()
				continue
			}
			b.handleMessage(ctx, msg.Payload)
		}
	}
}

func (b *RedisBridge) handleMessage(ctx context.Context, payload string) {
	switch {
	case strings.HasPrefix(payload, "engine_updated:"):
		id := strings.TrimPrefix(payload, "engine_updated:")
		b.fetchAndUpsertEngine(ctx, id)
	case strings.HasPrefix(payload, "engine_removed:"):
		id := strings.TrimPrefix(payload, "engine_removed:")
		b.st.RemoveEngine(id)
	case strings.HasPrefix(payload, "vpn_updated:"):
		name := strings.TrimPrefix(payload, "vpn_updated:")
		b.fetchAndUpsertVPNNode(ctx, name)
	case strings.HasPrefix(payload, "vpn_removed:"):
		name := strings.TrimPrefix(payload, "vpn_removed:")
		b.st.RemoveVPNNode(name)
	}
}

func (b *RedisBridge) fetchAndUpsertEngine(ctx context.Context, id string) {
	data, err := b.rdb.Get(ctx, cpEngineKey(id)).Bytes()
	if err != nil {
		return
	}
	if e := b.unmarshalEngine(data); e != nil {
		b.st.UpsertEngine(e)
	}
}

func (b *RedisBridge) fetchAndUpsertVPNNode(ctx context.Context, name string) {
	data, err := b.rdb.Get(ctx, cpVPNNodeKey(name)).Bytes()
	if err != nil {
		return
	}
	if n := b.unmarshalVPNNode(data); n != nil {
		b.st.UpsertVPNNode(n)
	}
}

func (b *RedisBridge) unmarshalEngine(data []byte) *state.EngineState {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil
	}
	e := &state.EngineState{}
	jsonStr(raw, "container_id", &e.ContainerID)
	jsonStr(raw, "container_name", &e.ContainerName)
	jsonStr(raw, "host", &e.Host)
	jsonInt(raw, "port", &e.Port)
	jsonInt(raw, "api_port", &e.APIPort)
	jsonBool(raw, "forwarded", &e.Forwarded)
	jsonStr(raw, "vpn_container", &e.VPNContainer)
	jsonStr(raw, "health_status", &e.HealthStatus)
	jsonBool(raw, "draining", &e.Draining)
	jsonStr(raw, "drain_reason", &e.DrainReason)
	if e.ContainerID == "" {
		return nil
	}
	return e
}

func (b *RedisBridge) unmarshalVPNNode(data []byte) *state.VPNNodeState {
	var raw map[string]json.RawMessage
	if err := json.Unmarshal(data, &raw); err != nil {
		return nil
	}
	n := &state.VPNNodeState{}
	jsonStr(raw, "container_name", &n.ContainerName)
	jsonStr(raw, "container_id", &n.ContainerID)
	jsonStr(raw, "status", &n.Status)
	jsonBool(raw, "healthy", &n.Healthy)
	jsonStr(raw, "provider", &n.Provider)
	jsonStr(raw, "protocol", &n.Protocol)
	jsonStr(raw, "lifecycle", &n.Lifecycle)
	jsonBool(raw, "managed_dynamic", &n.ManagedDynamic)
	jsonBool(raw, "port_forwarding_supported", &n.PortForwardingSupported)
	if n.ContainerName == "" {
		return nil
	}
	return n
}

// PublishCounts writes stream and monitor counts to Redis for the autoscaler.
func (b *RedisBridge) PublishCounts(ctx context.Context) {
	streamCounts := b.st.GetStreamCounts()
	monitorCounts := b.st.GetMonitorCounts()

	pipe := b.rdb.Pipeline()

	if len(streamCounts) > 0 {
		args := make(map[string]interface{}, len(streamCounts))
		for k, v := range streamCounts {
			args[k] = strconv.Itoa(v)
		}
		pipe.HSet(ctx, cpStreamCounts, args)
		pipe.Expire(ctx, cpStreamCounts, 60*time.Second)
	} else {
		pipe.Del(ctx, cpStreamCounts)
	}

	if len(monitorCounts) > 0 {
		args := make(map[string]interface{}, len(monitorCounts))
		for k, v := range monitorCounts {
			args[k] = strconv.Itoa(v)
		}
		pipe.HSet(ctx, cpMonitorCounts, args)
		pipe.Expire(ctx, cpMonitorCounts, 60*time.Second)
	} else {
		pipe.Del(ctx, cpMonitorCounts)
	}

	if _, err := pipe.Exec(ctx); err != nil {
		slog.Warn("RedisBridge: failed to publish counts", "err", err)
	}
}

// RunCountsPublisher periodically publishes stream/monitor counts to Redis.
func (b *RedisBridge) RunCountsPublisher(ctx context.Context, interval time.Duration) {
	ticker := time.NewTicker(interval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			b.PublishCounts(ctx)
		}
	}
}

// ── JSON helpers ──────────────────────────────────────────────────────────────

func jsonStr(m map[string]json.RawMessage, key string, dst *string) {
	if v, ok := m[key]; ok {
		_ = json.Unmarshal(v, dst)
	}
}

func jsonInt(m map[string]json.RawMessage, key string, dst *int) {
	if v, ok := m[key]; ok {
		_ = json.Unmarshal(v, dst)
	}
}

func jsonBool(m map[string]json.RawMessage, key string, dst *bool) {
	if v, ok := m[key]; ok {
		_ = json.Unmarshal(v, dst)
	}
}
