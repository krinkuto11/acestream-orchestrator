package stream

import (
	"context"
	"testing"
	"time"

	"github.com/alicebob/miniredis/v2"
	"github.com/redis/go-redis/v9"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/proxy/buffer"
)

// testHub creates a Hub backed by an in-process miniredis server.
func testHub(t *testing.T) (*Hub, *miniredis.Miniredis) {
	t.Helper()
	mr := miniredis.RunT(t)
	rdb := redis.NewClient(&redis.Options{Addr: mr.Addr()})
	h := NewHub(rdb, nil)
	t.Cleanup(func() {
		h.Stop()
		rdb.Close()
	})
	return h, mr
}

// injectStream adds a pre-built streamEntry into the hub without going through
// StartStream (which launches goroutines and makes Redis calls).
func injectStream(h *Hub, contentID string, shutdownAt time.Time) (*buffer.RingBuffer, func()) {
	buf := buffer.New(188*10, 4)
	_, cancel := context.WithCancel(context.Background())
	cm := &ClientManager{
		contentID: contentID,
		clients:   make(map[string]*ClientRecord),
		stopCh:    make(chan struct{}),
	}
	h.mu.Lock()
	h.streams[contentID] = &streamEntry{
		buf:        buf,
		clients:    cm,
		cancelFn:   cancel,
		shutdownAt: shutdownAt,
	}
	h.mu.Unlock()
	return buf, func() {
		buf.Stop()
		cancel()
	}
}

// ── atGlobalLimits ────────────────────────────────────────────────────────────

func TestHub_AtGlobalLimits_StreamCount(t *testing.T) {
	h, _ := testHub(t)

	orig := config.C.Load()
	cfg := *orig
	cfg.MaxTotalStreams = 2
	cfg.MaxMemoryMB = 0
	config.C.Store(&cfg)
	defer config.C.Store(orig)

	_, cleanup1 := injectStream(h, "s1", time.Time{})
	_, cleanup2 := injectStream(h, "s2", time.Time{})
	defer cleanup1()
	defer cleanup2()

	h.mu.RLock()
	atLimit := h.atGlobalLimits()
	h.mu.RUnlock()
	if !atLimit {
		t.Error("atGlobalLimits must return true when stream count equals MaxTotalStreams")
	}
}

func TestHub_AtGlobalLimits_BelowCount(t *testing.T) {
	h, _ := testHub(t)

	orig := config.C.Load()
	cfg := *orig
	cfg.MaxTotalStreams = 5
	cfg.MaxMemoryMB = 0
	config.C.Store(&cfg)
	defer config.C.Store(orig)

	_, cleanup := injectStream(h, "s1", time.Time{})
	defer cleanup()

	h.mu.RLock()
	atLimit := h.atGlobalLimits()
	h.mu.RUnlock()
	if atLimit {
		t.Error("atGlobalLimits must return false when below MaxTotalStreams")
	}
}

func TestHub_AtGlobalLimits_Disabled(t *testing.T) {
	h, _ := testHub(t)

	orig := config.C.Load()
	cfg := *orig
	cfg.MaxTotalStreams = 0 // disabled
	cfg.MaxMemoryMB = 0
	config.C.Store(&cfg)
	defer config.C.Store(orig)

	for i := 0; i < 100; i++ {
		id := "stream-" + string(rune('a'+i))
		_, cleanup := injectStream(h, id, time.Time{})
		defer cleanup()
	}

	h.mu.RLock()
	atLimit := h.atGlobalLimits()
	h.mu.RUnlock()
	if atLimit {
		t.Error("atGlobalLimits must return false when MaxTotalStreams=0 (disabled)")
	}
}

// ── evictOldestIdle ───────────────────────────────────────────────────────────

