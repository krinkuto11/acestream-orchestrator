// Package buffer provides an in-memory ring buffer for MPEG-TS stream chunks.
// Each stream gets one RingBuffer; all clients read from it by index.
// No Redis is used for chunk storage — Redis is used only for ownership keys
// and client tracking metadata.
package buffer

import (
	"sync"
	"time"

	"github.com/acestream/proxy/internal/ts"
)

const (
	defaultSlots    = 16   // number of ring slots (must be power of two for mask trick)
	minSafeSlots    = 4
)

// Chunk is a single buffered unit: aligned TS data + the global sequence index.
type Chunk struct {
	Data  []byte
	Index int64
}

// RingBuffer holds the most recent N chunks in a fixed-size circular array.
// Producers call Write; consumers call Read(afterIndex). All methods are
// goroutine-safe.
type RingBuffer struct {
	mu            sync.RWMutex
	slots         [][]byte  // circular storage
	seq           []int64   // global sequence index per slot
	head          int64     // next index to be written (monotonically increasing)
	cap           int       // len(slots), always power of two
	mask          int       // cap-1 for fast mod
	targetSize    int       // bytes per chunk (188-byte aligned)
	partial       []byte    // leftover bytes < 188 between writes
	notify        chan struct{} // closed-and-replaced on every new chunk
	notifyMu      sync.Mutex
	stopped       bool

	// Write timing for freshness checks
	lastWriteTime time.Time

	// Source rate EMA (chunks/second)
	sourceRateEMA      float64
	rateWindowStart    time.Time
	rateWindowDelta    float64
}

// New creates a RingBuffer with the given chunk target size (bytes) and slot count.
func New(targetChunkSize, slots int) *RingBuffer {
	if slots <= 0 {
		slots = defaultSlots
	}
	// Round up to next power of two
	slots = nextPow2(slots)

	// Align targetChunkSize to TS packet size
	targetChunkSize = (targetChunkSize / ts.PacketSize) * ts.PacketSize
	if targetChunkSize == 0 {
		targetChunkSize = ts.PacketSize * 5644
	}

	rb := &RingBuffer{
		slots:      make([][]byte, slots),
		seq:        make([]int64, slots),
		cap:        slots,
		mask:       slots - 1,
		targetSize: targetChunkSize,
		notify:     make(chan struct{}),
	}
	for i := range rb.seq {
		rb.seq[i] = -1
	}
	return rb
}

// Write appends raw bytes, accumulates until targetSize, then stores one or
// more chunks into the ring. Returns number of chunks written.
func (rb *RingBuffer) Write(data []byte) int {
	if len(data) == 0 {
		return 0
	}

	rb.mu.Lock()
	if rb.stopped {
		rb.mu.Unlock()
		return 0
	}
	rb.lastWriteTime = time.Now()

	// Combine with partial remainder
	combined := append(rb.partial, data...)

	// Align to TS packet boundary
	alignedLen := (len(combined) / ts.PacketSize) * ts.PacketSize
	if alignedLen == 0 {
		rb.partial = combined
		rb.mu.Unlock()
		return 0
	}
	rb.partial = combined[alignedLen:]
	aligned := combined[:alignedLen]

	// Write as many target-size chunks as possible
	written := 0
	for len(aligned) >= rb.targetSize {
		chunk := make([]byte, rb.targetSize)
		copy(chunk, aligned[:rb.targetSize])
		aligned = aligned[rb.targetSize:]
		rb.storeChunk(chunk)
		written++
	}

	rb.mu.Unlock()

	if written > 0 {
		rb.updateSourceRate(written)
		rb.broadcast()
	}
	return written
}

// storeChunk must be called with rb.mu held.
func (rb *RingBuffer) storeChunk(data []byte) {
	idx := rb.head
	rb.head++
	slot := int(idx) & rb.mask
	rb.slots[slot] = data
	rb.seq[slot] = idx
}

// Head returns the index of the most recently written chunk (-1 if none yet).
func (rb *RingBuffer) Head() int64 {
	rb.mu.RLock()
	h := rb.head - 1
	rb.mu.RUnlock()
	return h
}

