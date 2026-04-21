package stream

import (
	"context"
	"fmt"
	"log/slog"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/acestream/proxy/internal/config"
	"github.com/acestream/proxy/internal/rediskeys"
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

	// Tracking fields for rate calculation
	PrevBytesSent int64
	PrevUpdatedAt time.Time
	BPS           float64 // smoothed bits per second

	// Extended telemetry
	RequestsTotal       int64
	BufferSecondsBehind float64
	IsPrebuffering      bool
	LastRequestKind     string
	LastSequence        int64
}

// ClientManager tracks active clients for one stream.
// It persists client presence to Redis so other proxy instances can see total counts.
type ClientManager struct {
	contentID string
	workerID  string
	rdb       *redis.Client

	mu         sync.RWMutex
	clients    map[string]*ClientRecord
	hadClients bool // true once at least one client (TS or HLS) has ever connected
	isInitialBuffering bool

	stopCh   chan struct{}
	stopOnce sync.Once
}

func newClientManager(contentID, workerID string, rdb *redis.Client) *ClientManager {
	cm := &ClientManager{
		contentID: contentID,
		workerID:  workerID,
		rdb:       rdb,
		clients:            make(map[string]*ClientRecord),
		stopCh:             make(chan struct{}),
		isInitialBuffering: true, // Start in buffering mode
	}
	go cm.heartbeatLoop()
	return cm
}

// SetInitialBuffering updates the buffering state. While true, ghost eviction is suspended.
func (cm *ClientManager) SetInitialBuffering(v bool) {
	cm.mu.Lock()
	cm.isInitialBuffering = v
	cm.mu.Unlock()
}

// Add registers a new client. Returns false if the stream is at max capacity.
func (cm *ClientManager) Add(clientID, ip, userAgent string, initialIndex int64) bool {
	cm.mu.Lock()
	defer cm.mu.Unlock()

	if len(cm.clients) >= config.C.MaxClientsPerStream() {
		slog.Warn("rejecting client: max capacity reached",
			"stream", cm.contentID, "client", clientID, "max", config.C.MaxClientsPerStream())
		return false
	}

	now := time.Now()
	cm.clients[clientID] = &ClientRecord{
		ID:            clientID,
		IP:            ip,
		UserAgent:     userAgent,
		ConnectedAt:   now,
		LastActive:    now,
		InitialIndex:  initialIndex,
		WorkerID:      cm.workerID,
		PrevUpdatedAt: now,
		RequestsTotal: 1,
		LastRequestKind: "TS",
	}
	cm.hadClients = true

	ctx := context.Background()
	ttl := config.C.ClientRecordTTL
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
		"requests_total", "1",
		"bps", "0",
		"stats_updated_at", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
	)
	cm.rdb.Expire(ctx, key, ttl)

	setKey := rediskeys.Clients(cm.contentID)
	cm.rdb.SAdd(ctx, setKey, clientID)
	cm.rdb.Expire(ctx, setKey, ttl)

	// Clear init/disconnect timing keys so cleanup logic doesn't fire
	cm.rdb.Del(ctx, rediskeys.StreamInitTime(cm.contentID))
	cm.rdb.Del(ctx, rediskeys.LastClientDisconnect(cm.contentID))

	slog.Info("client connected", "stream", cm.contentID, "client", clientID, "ip", ip,
		"local", len(cm.clients))
	return true
}

// Remove deregisters a client and records disconnect time when it's the last one.
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
			config.C.ClientRecordTTL)
	}

	slog.Info("client disconnected", "stream", cm.contentID, "client", clientID,
		"remaining_local", remaining)
}

// HadClients returns true once at least one client has ever connected.
// Used by the cleanup loop to distinguish streams that were never served from
// streams whose last client has since disconnected (or been ghost-evicted).
func (cm *ClientManager) HadClients() bool {
	cm.mu.RLock()
	v := cm.hadClients
	cm.mu.RUnlock()
	return v
}

// HasCapacity returns true if another client can be accepted. Used to gate
// response headers before committing them to the client.
func (cm *ClientManager) HasCapacity() bool {
	cm.mu.RLock()
	ok := len(cm.clients) < config.C.MaxClientsPerStream()
	cm.mu.RUnlock()
	return ok
}

// LocalCount returns the number of clients managed by this instance.
func (cm *ClientManager) LocalCount() int {
	cm.mu.RLock()
	n := len(cm.clients)
	cm.mu.RUnlock()
	return n
}

// TotalCount queries Redis for the total across all proxy instances.
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

// UpdateStats updates bytes/chunks sent for a client (local only; periodic flush handled by heartbeat).
func (cm *ClientManager) UpdateStats(clientID string, bytesDelta, chunksDelta int64) {
	cm.mu.Lock()
	if rec, ok := cm.clients[clientID]; ok {
		rec.BytesSent += bytesDelta
		rec.ChunksSent += chunksDelta
		rec.LastActive = time.Now()
	}
	cm.mu.Unlock()
}

// UpdatePosition stores the client's current runway in seconds (buffered data ahead of playhead).
// Called from ClientStreamer's delivery loop; flushed to Redis by the heartbeat.
func (cm *ClientManager) UpdatePosition(clientID string, secondsBehind float64) {
	cm.mu.Lock()
	if rec, ok := cm.clients[clientID]; ok {
		rec.BufferSecondsBehind = secondsBehind
	}
	cm.mu.Unlock()
}

