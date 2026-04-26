package stream

import (
	"context"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/rediskeys"
	"github.com/acestream/acestream/internal/proxy/telemetry"
)

// ClientRecord holds metadata for one connected client.
type ClientRecord struct {
	ID           string
	IP           string
	UserAgent    string
	ConnectedAt  time.Time
	LastActive   time.Time
	BytesSent    int64
	ChunksSent   int64
	InitialIndex int64
	WorkerID     string

	PrevBytesSent int64
	PrevUpdatedAt time.Time
	BPS           float64

	RequestsTotal       int64
	BufferSecondsBehind float64
	IsPrebuffering      bool
	LastRequestKind     string
	LastSequence        int64
}

// ClientManager tracks active clients for one stream.
type ClientManager struct {
	contentID string
	workerID  string
	rdb       *redis.Client

	mu                 sync.RWMutex
	clients            map[string]*ClientRecord
	hadClients         bool
	isInitialBuffering bool

	stopCh   chan struct{}
	stopOnce sync.Once
}

func newClientManager(contentID, workerID string, rdb *redis.Client) *ClientManager {
	cm := &ClientManager{
		contentID:          contentID,
		workerID:           workerID,
		rdb:                rdb,
		clients:            make(map[string]*ClientRecord),
		stopCh:             make(chan struct{}),
		isInitialBuffering: true,
	}
	go cm.heartbeatLoop()
	return cm
}

func (cm *ClientManager) SetInitialBuffering(v bool) {
	cm.mu.Lock()
	cm.isInitialBuffering = v
	cm.mu.Unlock()
}

func (cm *ClientManager) Add(clientID, ip, userAgent string, initialIndex int64) bool {
	cm.mu.Lock()
	defer cm.mu.Unlock()

	if len(cm.clients) >= config.C.Load().MaxClientsPerStream() {
		slog.Warn("rejecting client: max capacity reached",
			"stream", cm.contentID, "client", clientID, "max", config.C.Load().MaxClientsPerStream())
		return false
	}

	now := time.Now()
	cm.clients[clientID] = &ClientRecord{
		ID:              clientID,
		IP:              ip,
		UserAgent:       userAgent,
		ConnectedAt:     now,
		LastActive:      now,
		InitialIndex:    initialIndex,
		WorkerID:        cm.workerID,
		PrevUpdatedAt:   now,
		RequestsTotal:   1,
		LastRequestKind: "TS",
	}
	cm.hadClients = true

	ctx := context.Background()
	ttl := config.C.Load().ClientRecordTTL
	key := rediskeys.ClientMetadata(cm.contentID, clientID)
	cm.rdb.HSet(ctx, key,
		"client_id", clientID,
		"stream_id", cm.contentID,
		"ip_address", ip,
		"user_agent", userAgent,
		"protocol", "TS",
		"connected_at", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
		"last_active", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
		"initial_index", fmt.Sprintf("%d", initialIndex),
		"worker_id", cm.workerID,
		"bytes_sent", "0",
		"chunks_sent", "0",
		"requests_total", "1",
		"bps", "0",
		"stats_updated_at", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
	)
	cm.rdb.Expire(ctx, key, ttl)

	setKey := rediskeys.Clients(cm.contentID)
	cm.rdb.SAdd(ctx, setKey, clientID)
	cm.rdb.Expire(ctx, setKey, ttl)

	cm.rdb.Del(ctx, rediskeys.StreamInitTime(cm.contentID))
	cm.rdb.Del(ctx, rediskeys.LastClientDisconnect(cm.contentID))

	slog.Info("client connected", "stream", cm.contentID, "client", clientID, "ip", ip,
		"local", len(cm.clients))
	telemetry.DefaultTelemetry.ObserveConnect("TS")
	return true
}

func (cm *ClientManager) Remove(clientID string) {
	cm.mu.Lock()
	delete(cm.clients, clientID)
	remaining := len(cm.clients)
	cm.mu.Unlock()

	ctx := context.Background()
	setKey := rediskeys.Clients(cm.contentID)
	cm.rdb.SRem(ctx, setKey, clientID)
	cm.rdb.Del(ctx, rediskeys.ClientMetadata(cm.contentID, clientID))

	if remaining == 0 {
		cm.rdb.Set(ctx, rediskeys.LastClientDisconnect(cm.contentID),
			fmt.Sprintf("%f", float64(time.Now().UnixNano())/1e9),
			config.C.Load().ClientRecordTTL)
	}

	slog.Info("client disconnected", "stream", cm.contentID, "client", clientID,
		"remaining_local", remaining)
	telemetry.DefaultTelemetry.ObserveDisconnect("TS")
}

