package hls

import (
	"container/list"
	"sync"
	"time"

	"github.com/acestream/acestream/internal/config"
)

// GlobalCache provides a thread-safe, size-bounded LRU cache for HLS segments.
// It enforces both a strict memory limit (evicting oldest on overflow) and TTL.
type GlobalCache struct {
	mu          sync.Mutex
	maxBytes    int64
	totalBytes  int64
	entries     map[string]*list.Element
	evictList   *list.List
	stopCh      chan struct{}
	once        sync.Once
}

type cacheEntry struct {
	key     string
	data    []byte
	expires time.Time
}

// DefaultCache is the singleton cache instance used by the HTTP HLS proxy.
var DefaultCache *GlobalCache

func init() {
	// Allocate up to 20% of max memory for the HLS HTTP cache.
	limitMB := int64(config.C.Load().MaxMemoryMB) / 5
	if limitMB < 50 {
		limitMB = 50 // minimum 50MB
	}
	DefaultCache = NewGlobalCache(limitMB * 1024 * 1024)
}

// NewGlobalCache creates a new cache with the specified byte limit.
func NewGlobalCache(maxBytes int64) *GlobalCache {
	gc := &GlobalCache{
		maxBytes:   maxBytes,
		entries:    make(map[string]*list.Element),
		evictList:  list.New(),
		stopCh:     make(chan struct{}),
	}
	go gc.gcLoop()
	return gc
}

// Stop halts the background garbage collection.
func (gc *GlobalCache) Stop() {
	gc.once.Do(func() { close(gc.stopCh) })
}

// Get retrieves data if present and unexpired. Refreshes LRU order.
func (gc *GlobalCache) Get(key string) ([]byte, bool) {
	gc.mu.Lock()
	defer gc.mu.Unlock()

	if ent, ok := gc.entries[key]; ok {
		entry := ent.Value.(*cacheEntry)
		if time.Now().After(entry.expires) {
			gc.removeElement(ent)
			return nil, false
		}
		gc.evictList.MoveToFront(ent)
		return entry.data, true
	}
	return nil, false
}

// Set adds or updates data, evicting oldest items if maxBytes is exceeded.
func (gc *GlobalCache) Set(key string, data []byte, ttl time.Duration) {
	gc.mu.Lock()
	defer gc.mu.Unlock()

	if ent, ok := gc.entries[key]; ok {
		gc.evictList.MoveToFront(ent)
		entry := ent.Value.(*cacheEntry)
		gc.totalBytes -= int64(len(entry.data))
		entry.data = data
		entry.expires = time.Now().Add(ttl)
		gc.totalBytes += int64(len(data))
	} else {
		entry := &cacheEntry{
			key:     key,
			data:    data,
			expires: time.Now().Add(ttl),
		}
		ent := gc.evictList.PushFront(entry)
		gc.entries[key] = ent
		gc.totalBytes += int64(len(data))
	}

	// Enforce memory bound
	for gc.maxBytes > 0 && gc.totalBytes > gc.maxBytes && gc.evictList.Len() > 0 {
		gc.removeOldest()
	}
}

// TotalBytes returns the current memory usage of the cache.
func (gc *GlobalCache) TotalBytes() int64 {
	gc.mu.Lock()
	defer gc.mu.Unlock()
	return gc.totalBytes
}

func (gc *GlobalCache) removeOldest() {
	if ent := gc.evictList.Back(); ent != nil {
		gc.removeElement(ent)
	}
}

func (gc *GlobalCache) removeElement(e *list.Element) {
	gc.evictList.Remove(e)
	entry := e.Value.(*cacheEntry)
	delete(gc.entries, entry.key)
	gc.totalBytes -= int64(len(entry.data))
}

func (gc *GlobalCache) gcLoop() {
	t := time.NewTicker(30 * time.Second)
	defer t.Stop()
	for {
		select {
		case <-gc.stopCh:
			return
		case <-t.C:
			now := time.Now()
			gc.mu.Lock()
			// Scan from oldest (Back) to newest (Front)
			for e := gc.evictList.Back(); e != nil; {
				prev := e.Prev()
				if now.After(e.Value.(*cacheEntry).expires) {
					gc.removeElement(e)
				}
				e = prev
			}
			gc.mu.Unlock()
		}
	}
}
