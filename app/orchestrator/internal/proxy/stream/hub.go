package stream

import (
	"context"
	"fmt"
	"log/slog"
	"os"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"

	"github.com/acestream/acestream/internal/proxy/buffer"
	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/proxy/hls"
	"github.com/acestream/acestream/internal/rediskeys"
)

// Hub is the global registry of active streams.
type Hub struct {
	workerID string
	rdb      *redis.Client
	sink     EventSink

	mu      sync.RWMutex
	streams map[string]*streamEntry

	stopCh chan struct{}
}

type streamEntry struct {
	manager    *Manager
	buf        *buffer.RingBuffer
	clients    *ClientManager
	cancelFn   context.CancelFunc
	shutdownAt time.Time
	segmenter  *hls.Segmenter
}

// NewHub creates a Hub and starts background maintenance goroutines.
// sink receives stream lifecycle events; pass nil for a no-op sink.
func NewHub(rdb *redis.Client, sink EventSink) *Hub {
	if sink == nil {
		sink = noopSink{}
	}
	h := &Hub{
		workerID: workerID(),
		rdb:      rdb,
		sink:     sink,
		streams:  make(map[string]*streamEntry),
		stopCh:   make(chan struct{}),
	}
	go h.cleanupLoop()
	return h
}

func (h *Hub) Stop() {
	close(h.stopCh)
}

func (h *Hub) StartStream(ctx context.Context, p StreamParams) bool {
	h.mu.Lock()
	defer h.mu.Unlock()

	contentID := p.ContentID

	if e, ok := h.streams[contentID]; ok {
		e.shutdownAt = time.Time{}
		if p.Bitrate > 0 && e.manager.Bitrate() == 0 {
			e.manager.mu.Lock()
			e.manager.bitrate = p.Bitrate
			e.manager.mu.Unlock()
		}
		slog.Debug("stream already active", "stream", contentID)
		return false
	}

	if h.atGlobalLimits() {
		if !h.evictOldestIdle() {
			slog.Warn("resource limits reached, cannot start new stream", "stream", contentID)
			return false
		}
	}

	buf := buffer.New(config.C.Load().BufferChunkSize, 16)
	cm := newClientManager(contentID, h.workerID, h.rdb)
	mgr := newManager(p, buf, cm, h, h.sink)

	streamCtx, cancel := context.WithCancel(context.Background())
	h.streams[contentID] = &streamEntry{
		manager:  mgr,
		buf:      buf,
		clients:  cm,
		cancelFn: cancel,
	}

	bgCtx := context.Background()
	h.rdb.Set(bgCtx, rediskeys.StreamOwner(contentID), h.workerID, 5*time.Minute)
	h.rdb.Set(bgCtx, rediskeys.StreamInitTime(contentID), fmt.Sprintf("%f", float64(time.Now().UnixNano())/1e9), time.Hour)

	go func() {
		mgr.Run(streamCtx)
		slog.Info("stream manager exited", "stream", contentID)
		h.removeStream(contentID)
	}()

	slog.Info("stream started", "stream", contentID, "engine", fmt.Sprintf("%s:%d", p.Engine.Host, p.Engine.Port))
	return true
}

func (h *Hub) atGlobalLimits() bool {
	count := len(h.streams)
	if config.C.Load().MaxTotalStreams > 0 && count >= config.C.Load().MaxTotalStreams {
		return true
	}

	if config.C.Load().MaxMemoryMB > 0 {
		var totalMemBytes int64
		for _, e := range h.streams {
			totalMemBytes += int64(e.buf.TargetChunkSize() * 16)
			if e.segmenter != nil {
				totalMemBytes += e.segmenter.MemoryUsage()
			}
		}
		if hls.DefaultCache != nil {
			totalMemBytes += hls.DefaultCache.TotalBytes()
		}
		if totalMemBytes >= int64(config.C.Load().MaxMemoryMB)*1024*1024 {
			slog.Debug("global memory limit reached", "used_bytes", totalMemBytes, "limit_mb", config.C.Load().MaxMemoryMB)
			return true
		}
	}
	return false
}

func (h *Hub) evictOldestIdle() bool {
	var oldestID string
	var oldestTime time.Time

	for id, e := range h.streams {
		if !e.shutdownAt.IsZero() {
			if oldestID == "" || e.shutdownAt.Before(oldestTime) {
				oldestID = id
				oldestTime = e.shutdownAt
			}
		}
	}

	if oldestID != "" {
		slog.Info("evicting idle stream due to resource limits", "stream", oldestID)
		h.removeStreamLocked(oldestID)
		return true
	}
	return false
}

func (h *Hub) removeStreamLocked(contentID string) {
	e, ok := h.streams[contentID]
	if ok {
		delete(h.streams, contentID)
		e.cancelFn()
		e.clients.Stop()
		e.buf.Stop()
		if e.segmenter != nil {
			e.segmenter.Stop()
		}
		go h.flushRedis(contentID)
	}
}

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

func (h *Hub) ScheduleShutdown(contentID string, delay time.Duration) {
	h.mu.Lock()
	if e, ok := h.streams[contentID]; ok {
		e.shutdownAt = time.Now().Add(delay)
	}
	h.mu.Unlock()
}

func (h *Hub) CancelShutdown(contentID string) {
	h.mu.Lock()
	if e, ok := h.streams[contentID]; ok {
		e.shutdownAt = time.Time{}
	}
	h.mu.Unlock()
}

func (h *Hub) GetEntry(contentID string) (*Manager, *buffer.RingBuffer, *ClientManager) {
	h.mu.RLock()
	e, ok := h.streams[contentID]
	h.mu.RUnlock()
	if !ok {
		return nil, nil, nil
	}
	return e.manager, e.buf, e.clients
}

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

func (h *Hub) WorkerID() string { return h.workerID }

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
		e.cancelFn()
		e.clients.Stop()
		e.buf.Stop()
		if e.segmenter != nil {
			e.segmenter.Stop()
		}
		h.flushRedis(contentID)
	}
}

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
	ticker := time.NewTicker(config.C.Load().CleanupInterval)
	defer ticker.Stop()
	for {
		select {
		case <-h.stopCh:
			return
		case <-ticker.C:
			h.runCleanup()
		}
	}
}

func (h *Hub) runCleanup() {
	h.mu.Lock()
	now := time.Now()
	var toStop []string
	var toRefresh []string
	for id, e := range h.streams {
		toRefresh = append(toRefresh, id)

		localCount := e.clients.LocalCount()

		if !e.shutdownAt.IsZero() && now.After(e.shutdownAt) && localCount == 0 {
			toStop = append(toStop, id)
			continue
		}

		if e.shutdownAt.IsZero() && localCount == 0 && e.clients.HadClients() {
			e.shutdownAt = now.Add(config.C.Load().ChannelShutdownDelay)
		}
	}
	h.mu.Unlock()

	ctx := context.Background()
	for _, id := range toRefresh {
		h.rdb.Set(ctx, rediskeys.StreamOwner(id), h.workerID, 5*time.Minute)
	}

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

	iter := h.rdb.Scan(ctx, 0, rediskeys.BufferChunkPrefix(contentID)+"*", 500).Iterator()
	for iter.Next(ctx) {
		h.rdb.Del(ctx, iter.Val())
	}
}

func workerID() string {
	host, _ := os.Hostname()
	return fmt.Sprintf("%s:%d", host, os.Getpid())
}