func (cm *ClientManager) HadClients() bool {
	cm.mu.RLock()
	v := cm.hadClients
	cm.mu.RUnlock()
	return v
}

func (cm *ClientManager) HasCapacity() bool {
	cm.mu.RLock()
	ok := len(cm.clients) < config.C.Load().MaxClientsPerStream()
	cm.mu.RUnlock()
	return ok
}

func (cm *ClientManager) LocalCount() int {
	cm.mu.RLock()
	n := len(cm.clients)
	cm.mu.RUnlock()
	return n
}

func (cm *ClientManager) TotalCount() int {
	n, err := cm.rdb.SCard(context.Background(), rediskeys.Clients(cm.contentID)).Result()
	if err != nil {
		cm.mu.RLock()
		n2 := int64(len(cm.clients))
		cm.mu.RUnlock()
		return int(n2)
	}
	return int(n)
}

func (cm *ClientManager) UpdateStats(clientID string, bytesDelta, chunksDelta int64) {
	cm.mu.Lock()
	if rec, ok := cm.clients[clientID]; ok {
		rec.BytesSent += bytesDelta
		rec.ChunksSent += chunksDelta
		rec.LastActive = time.Now()
	}
	cm.mu.Unlock()
}

func (cm *ClientManager) UpdatePosition(clientID string, secondsBehind float64) {
	cm.mu.Lock()
	if rec, ok := cm.clients[clientID]; ok {
		rec.BufferSecondsBehind = secondsBehind
	}
	cm.mu.Unlock()
}

func (cm *ClientManager) HeartbeatHLSClient(clientID, ip, userAgent string, bytesDelta int64) {
	cm.mu.Lock()
	rec, exists := cm.clients[clientID]
	now := time.Now()
	if !exists {
		rec = &ClientRecord{
			ID:              clientID,
			IP:              ip,
			UserAgent:       userAgent,
			ConnectedAt:     now,
			LastActive:      now,
			WorkerID:        cm.workerID,
			PrevUpdatedAt:   now,
			LastRequestKind: "HLS",
		}
		cm.clients[clientID] = rec
		cm.hadClients = true
	}

	if bytesDelta > 0 {
		dt := now.Sub(rec.PrevUpdatedAt).Seconds()
		if dt > 0 {
			instantBPS := float64(bytesDelta) * 8.0 / dt
			if rec.BPS == 0 {
				rec.BPS = instantBPS
			} else {
				const alpha = 0.3
				rec.BPS = (rec.BPS * (1.0 - alpha)) + (instantBPS * alpha)
			}
		}
		rec.PrevBytesSent = rec.BytesSent
		rec.PrevUpdatedAt = now
	}

	rec.LastActive = now
	rec.BytesSent += bytesDelta
	rec.ChunksSent++
	rec.RequestsTotal++
	cm.mu.Unlock()

	ctx := context.Background()
	ttl := config.C.Load().ClientRecordTTL
	key := rediskeys.ClientMetadata(cm.contentID, clientID)

	if !exists {
		cm.rdb.HSet(ctx, key,
			"client_id", clientID,
			"stream_id", cm.contentID,
			"ip_address", ip,
			"user_agent", userAgent,
			"protocol", "HLS",
			"connected_at", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
			"last_active", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
			"bytes_sent", fmt.Sprintf("%d", bytesDelta),
			"chunks_sent", "1",
			"requests_total", "1",
			"worker_id", cm.workerID,
			"bps", fmt.Sprintf("%.2f", rec.BPS),
			"stats_updated_at", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
		)
		setKey := rediskeys.Clients(cm.contentID)
		cm.rdb.SAdd(ctx, setKey, clientID)
		cm.rdb.Expire(ctx, setKey, ttl)
		slog.Info("hls client connected", "stream", cm.contentID, "client", clientID, "ip", ip)
		telemetry.DefaultTelemetry.ObserveConnect("HLS")
	} else {
		cm.rdb.HSet(ctx, key,
			"last_active", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
			"bytes_sent", fmt.Sprintf("%d", rec.BytesSent),
			"chunks_sent", fmt.Sprintf("%d", rec.ChunksSent),
			"requests_total", fmt.Sprintf("%d", rec.RequestsTotal),
			"bps", fmt.Sprintf("%.2f", rec.BPS),
			"stats_updated_at", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
		)
	}
	cm.rdb.Expire(ctx, key, ttl)
}

func (cm *ClientManager) Stop() {
	cm.stopOnce.Do(func() { close(cm.stopCh) })
}