// ReadAfter returns all chunks with index > afterIndex (up to maxChunks).
// Also returns the last index seen (for the caller to advance its cursor).
// Returns nil, -1 if no new chunks are available.
func (rb *RingBuffer) ReadAfter(afterIndex int64, maxChunks int) ([][]byte, int64) {
	if maxChunks <= 0 {
		maxChunks = 10
	}

	rb.mu.RLock()
	defer rb.mu.RUnlock()

	head := rb.head - 1
	if head < 0 || afterIndex >= head {
		return nil, afterIndex
	}

	// How many chunks behind is the client?
	behind := head - afterIndex
	// Don't overflow the ring — if client is too far behind, jump to oldest available
	oldest := rb.head - int64(rb.cap)
	if oldest < 0 {
		oldest = 0
	}
	startIdx := afterIndex + 1
	if startIdx < oldest {
		startIdx = oldest
	}

	// Adaptive fetch count based on lag
	fetch := int64(maxChunks)
	switch {
	case behind > 100:
		fetch = 15
	case behind > 50:
		fetch = 10
	case behind > 20:
		fetch = 5
	default:
		fetch = 3
	}
	if fetch > int64(maxChunks) {
		fetch = int64(maxChunks)
	}

	endIdx := startIdx + fetch
	if endIdx > rb.head {
		endIdx = rb.head
	}

	chunks := make([][]byte, 0, endIdx-startIdx)
	lastIdx := startIdx - 1
	for i := startIdx; i < endIdx; i++ {
		slot := int(i) & rb.mask
		if rb.seq[slot] != i {
			// Slot overwritten or not yet written — stop here (contiguous only)
			break
		}
		chunks = append(chunks, rb.slots[slot])
		lastIdx = i
	}

	if len(chunks) == 0 {
		return nil, afterIndex
	}
	return chunks, lastIdx
}

// Wait blocks until a new chunk is available after afterIndex, or until ctx is done.
func (rb *RingBuffer) Wait(afterIndex int64) <-chan struct{} {
	rb.notifyMu.Lock()
	ch := rb.notify
	rb.notifyMu.Unlock()

	// Already ahead — return a closed channel so caller doesn't block.
	rb.mu.RLock()
	ahead := rb.head-1 > afterIndex
	rb.mu.RUnlock()
	if ahead {
		closed := make(chan struct{})
		close(closed)
		return closed
	}
	return ch
}

// IsFresh returns true if upstream has written data within maxSilence.
func (rb *RingBuffer) IsFresh(maxSilence time.Duration) bool {
	rb.mu.RLock()
	t := rb.lastWriteTime
	rb.mu.RUnlock()
	if t.IsZero() {
		return false
	}
	return time.Since(t) <= maxSilence
}

// TargetChunkSize returns the target byte size of each chunk.
func (rb *RingBuffer) TargetChunkSize() int {
	return rb.targetSize
}

// SourceRate returns the EMA of chunks/second from the upstream.
func (rb *RingBuffer) SourceRate() float64 {
	rb.mu.RLock()
	r := rb.sourceRateEMA
	rb.mu.RUnlock()
	return r
}

// Reset discards all accumulated data (e.g. after engine failover).
func (rb *RingBuffer) Reset() {
	rb.mu.Lock()
	rb.partial = rb.partial[:0]
	for i := range rb.seq {
		rb.seq[i] = -1
		rb.slots[i] = nil
	}
	rb.mu.Unlock()
}

// Stop signals the buffer is done; Write calls are no-ops after this.
func (rb *RingBuffer) Stop() {
	rb.mu.Lock()
	rb.stopped = true
	rb.mu.Unlock()
	rb.broadcast()
}

func (rb *RingBuffer) broadcast() {
	rb.notifyMu.Lock()
	old := rb.notify
	rb.notify = make(chan struct{})
	rb.notifyMu.Unlock()
	close(old)
}

func (rb *RingBuffer) updateSourceRate(written int) {
	rb.mu.Lock()
	now := time.Now()
	if rb.rateWindowStart.IsZero() {
		rb.rateWindowStart = now
		rb.mu.Unlock()
		return
	}
	rb.rateWindowDelta += float64(written)
	elapsed := now.Sub(rb.rateWindowStart).Seconds()
	if elapsed < 1.5 && rb.sourceRateEMA != 0 {
		rb.mu.Unlock()
		return
	}
	instant := rb.rateWindowDelta / max(0.001, elapsed)
	const alpha = 0.15
	if rb.sourceRateEMA == 0 {
		rb.sourceRateEMA = instant
	} else {
		rb.sourceRateEMA = alpha*instant + (1-alpha)*rb.sourceRateEMA
	}
	rb.rateWindowStart = now
	rb.rateWindowDelta = 0
	rb.mu.Unlock()
}

func nextPow2(n int) int {
	n--
	n |= n >> 1
	n |= n >> 2
	n |= n >> 4
	n |= n >> 8
	n |= n >> 16
	n++
	if n < minSafeSlots {
		return minSafeSlots
	}
	return n
}

func max(a, b float64) float64 {
	if a > b {
		return a
	}
	return b
}
