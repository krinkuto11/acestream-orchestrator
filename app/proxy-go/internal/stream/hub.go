package stream

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/acestream/proxy/internal/buffer"
	"github.com/acestream/proxy/internal/config"
	"github.com/acestream/proxy/internal/hls"
	"github.com/acestream/proxy/internal/rediskeys"
)

// Hub is the global registry of active streams. One Hub per process.
type Hub struct {
	workerID string
	rdb      *redis.Client

	mu       sync.RWMutex
	streams  map[string]*streamEntry
}

type streamEntry struct {
	manager    *Manager
	buf        *buffer.RingBuffer
	clients    *ClientManager
	cancelFn   context.CancelFunc
	shutdownAt time.Time // non-zero: stream will stop at this time if no clients
	segmenter  *hls.Segmenter // non-nil only in API mode after first HLS manifest request
}

// NewHub creates a Hub and starts background maintenance goroutines.
func NewHub(rdb *redis.Client) *Hub {
	h := &Hub{
		workerID: workerID(),
		rdb:      rdb,
		streams:  make(map[string]*streamEntry),
	}
	go h.cleanupLoop()
	return h
}

// StartStream ensures a stream is running. If it already exists and is healthy,
// this is a no-op. Returns true if a new stream was started.
//
// NOTE: The stream's internal context is always rooted at context.Background(),
// NOT at the caller-provided ctx. This is intentional: streams must outlive the
// HTTP request that started them (e.g. in HLS mode the handler returns immediately
// after issuing a redirect, which would otherwise cancel the stream goroutine).
// The caller's ctx is accepted for API compatibility but is not used for the stream
// lifecycle — only for the initial Redis ownership write.
func (h *Hub) StartStream(ctx context.Context, p StreamParams) bool {
	h.mu.Lock()
	defer h.mu.Unlock()

	contentID := p.ContentID

	if e, ok := h.streams[contentID]; ok {
		// Already running — cancel any pending shutdown timer
		e.shutdownAt = time.Time{}
		if p.Bitrate > 0 && e.manager.Bitrate() == 0 {
			e.manager.mu.Lock()
			e.manager.bitrate = p.Bitrate
			e.manager.mu.Unlock()
		}
		slog.Debug("stream already active", "stream", contentID)
		return false
	}

	buf := buffer.New(config.C.BufferChunkSize, 16)
	cm := newClientManager(contentID, h.workerID, h.rdb)
	mgr := newManager(p, buf, cm, h)

	// Always use context.Background() for stream lifecycle — never the request
	// context, which is cancelled as soon as the HTTP handler returns.
	streamCtx, cancel := context.WithCancel(context.Background())
	h.streams[contentID] = &streamEntry{
		manager:  mgr,
		buf:      buf,
		clients:  cm,
		cancelFn: cancel,
	}

	// Claim ownership in Redis
	h.rdb.Set(ctx, rediskeys.StreamOwner(contentID), h.workerID, 5*time.Minute)
	h.rdb.Set(ctx, rediskeys.StreamInitTime(contentID), fmt.Sprintf("%f", float64(time.Now().UnixNano())/1e9), time.Hour)

	go func() {
		mgr.Run(streamCtx)
		slog.Info("stream manager exited", "stream", contentID)
		h.removeStream(contentID)
	}()

	slog.Info("stream started", "stream", contentID, "engine", fmt.Sprintf("%s:%d", p.Engine.Host, p.Engine.Port))
	return true
}

// StopStream stops a stream immediately.
func (h *Hub) StopStream(contentID string) {
	h.mu.Lock()
	e, ok := h.streams[contentID]
	if ok {
		delete(h.streams, contentID)
	}
	h.mu.Unlock()

	if ok {
		e.cancelFn()
		e.clients.Stop()
		e.buf.Stop()
		if e.segmenter != nil {
			e.segmenter.Stop()
		}
		h.flushRedis(contentID)
		slog.Info("stream stopped", "stream", contentID)
	}
}

// ScheduleShutdown marks a stream to stop after delay if no clients connect.
func (h *Hub) ScheduleShutdown(contentID string, delay time.Duration) {
	h.mu.Lock()
	if e, ok := h.streams[contentID]; ok {
		e.shutdownAt = time.Now().Add(delay)
	}
	h.mu.Unlock()
}

// CancelShutdown prevents a pending shutdown (e.g. a new client reconnected).
func (h *Hub) CancelShutdown(contentID string) {
	h.mu.Lock()
	if e, ok := h.streams[contentID]; ok {
		e.shutdownAt = time.Time{}
	}
	h.mu.Unlock()
}