// HeartbeatHLSClient records activity for an HLS client (manifest or segment
// fetch). HLS clients never go through ClientStreamer, so they must be
// registered here to be visible to the Python telemetry UI.
//
// The client is added to the Redis set + metadata hash on first call (the same
// keys that client_tracking_service.get_stream_clients reads), then the
// last_active timestamp and stats are refreshed on every call.
func (cm *ClientManager) HeartbeatHLSClient(clientID, ip, userAgent string, bytesDelta int64) {
	cm.mu.Lock()
	rec, exists := cm.clients[clientID]
	now := time.Now()
	if !exists {
		rec = &ClientRecord{
			ID:            clientID,
			IP:            ip,
			UserAgent:     userAgent,
			ConnectedAt:   now,
			LastActive:    now,
			WorkerID:      cm.workerID,
			PrevUpdatedAt: now,
		}
		rec.LastRequestKind = "HLS"
		cm.clients[clientID] = rec
		cm.hadClients = true
	}

	// BPS calculation with EMA smoothing (alpha=0.3)
	if bytesDelta > 0 {
		dt := now.Sub(rec.PrevUpdatedAt).Seconds()
		if dt > 0 {
			instantBPS := float64(bytesDelta) * 8.0 / dt
			if rec.BPS == 0 {
				rec.BPS = instantBPS
			} else {
				alpha := 0.3
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
	ttl := config.C.ClientRecordTTL
	key := rediskeys.ClientMetadata(cm.contentID, clientID)

	if !exists {
		// First time: full registration so Python tracker can hydrate the row.
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
	} else {
		// Incremental update: refresh presence fields only.
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

// Stop halts the heartbeat goroutine. Safe to call multiple times.
func (cm *ClientManager) Stop() {
	cm.stopOnce.Do(func() { close(cm.stopCh) })
}

func (cm *ClientManager) heartbeatLoop() {
	ticker := time.NewTicker(config.C.ClientHeartbeatInterval)
	defer ticker.Stop()

	for {
		select {
		case <-cm.stopCh:
			return
		case <-ticker.C:
			cm.sendHeartbeats()
			cm.evictGhosts()
		}
	}
}

func (cm *ClientManager) sendHeartbeats() {
	// Don't refresh Redis keys if the stream has already been stopped.
	select {
	case <-cm.stopCh:
		return
	default:
	}

	cm.mu.RLock()
	clients := make([]*ClientRecord, 0, len(cm.clients))
	for _, rec := range cm.clients {
		clients = append(clients, rec)
	}
	cm.mu.RUnlock()

	if len(clients) == 0 {
		return
	}

	ctx := context.Background()
	ttl := config.C.ClientRecordTTL
	setKey := rediskeys.Clients(cm.contentID)

	nowStr := fmt.Sprintf("%f", float64(time.Now().UnixNano())/1e9)
	pipe := cm.rdb.Pipeline()
	for _, rec := range clients {
		key := rediskeys.ClientMetadata(cm.contentID, rec.ID)
		lastActive := fmt.Sprintf("%f", float64(rec.LastActive.UnixNano())/1e9)
		pipe.HSet(ctx, key,
			"last_active", lastActive,
			"bps", fmt.Sprintf("%.2f", rec.BPS),
			"chunks_sent", fmt.Sprintf("%d", rec.ChunksSent),
			"buffer_seconds_behind", fmt.Sprintf("%.3f", rec.BufferSecondsBehind),
			"stats_updated_at", nowStr,
		)
		pipe.Expire(ctx, key, ttl)
		pipe.SAdd(ctx, setKey, rec.ID)
	}
	pipe.Expire(ctx, setKey, ttl)
	pipe.Exec(ctx) //nolint:errcheck

	// Refresh owner key too
	ownerKey := rediskeys.StreamOwner(cm.contentID)
	cm.rdb.Set(ctx, ownerKey, cm.workerID, 5*time.Minute)
}

func (cm *ClientManager) evictGhosts() {
	tsGhostTimeout := config.C.ClientHeartbeatInterval * 5
	hlsGhostTimeout := config.C.HLSClientIdleTimeout

	cm.mu.Lock()
	if cm.isInitialBuffering {
		cm.mu.Unlock()
		return
	}

	var ghosts []string
	now := time.Now()
	for id, rec := range cm.clients {
		timeout := tsGhostTimeout
		if rec.LastRequestKind == "HLS" {
			timeout = hlsGhostTimeout
		}
		if now.Sub(rec.LastActive) > timeout {
			ghosts = append(ghosts, id)
		}
	}
	for _, id := range ghosts {
		delete(cm.clients, id)
	}
	cm.mu.Unlock()

	if len(ghosts) > 0 {
		slog.Debug("evicted ghost clients", "stream", cm.contentID, "count", len(ghosts))
		ctx := context.Background()
		for _, id := range ghosts {
			cm.rdb.SRem(ctx, rediskeys.Clients(cm.contentID), id)
			cm.rdb.Del(ctx, rediskeys.ClientMetadata(cm.contentID, id))
		}

		// Re-read the count under the lock to avoid a TOCTOU race with Add():
		// a new client may have connected between when we released the lock and now.
		cm.mu.RLock()
		currentCount := len(cm.clients)
		cm.mu.RUnlock()
		if currentCount == 0 {
			cm.rdb.Set(ctx, rediskeys.LastClientDisconnect(cm.contentID),
				fmt.Sprintf("%f", float64(time.Now().UnixNano())/1e9),
				config.C.ClientRecordTTL)
		}
	}
}
