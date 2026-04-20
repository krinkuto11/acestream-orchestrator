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

	stopCh chan struct{}
}

func newClientManager(contentID, workerID string, rdb *redis.Client) *ClientManager {
	cm := &ClientManager{
		contentID: contentID,
		workerID:  workerID,
		rdb:       rdb,
		clients:   make(map[string]*ClientRecord),
		stopCh:    make(chan struct{}),
	}
	go cm.heartbeatLoop()
	return cm
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
		ID:           clientID,
		IP:           ip,
		UserAgent:    userAgent,
		ConnectedAt:  now,
		LastActive:   now,
		InitialIndex: initialIndex,
		WorkerID:     cm.workerID,
	}
	cm.hadClients = true

	ctx := context.Background()
	ttl := config.C.ClientRecordTTL
	key := rediskeys.ClientMetadata(cm.contentID, clientID)
	cm.rdb.HSet(ctx, key,
		"ip_address", ip,
		"user_agent", userAgent,
		"connected_at", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
		"last_active", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
		"initial_index", fmt.Sprintf("%d", initialIndex),
		"worker_id", cm.workerID,
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
			ID:          clientID,
			IP:          ip,
			UserAgent:   userAgent,
			ConnectedAt: now,
			LastActive:  now,
			WorkerID:    cm.workerID,
		}
		cm.clients[clientID] = rec
		cm.hadClients = true
	}
	rec.LastActive = now
	rec.BytesSent += bytesDelta
	rec.ChunksSent++
	cm.mu.Unlock()

	ctx := context.Background()
	ttl := config.C.ClientRecordTTL
	key := rediskeys.ClientMetadata(cm.contentID, clientID)

	if !exists {
		// First time: full registration so Python tracker can hydrate the row.
		cm.rdb.HSet(ctx, key,
			"ip_address", ip,
			"user_agent", userAgent,
			"protocol", "HLS",
			"connected_at", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
			"last_active", fmt.Sprintf("%f", float64(now.UnixNano())/1e9),
			"bytes_sent", fmt.Sprintf("%d", bytesDelta),
			"chunks_sent", "1",
			"requests_total", "1",
			"worker_id", cm.workerID,
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
			"requests_total", fmt.Sprintf("%d", rec.ChunksSent),
		)
	}
	cm.rdb.Expire(ctx, key, ttl)
}

// Stop halts the heartbeat goroutine.
func (cm *ClientManager) Stop() {
	close(cm.stopCh)
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
	cm.mu.RLock()
	ids := make([]string, 0, len(cm.clients))
	for id := range cm.clients {
		ids = append(ids, id)
	}
	cm.mu.RUnlock()

	if len(ids) == 0 {
		return
	}

	ctx := context.Background()
	now := fmt.Sprintf("%f", float64(time.Now().UnixNano())/1e9)
	ttl := config.C.ClientRecordTTL
	setKey := rediskeys.Clients(cm.contentID)

	pipe := cm.rdb.Pipeline()
	for _, id := range ids {
		key := rediskeys.ClientMetadata(cm.contentID, id)
		pipe.HSet(ctx, key, "last_active", now)
		pipe.Expire(ctx, key, ttl)
		pipe.SAdd(ctx, setKey, id)
	}
	pipe.Expire(ctx, setKey, ttl)
	pipe.Exec(ctx) //nolint:errcheck

	// Refresh owner key too
	ownerKey := rediskeys.StreamOwner(cm.contentID)
	cm.rdb.Set(ctx, ownerKey, cm.workerID, 5*time.Minute)
}

func (cm *ClientManager) evictGhosts() {
	ghostTimeout := config.C.ClientHeartbeatInterval * 5

	cm.mu.Lock()
	var ghosts []string
	now := time.Now()
	for id, rec := range cm.clients {
		if now.Sub(rec.LastActive) > ghostTimeout {
			ghosts = append(ghosts, id)
		}
	}
	for _, id := range ghosts {
		delete(cm.clients, id)
	}
	cm.mu.Unlock()

	if len(ghosts) > 0 {
		slog.Warn("evicted ghost clients", "stream", cm.contentID, "count", len(ghosts))
		ctx := context.Background()
		for _, id := range ghosts {
			cm.rdb.SRem(ctx, rediskeys.Clients(cm.contentID), id)
			cm.rdb.Del(ctx, rediskeys.ClientMetadata(cm.contentID, id))
		}
	}
}