// GetEntry returns the buffer and client manager for an active stream, or nil.
func (h *Hub) GetEntry(contentID string) (*Manager, *buffer.RingBuffer, *ClientManager) {
	h.mu.RLock()
	e, ok := h.streams[contentID]
	h.mu.RUnlock()
	if !ok {
		return nil, nil, nil
	}
	return e.manager, e.buf, e.clients
}

// HotSwap migrates a stream to a new engine without dropping clients.
func (h *Hub) HotSwap(contentID string, ep EngineParams) bool {
	h.mu.RLock()
	e, ok := h.streams[contentID]
	h.mu.RUnlock()
	if !ok {
		return false
	}
	e.manager.HotSwap(ep)
	return true
}

// WorkerID returns this hub's worker identifier.
func (h *Hub) WorkerID() string { return h.workerID }

// IsOwner checks whether this process owns the stream's Redis key.
func (h *Hub) IsOwner(contentID string) bool {
	val, err := h.rdb.Get(context.Background(), rediskeys.StreamOwner(contentID)).Result()
	if err != nil {
		return false
	}
	return val == h.workerID
}

func (h *Hub) removeStream(contentID string) {
	h.mu.Lock()
	e, ok := h.streams[contentID]
	if ok {
		delete(h.streams, contentID)
	}
	h.mu.Unlock()
	if ok {
		e.clients.Stop()
		e.buf.Stop()
		if e.segmenter != nil {
			e.segmenter.Stop()
		}
		h.flushRedis(contentID)
	}
}

// GetOrCreateSegmenter returns the HLS segmenter for a stream, creating it if
// it doesn't exist yet. Only called in API mode. Safe for concurrent use.
func (h *Hub) GetOrCreateSegmenter(contentID string, buf *buffer.RingBuffer) *hls.Segmenter {
	h.mu.Lock()
	defer h.mu.Unlock()
	e, ok := h.streams[contentID]
	if !ok {
		return nil
	}
	if e.segmenter == nil {
		e.segmenter = hls.NewSegmenter(contentID, buf)
	}
	return e.segmenter
}

// GetSegmenter returns the active HLS segmenter for a stream, or nil.
func (h *Hub) GetSegmenter(contentID string) *hls.Segmenter {
	h.mu.RLock()
	e, ok := h.streams[contentID]
	h.mu.RUnlock()
	if !ok {
		return nil
	}
	return e.segmenter
}

func (h *Hub) cleanupLoop() {
	ticker := time.NewTicker(config.C.CleanupInterval)
	defer ticker.Stop()
	for range ticker.C {
		h.runCleanup()
	}
}

func (h *Hub) runCleanup() {
	h.mu.Lock()
	now := time.Now()
	var toStop []string
	for id, e := range h.streams {
		// Refresh ownership TTL
		h.rdb.Set(context.Background(), rediskeys.StreamOwner(id), h.workerID, 5*time.Minute)

		// Check pending shutdown
		if !e.shutdownAt.IsZero() && now.After(e.shutdownAt) && e.clients.LocalCount() == 0 {
			toStop = append(toStop, id)
		}
	}
	h.mu.Unlock()

	for _, id := range toStop {
		slog.Info("cleanup: stopping idle stream", "stream", id)
		h.StopStream(id)
	}
}

func (h *Hub) flushRedis(contentID string) {
	ctx := context.Background()
	keys := []string{
		rediskeys.StreamOwner(contentID),
		rediskeys.StreamMetadata(contentID),
		rediskeys.BufferIndex(contentID),
		rediskeys.StreamStopping(contentID),
		rediskeys.Clients(contentID),
		rediskeys.LastClientDisconnect(contentID),
		rediskeys.ConnectionAttempt(contentID),
		rediskeys.LastData(contentID),
		rediskeys.StreamInitTime(contentID),
		rediskeys.StreamActivity(contentID),
	}
	h.rdb.Del(ctx, keys...)

	// Wildcard chunk keys — use SCAN
	iter := h.rdb.Scan(ctx, 0, rediskeys.BufferChunkPrefix(contentID)+"*", 500).Iterator()
	for iter.Next(ctx) {
		h.rdb.Del(ctx, iter.Val())
	}
}

func workerID() string {
	host, _ := os.Hostname()
	return fmt.Sprintf("%s:%d", host, os.Getpid())
}
