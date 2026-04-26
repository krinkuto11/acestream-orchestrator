// Package buffer provides an in-memory ring buffer for MPEG-TS stream chunks.
// Each stream gets one RingBuffer; all clients read from it by index.
// No Redis is used for chunk storage — Redis is used only for ownership keys
// and client tracking metadata.
package buffer

import (
	"io"
	"net"
	"sync"
	"time"

	"github.com/acestream/acestream/internal/proxy/ts"
)

// ─── Ring constants ──────────────────────────────────────────────────────────

const (
	defaultSlots = 16 // must be power of two
	minSafeSlots = 4
)

// ─── PCR bitrate constants ────────────────────────────────────────────────────

const (
	pcrWindowSize = 8              // circular sample window for bitrate regression
	pcrClockRate  = int64(27_000_000) // 27 MHz PCR clock
	pcrMaxTicks   = int64(1) << 42 // full 42-bit PCR wrap (~4.67 days at 27 MHz)
	pcrHalfTicks  = pcrMaxTicks >> 1

	// Sanity bounds: below 10 KB/s or above 500 MB/s → discard sample.
	pcrMinBPS = float64(10_000)
	pcrMaxBPS = float64(500_000_000)

	// EMA smoothing factor for PCR-derived bitrate.
	// α=0.25 converges in ~3–4 samples while dampening PCR jitter.
	pcrAlpha = 0.25
)

// pcrSample pairs a cumulative byte count with a PCR timestamp.
type pcrSample struct {
	bytes int64 // total bytes written to the ring up to this point
	ticks int64 // PCR value in 27 MHz ticks
}

// ─── alwaysReady ─────────────────────────────────────────────────────────────

// alwaysReady is a pre-closed channel returned by Wait() when the caller is
// already ahead of the ring — avoids a make+close allocation on every poll.
var alwaysReady = func() chan struct{} {
	c := make(chan struct{})
	close(c)
	return c
}()

// ─── Chunk ───────────────────────────────────────────────────────────────────

// Chunk is a single buffered unit: aligned TS data + the global sequence index.
type Chunk struct {
	Data  []byte
	Index int64
}

// ─── RingBuffer ──────────────────────────────────────────────────────────────

// RingBuffer holds the most recent N chunks in a fixed-size circular array.
// Producers call Write; consumers call ReadAfter / WriteAfterTo.
// All methods are goroutine-safe.
type RingBuffer struct {
	mu        sync.RWMutex
	slots     [][]byte  // circular chunk storage
	seq       []int64   // global sequence index per slot
	head      int64     // index of next slot to write (monotonically increasing)
	cap       int       // len(slots), always power of two
	mask      int       // cap-1 for fast modulo
	targetSize int      // bytes per chunk (188-byte aligned)
	partial   []byte    // leftover bytes < 188 between calls
	notify    chan struct{}
	notifyMu  sync.Mutex
	stopped   bool

	// Freshness
	lastWriteTime time.Time

	// ── PCR-based video bitrate ───────────────────────────────────────────────
	// The ring buffer scans every completed chunk for PCR timestamps as it is
	// written. Bitrate is computed from the slope across a sliding window of
	// (cumulativeBytes, pcrTicks) samples — the same principle used by the HLS
	// segmenter to measure segment duration, but applied to the full stream.
	//
	// This is immune to AceStream's initial data-transfer burst: PCR timestamps
	// advance at the video clock rate regardless of how fast bytes arrive.
	pcrSamples    [pcrWindowSize]pcrSample
	pcrHead       int     // next write position in the circular sample buffer
	pcrCount      int     // valid samples (0..pcrWindowSize)
	pcrPID        uint16  // PCR PID we locked onto (0 = not yet detected)
	pcrTotalBytes int64   // cumulative bytes stored (updated in storeChunk)
	pcrBitrateBPS float64 // EMA-smoothed PCR-derived bitrate in bytes/s
}

// ─── Constructor ─────────────────────────────────────────────────────────────

