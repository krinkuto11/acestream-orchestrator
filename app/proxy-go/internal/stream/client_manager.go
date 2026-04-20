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

	mu      sync.RWMutex
	clients map[string]*ClientRecord

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