func (cm *ClientManager) heartbeatLoop() {
	interval := config.C.Load().ClientHeartbeatInterval
	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-cm.stopCh:
			return
		case <-ticker.C:
			if newInterval := config.C.Load().ClientHeartbeatInterval; newInterval != interval {
				ticker.Reset(newInterval)
				interval = newInterval
			}
			cm.sendHeartbeats()
			cm.evictGhosts()
		}
	}
}

func (cm *ClientManager) sendHeartbeats() {
	select {
	case <-cm.stopCh:
		return
	default:
	}

	cm.mu.Lock()
	now := time.Now()
	clients := make([]*ClientRecord, 0, len(cm.clients))
	for _, rec := range cm.clients {
		if rec.LastRequestKind == "TS" {
			dt := now.Sub(rec.PrevUpdatedAt).Seconds()
			if dt >= 1.0 {
				deltaBytes := rec.BytesSent - rec.PrevBytesSent
				instantBPS := float64(deltaBytes) * 8.0 / dt

				if rec.BPS == 0 {
					rec.BPS = instantBPS
				} else {
					const alpha = 0.3
					rec.BPS = (rec.BPS * (1.0 - alpha)) + (instantBPS * alpha)
				}

				rec.PrevBytesSent = rec.BytesSent
				rec.PrevUpdatedAt = now
			}
		}
		clients = append(clients, rec)
	}
	cm.mu.Unlock()

	if len(clients) == 0 {
		return
	}

	ctx := context.Background()
	ttl := config.C.Load().ClientRecordTTL
	setKey := rediskeys.Clients(cm.contentID)

	nowStr := fmt.Sprintf("%f", float64(time.Now().UnixNano())/1e9)
	pipe := cm.rdb.Pipeline()
	for _, rec := range clients {
		key := rediskeys.ClientMetadata(cm.contentID, rec.ID)
		lastActive := fmt.Sprintf("%f", float64(rec.LastActive.UnixNano())/1e9)
		pipe.HSet(ctx, key,
			"last_active", lastActive,
			"bytes_sent", fmt.Sprintf("%d", rec.BytesSent),
			"bps", fmt.Sprintf("%.2f", rec.BPS),
			"chunks_sent", fmt.Sprintf("%d", rec.ChunksSent),
			"requests_total", fmt.Sprintf("%d", rec.RequestsTotal),
			"buffer_seconds_behind", fmt.Sprintf("%.3f", rec.BufferSecondsBehind),
			"stats_updated_at", nowStr,
		)
		pipe.Expire(ctx, key, ttl)
		pipe.SAdd(ctx, setKey, rec.ID)
	}
	pipe.Expire(ctx, setKey, ttl)
	pipe.Exec(ctx) //nolint:errcheck

	ownerKey := rediskeys.StreamOwner(cm.contentID)
	cm.rdb.Set(ctx, ownerKey, cm.workerID, 5*time.Minute)
}

func (cm *ClientManager) evictGhosts() {
	tsGhostTimeout := config.C.Load().ClientHeartbeatInterval * 5
	hlsGhostTimeout := config.C.Load().HLSClientIdleTimeout

	cm.mu.Lock()
	if cm.isInitialBuffering {
		cm.mu.Unlock()
		return
	}

	type eviction struct {
		id       string
		protocol string
	}
	var ghosts []eviction
	now := time.Now()
	for id, rec := range cm.clients {
		timeout := tsGhostTimeout
		if rec.LastRequestKind == "HLS" {
			timeout = hlsGhostTimeout
		}
		if now.Sub(rec.LastActive) > timeout {
			ghosts = append(ghosts, eviction{id: id, protocol: rec.LastRequestKind})
		}
	}
	for _, g := range ghosts {
		delete(cm.clients, g.id)
	}
	cm.mu.Unlock()

	if len(ghosts) > 0 {
		slog.Debug("evicted ghost clients", "stream", cm.contentID, "count", len(ghosts))
		ctx := context.Background()
		for _, g := range ghosts {
			cm.rdb.SRem(ctx, rediskeys.Clients(cm.contentID), g.id)
			cm.rdb.Del(ctx, rediskeys.ClientMetadata(cm.contentID, g.id))
			telemetry.DefaultTelemetry.ObserveDisconnect(g.protocol)
		}

		cm.mu.RLock()
		currentCount := len(cm.clients)
		cm.mu.RUnlock()
		if currentCount == 0 {
			cm.rdb.Set(ctx, rediskeys.LastClientDisconnect(cm.contentID),
				fmt.Sprintf("%f", float64(time.Now().UnixNano())/1e9),
				config.C.Load().ClientRecordTTL)
		}
	}
}