// New creates a RingBuffer with the given chunk target size (bytes) and slot count.
func New(targetChunkSize, slots int) *RingBuffer {
	if slots <= 0 {
		slots = defaultSlots
	}
	slots = nextPow2(slots)

	// Align to TS packet size
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

// ─── Write ───────────────────────────────────────────────────────────────────

// Write appends raw bytes, accumulates until targetSize, then stores one or
// more chunks. Returns the number of chunks written.
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

	combined := append(rb.partial, data...)
	alignedLen := (len(combined) / ts.PacketSize) * ts.PacketSize
	if alignedLen == 0 {
		rb.partial = combined
		rb.mu.Unlock()
		return 0
	}
	subTS := combined[alignedLen:]
	aligned := combined[:alignedLen]

	// Track the last stored chunk for PCR scanning (done outside the lock).
	var lastChunk []byte
	var lastChunkBytes int64

	written := 0
	for len(aligned) >= rb.targetSize {
		chunk := make([]byte, rb.targetSize)
		copy(chunk, aligned[:rb.targetSize])
		aligned = aligned[rb.targetSize:]
		rb.storeChunk(chunk)           // increments rb.pcrTotalBytes
		lastChunk = chunk
		lastChunkBytes = rb.pcrTotalBytes
		written++
	}

	rb.partial = append(aligned, subTS...)
	rb.mu.Unlock()

	if written > 0 {
		// PCR scan runs outside the lock: the stored chunk is immutable.
		if lastChunk != nil {
			rb.updatePCRBitrate(lastChunk, lastChunkBytes)
		}
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
	rb.pcrTotalBytes += int64(len(data))
}

// ─── Read ─────────────────────────────────────────────────────────────────────

// Head returns the index of the most recently written chunk (-1 if none yet).
func (rb *RingBuffer) Head() int64 {
	rb.mu.RLock()
	h := rb.head - 1
	rb.mu.RUnlock()
	return h
}

// ReadAfter returns all chunks with index > afterIndex (up to maxChunks).
// Returns the last index seen (for cursor advancement).
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

	oldest := rb.head - int64(rb.cap)
	if oldest < 0 {
		oldest = 0
	}
	startIdx := afterIndex + 1
	if startIdx < oldest {
		startIdx = oldest
	}

	endIdx := startIdx + int64(maxChunks)
	if endIdx > rb.head {
		endIdx = rb.head
	}

	chunks := make([][]byte, 0, endIdx-startIdx)
	lastIdx := startIdx - 1
	for i := startIdx; i < endIdx; i++ {
		slot := int(i) & rb.mask
		if rb.seq[slot] != i {
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

// WriteAfterTo writes all chunks with index > afterIndex (up to maxChunks)
// directly to w using scatter/gather I/O. Returns bytes written, last index,
// and any write error.
func (rb *RingBuffer) WriteAfterTo(afterIndex int64, maxChunks int, w io.Writer) (int64, int64, error) {
	if maxChunks <= 0 {
		maxChunks = 15
	}

	rb.mu.RLock()

	head := rb.head - 1
	if head < 0 || afterIndex >= head {
		rb.mu.RUnlock()
		return 0, afterIndex, nil
	}

	oldest := rb.head - int64(rb.cap)
	if oldest < 0 {
		oldest = 0
	}
	startIdx := afterIndex + 1
	if startIdx < oldest {
		startIdx = oldest
	}

	endIdx := startIdx + int64(maxChunks)
	if endIdx > rb.head {
		endIdx = rb.head
	}

	var netBuf net.Buffers
	lastIdx := startIdx - 1
	for i := startIdx; i < endIdx; i++ {
		slot := int(i) & rb.mask
		if rb.seq[slot] != i {
			break
		}
		netBuf = append(netBuf, rb.slots[slot])
		lastIdx = i
	}
	rb.mu.RUnlock() // release before blocking I/O — a slow client must not stall writers

	if len(netBuf) == 0 {
		return 0, afterIndex, nil
	}

	n, err := netBuf.WriteTo(w)
	return n, lastIdx, err
}

// ─── Signalling ──────────────────────────────────────────────────────────────

// Wait returns a channel that is closed when a new chunk is written after
// afterIndex. If data is already available it returns the shared alwaysReady
// channel so the caller does not block.
func (rb *RingBuffer) Wait(afterIndex int64) <-chan struct{} {
	rb.notifyMu.Lock()
	ch := rb.notify
	rb.notifyMu.Unlock()

	rb.mu.RLock()
	ahead := rb.head-1 > afterIndex
	rb.mu.RUnlock()
	if ahead {
		return alwaysReady
	}
	return ch
}

// ─── Freshness ───────────────────────────────────────────────────────────────

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

// ─── Metrics ─────────────────────────────────────────────────────────────────

// TargetChunkSize returns the target byte size of each chunk.
func (rb *RingBuffer) TargetChunkSize() int {
	return rb.targetSize
}

// VideoBitrate returns the PCR-derived video bitrate in bytes/s.
//
// This is computed from PCR timestamps embedded in the TS stream, so it
// reflects the encoded video rate regardless of how fast bytes were delivered
// (e.g. an AceStream pre-buffering burst does not inflate this value).
//
// Returns 0 while fewer than 2 PCR samples have been collected (typically
// the first ~200 ms of a stream). Callers should fall back to the
// engine-reported bitrate during this window.
func (rb *RingBuffer) VideoBitrate() float64 {
	rb.mu.RLock()
	bps := rb.pcrBitrateBPS
	rb.mu.RUnlock()
	return bps
}

// ─── Lifecycle ───────────────────────────────────────────────────────────────

// Reset discards all accumulated data (e.g. after engine failover). PCR state
// is cleared so bitrate measurement restarts from a clean slate.
func (rb *RingBuffer) Reset() {
	rb.mu.Lock()
	rb.partial = rb.partial[:0]
	for i := range rb.seq {
		rb.seq[i] = -1
		rb.slots[i] = nil
	}
	rb.lastWriteTime = time.Time{}
	rb.pcrSamples = [pcrWindowSize]pcrSample{}
	rb.pcrHead = 0
	rb.pcrCount = 0
	rb.pcrPID = 0
	rb.pcrTotalBytes = 0
	rb.pcrBitrateBPS = 0
	rb.mu.Unlock()
}

// Stop signals the buffer is done; subsequent Write calls are no-ops.
func (rb *RingBuffer) Stop() {
	rb.mu.Lock()
	rb.stopped = true
	rb.mu.Unlock()
	rb.broadcast()
}

// ─── Internal helpers ────────────────────────────────────────────────────────

func (rb *RingBuffer) broadcast() {
	rb.notifyMu.Lock()
	old := rb.notify
	rb.notify = make(chan struct{})
	rb.notifyMu.Unlock()
	close(old)
}

// updatePCRBitrate scans chunk for PCR timestamps and updates the PCR-based
// bitrate EMA. Called after Write releases rb.mu; chunk is immutable by then.
//
// bytesAtWrite is the value of rb.pcrTotalBytes immediately after the chunk
// was stored — captured under the write lock to avoid races.
func (rb *RingBuffer) updatePCRBitrate(chunk []byte, bytesAtWrite int64) {
	ticks, pid, found := ts.ScanForLastPCR(chunk)
	if !found {
		return
	}

	rb.mu.Lock()
	defer rb.mu.Unlock()

	// Lock onto the first PCR PID encountered; ignore all others.
	// AceStream live streams are SPTS so there is only one PCR PID.
	if rb.pcrPID == 0 {
		rb.pcrPID = pid
	} else if rb.pcrPID != pid {
		return
	}

	// Add sample to the circular window.
	rb.pcrSamples[rb.pcrHead] = pcrSample{bytes: bytesAtWrite, ticks: ticks}
	rb.pcrHead = (rb.pcrHead + 1) % pcrWindowSize
	if rb.pcrCount < pcrWindowSize {
		rb.pcrCount++
	}

	if rb.pcrCount < 2 {
		return // need at least two samples for a slope
	}

	// Compute bitrate from oldest→newest sample pair.
	oldestIdx := (rb.pcrHead - rb.pcrCount + pcrWindowSize) % pcrWindowSize
	oldest := rb.pcrSamples[oldestIdx]

	dBytes := bytesAtWrite - oldest.bytes
	dTicks := ticks - oldest.ticks
	if dTicks < 0 {
		dTicks += pcrMaxTicks // handle 42-bit PCR rollover
	}
	if dBytes <= 0 || dTicks <= 0 || dTicks > pcrHalfTicks {
		// Non-monotonic data or discontinuity — discard this sample.
		rb.pcrCount = 1
		rb.pcrHead = (rb.pcrHead - 1 + pcrWindowSize) % pcrWindowSize
		rb.pcrSamples[rb.pcrHead] = pcrSample{bytes: bytesAtWrite, ticks: ticks}
		rb.pcrHead = (rb.pcrHead + 1) % pcrWindowSize
		return
	}

	rawBPS := float64(dBytes) / (float64(dTicks) / float64(pcrClockRate))
	if rawBPS < pcrMinBPS || rawBPS > pcrMaxBPS {
		return // outside sane range — skip without resetting the window
	}

	if rb.pcrBitrateBPS == 0 {
		rb.pcrBitrateBPS = rawBPS
	} else {
		rb.pcrBitrateBPS = pcrAlpha*rawBPS + (1-pcrAlpha)*rb.pcrBitrateBPS
	}
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