func TestHub_EvictOldestIdle_RemovesEarliest(t *testing.T) {
	h, _ := testHub(t)

	early := time.Now().Add(-10 * time.Minute)
	late := time.Now().Add(-1 * time.Minute)

	_, cleanup1 := injectStream(h, "s-early", early)
	_, cleanup2 := injectStream(h, "s-late", late)
	defer cleanup1()
	defer cleanup2()

	h.mu.Lock()
	evicted := h.evictOldestIdle()
	h.mu.Unlock()

	if !evicted {
		t.Fatal("evictOldestIdle must return true when idle streams exist")
	}
	h.mu.RLock()
	_, earlyStillExists := h.streams["s-early"]
	_, lateStillExists := h.streams["s-late"]
	h.mu.RUnlock()

	if earlyStillExists {
		t.Error("s-early (oldest idle) must have been evicted")
	}
	if !lateStillExists {
		t.Error("s-late must still be present")
	}
}

func TestHub_EvictOldestIdle_NoIdleStreams_ReturnsFalse(t *testing.T) {
	h, _ := testHub(t)

	// Streams with zero shutdownAt are not idle
	_, cleanup := injectStream(h, "active", time.Time{})
	defer cleanup()

	h.mu.Lock()
	evicted := h.evictOldestIdle()
	h.mu.Unlock()

	if evicted {
		t.Error("evictOldestIdle must return false when no streams are idle")
	}
}

func TestHub_EvictOldestIdle_EmptyHub_ReturnsFalse(t *testing.T) {
	h, _ := testHub(t)

	h.mu.Lock()
	evicted := h.evictOldestIdle()
	h.mu.Unlock()

	if evicted {
		t.Error("evictOldestIdle must return false on an empty hub")
	}
}

// ── ScheduleShutdown / CancelShutdown ────────────────────────────────────────

func TestHub_ScheduleAndCancelShutdown(t *testing.T) {
	h, _ := testHub(t)
	_, cleanup := injectStream(h, "ch1", time.Time{})
	defer cleanup()

	delay := 30 * time.Second
	before := time.Now()
	h.ScheduleShutdown("ch1", delay)

	h.mu.RLock()
	e := h.streams["ch1"]
	scheduled := e.shutdownAt
	h.mu.RUnlock()

	if scheduled.IsZero() {
		t.Fatal("shutdownAt must be non-zero after ScheduleShutdown")
	}
	if scheduled.Before(before.Add(delay - time.Second)) {
		t.Errorf("shutdownAt %v earlier than expected (before+delay-1s)", scheduled)
	}

	h.CancelShutdown("ch1")
	h.mu.RLock()
	e = h.streams["ch1"]
	cancelled := e.shutdownAt
	h.mu.RUnlock()

	if !cancelled.IsZero() {
		t.Error("shutdownAt must be zero after CancelShutdown")
	}
}

func TestHub_ScheduleShutdown_UnknownStream_NoOp(t *testing.T) {
	h, _ := testHub(t)
	// Should not panic
	h.ScheduleShutdown("nonexistent", time.Second)
}

// ── GetEntry ─────────────────────────────────────────────────────────────────

func TestHub_GetEntry_ReturnsNilForUnknown(t *testing.T) {
	h, _ := testHub(t)
	mgr, buf, cm := h.GetEntry("does-not-exist")
	if mgr != nil || buf != nil || cm != nil {
		t.Error("GetEntry must return all-nil for unknown contentID")
	}
}

func TestHub_GetEntry_ReturnsEntryForKnown(t *testing.T) {
	h, _ := testHub(t)
	buf, cleanup := injectStream(h, "known", time.Time{})
	defer cleanup()

	_, gotBuf, _ := h.GetEntry("known")
	if gotBuf != buf {
		t.Error("GetEntry must return the same buffer that was injected")
	}
}

// ── StartStream deduplication ─────────────────────────────────────────────────

func TestHub_StartStream_DeduplicatesExisting(t *testing.T) {
	h, _ := testHub(t)

	// Inject an already-active stream
	_, cleanup := injectStream(h, "dup-stream", time.Time{})
	defer cleanup()

	p := StreamParams{
		ContentID: "dup-stream",
		Engine:    EngineParams{Host: "127.0.0.1", Port: 6878},
	}
	// StartStream should return false (stream already exists), not start a new manager
	started := h.StartStream(context.Background(), p)
	if started {
		t.Error("StartStream must return false when stream is already active")
	}
}
