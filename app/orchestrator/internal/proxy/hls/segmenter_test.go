package hls

import (
	"strings"
	"testing"
)

// newTestSegmenter builds a Segmenter with a nil buffer — safe for tests that
// call pushSegment directly without touching the background run() goroutine.
// We cancel the context immediately so the goroutine exits, then reset segs.
func newTestSegmenter(windowSize int) *Segmenter {
	s := &Segmenter{
		contentID:  "test-stream",
		buf:        nil,
		targetDur:  defaultTargetDurSec,
		windowSize: windowSize,
	}
	// No background goroutine started; safe to call pushSegment directly.
	return s
}

// makeTS returns a minimal valid MPEG-TS payload of n packets (188 bytes each).
func makeTS(packets int) []byte {
	data := make([]byte, packets*188)
	for i := 0; i < packets; i++ {
		data[i*188] = 0x47 // sync byte
	}
	return data
}

// ── Manifest with no segments ─────────────────────────────────────────────────

func TestSegmenter_ManifestEmpty_ReturnsValidPlaylist(t *testing.T) {
	s := newTestSegmenter(6)
	m := s.Manifest("http://localhost:8000", "stream1")
	if !strings.HasPrefix(m, "#EXTM3U") {
		t.Errorf("empty manifest must start with #EXTM3U, got:\n%s", m)
	}
	if !strings.Contains(m, "#EXT-X-MEDIA-SEQUENCE:0") {
		t.Errorf("empty manifest must contain media sequence 0, got:\n%s", m)
	}
}

// ── pushSegment and Segment lookup ───────────────────────────────────────────

func TestSegmenter_PushAndRetrieve(t *testing.T) {
	s := newTestSegmenter(6)
	data := makeTS(10) // 10 TS packets
	s.pushSegment(0, data, 2.0)

	got, ok := s.Segment(0)
	if !ok {
		t.Fatal("Segment(0) not found after pushSegment")
	}
	if len(got) != len(data) {
		t.Errorf("segment length = %d, want %d", len(got), len(data))
	}
}

func TestSegmenter_MissingSeqReturnsFalse(t *testing.T) {
	s := newTestSegmenter(6)
	s.pushSegment(0, makeTS(5), 2.0)
	_, ok := s.Segment(99)
	if ok {
		t.Error("Segment(99) must return false for non-existent seq")
	}
}

// ── Sliding window eviction ───────────────────────────────────────────────────

func TestSegmenter_WindowEviction(t *testing.T) {
	s := newTestSegmenter(3) // window = 3
	for i := 0; i < 5; i++ {
		s.pushSegment(i, makeTS(4), 2.0)
	}
	// Only seqs 2, 3, 4 should remain
	for _, seq := range []int{0, 1} {
		if _, ok := s.Segment(seq); ok {
			t.Errorf("seq %d must have been evicted (window=3)", seq)
		}
	}
	for _, seq := range []int{2, 3, 4} {
		if _, ok := s.Segment(seq); !ok {
			t.Errorf("seq %d must still be in window", seq)
		}
	}
}

func TestSegmenter_SegmentCount(t *testing.T) {
	s := newTestSegmenter(6)
	if s.SegmentCount() != 0 {
		t.Error("new segmenter must have 0 segments")
	}
	s.pushSegment(0, makeTS(4), 2.0)
	s.pushSegment(1, makeTS(4), 2.0)
	if s.SegmentCount() != 2 {
		t.Errorf("SegmentCount = %d, want 2", s.SegmentCount())
	}
}

// ── MemoryUsage ───────────────────────────────────────────────────────────────

func TestSegmenter_MemoryUsage(t *testing.T) {
	s := newTestSegmenter(6)
	if s.MemoryUsage() != 0 {
		t.Error("empty segmenter must report 0 memory usage")
	}

	data := makeTS(10) // 10 × 188 = 1880 bytes
	s.pushSegment(0, data, 2.0)
	got := s.MemoryUsage()
	if got != int64(len(data)) {
		t.Errorf("MemoryUsage = %d, want %d", got, len(data))
	}

	s.pushSegment(1, data, 2.0)
	got = s.MemoryUsage()
	if got != int64(len(data)*2) {
		t.Errorf("MemoryUsage after 2 segments = %d, want %d", got, len(data)*2)
	}
}

// ── Manifest content ─────────────────────────────────────────────────────────

func TestSegmenter_ManifestContainsSegments(t *testing.T) {
	s := newTestSegmenter(6)
	s.pushSegment(7, makeTS(4), 1.5)
	s.pushSegment(8, makeTS(4), 2.3)

	m := s.Manifest("http://proxy:8000", "ch1")
	if !strings.Contains(m, "#EXT-X-MEDIA-SEQUENCE:7") {
		t.Errorf("manifest must contain MEDIA-SEQUENCE:7, got:\n%s", m)
	}
	if !strings.Contains(m, "seq=7") {
		t.Errorf("manifest must contain seq=7 URL, got:\n%s", m)
	}
	if !strings.Contains(m, "seq=8") {
		t.Errorf("manifest must contain seq=8 URL, got:\n%s", m)
	}
	if !strings.Contains(m, "#EXTINF:1.500") {
		t.Errorf("manifest must contain EXTINF:1.500, got:\n%s", m)
	}
	if !strings.Contains(m, "stream=ch1") {
		t.Errorf("manifest must encode stream key, got:\n%s", m)
	}
}

func TestSegmenter_ManifestTargetDuration_CeilsMax(t *testing.T) {
	s := newTestSegmenter(6)
	s.pushSegment(0, makeTS(4), 3.7) // max dur = 3.7 → TARGETDURATION = ceil(3.7)+1 = 5
	m := s.Manifest("http://localhost", "s")
	if !strings.Contains(m, "#EXT-X-TARGETDURATION:5") {
		t.Errorf("TARGETDURATION must be ceil(3.7)+1=5, got:\n%s", m)
	}
}

// ── Discontinuity flag ────────────────────────────────────────────────────────

func TestSegmenter_DiscontinuityPropagated(t *testing.T) {
	s := newTestSegmenter(6)
	s.mu.Lock()
	s.pendingDiscontinuity = true
	s.mu.Unlock()

	s.pushSegment(0, makeTS(4), 2.0)

	m := s.Manifest("http://localhost", "s")
	if !strings.Contains(m, "#EXT-X-DISCONTINUITY") {
		t.Errorf("manifest must contain EXT-X-DISCONTINUITY after buffer reset, got:\n%s", m)
	}
}

func TestSegmenter_DiscontinuityConsumedOnce(t *testing.T) {
	s := newTestSegmenter(6)
	s.mu.Lock()
	s.pendingDiscontinuity = true
	s.mu.Unlock()

	s.pushSegment(0, makeTS(4), 2.0)
	s.pushSegment(1, makeTS(4), 2.0) // second push should NOT get discontinuity

	seg1, _ := s.Segment(1)
	// Find segment 1 in the segs slice and verify its discontinuity flag
	s.mu.RLock()
	var disc1 bool
	for _, seg := range s.segs {
		if seg.seq == 1 {
			disc1 = seg.discontinuity
		}
	}
	s.mu.RUnlock()
	_ = seg1
	if disc1 {
		t.Error("second segment after discontinuity must not carry discontinuity flag")
	}
}
