package hls

import (
	"bytes"
	"context"
	"fmt"
	"log/slog"
	"math"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/acestream/acestream/internal/proxy/buffer"
	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/proxy/ts"
)

const (
	defaultTargetDurSec = 2.0 // target HLS segment duration in seconds
	flushTimeoutSec     = 5.0 // force-flush partial segment after this silence
	defaultWindowSize   = 6   // sliding window: keep N segments in memory
	pcrRollover         = float64(uint64(1)<<33) / 90000.0 // ~95443 s
)

// Segmenter slices a live MPEG-TS ring buffer into HLS segments, using PCR
// timestamps for accurate duration measurement. No FFmpeg required.
//
// One Segmenter per stream; created lazily on the first HLS manifest request
// in API mode. Segment URLs use the ?seq= query parameter.
type Segmenter struct {
	contentID  string
	buf        *buffer.RingBuffer
	targetDur  float64 // seconds per segment
	windowSize int

	mu   sync.RWMutex
	segs []*hlsSegment

	ctx    context.Context
	cancel context.CancelFunc
}

type hlsSegment struct {
	seq      int
	data     []byte
	duration float64 // actual PCR-measured duration (or estimate)
}

// NewSegmenter creates a Segmenter and starts the background slicing goroutine.
// buf must already be filling with TS data.
func NewSegmenter(contentID string, buf *buffer.RingBuffer) *Segmenter {
	windowSize := config.C.Load().HLSWindowSize
	if windowSize <= 0 {
		windowSize = defaultWindowSize
	}
	ctx, cancel := context.WithCancel(context.Background())
	s := &Segmenter{
		contentID:  contentID,
		buf:        buf,
		targetDur:  defaultTargetDurSec,
		windowSize: windowSize,
		ctx:        ctx,
		cancel:     cancel,
	}
	go s.run()
	slog.Info("hls segmenter started", "stream", contentID, "window_size", windowSize)
	return s
}

// Stop halts the background goroutine.
func (s *Segmenter) Stop() {
	s.cancel()
}

// Manifest returns the current HLS live playlist.
// proxyBase: e.g. "http://host:8000"; streamKey: used in segment URLs.
func (s *Segmenter) Manifest(proxyBase, streamKey string) string {
	s.mu.RLock()
	segs := make([]*hlsSegment, len(s.segs))
	copy(segs, s.segs)
	s.mu.RUnlock()

	if len(segs) == 0 {
		// No segments yet — return a minimal valid playlist; player will retry.
		return "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:4\n#EXT-X-MEDIA-SEQUENCE:0\n"
	}

	maxDur := 0.0
	for _, seg := range segs {
		if seg.duration > maxDur {
			maxDur = seg.duration
		}
	}

	base := strings.TrimRight(proxyBase, "/")
	var sb strings.Builder
	sb.WriteString("#EXTM3U\n")
	sb.WriteString("#EXT-X-VERSION:3\n")
	fmt.Fprintf(&sb, "#EXT-X-TARGETDURATION:%d\n", int(math.Ceil(maxDur))+1)
	fmt.Fprintf(&sb, "#EXT-X-MEDIA-SEQUENCE:%d\n", segs[0].seq)
	for _, seg := range segs {
		fmt.Fprintf(&sb, "#EXTINF:%.3f,\n", seg.duration)
		fmt.Fprintf(&sb, "%s/ace/hls/segment.ts?stream=%s&seq=%d\n", base, url.QueryEscape(streamKey), seg.seq)
	}
	return sb.String()
}

// SegmentCount returns the number of segments currently in the sliding window.
func (s *Segmenter) SegmentCount() int {
	s.mu.RLock()
	n := len(s.segs)
	s.mu.RUnlock()
	return n
}

// MemoryUsage returns the total memory in bytes consumed by the segment window.
func (s *Segmenter) MemoryUsage() int64 {
	s.mu.RLock()
	var total int64
	for _, seg := range s.segs {
		total += int64(len(seg.data))
	}
	s.mu.RUnlock()
	return total
}

// Segment returns the raw TS bytes for the given sequence number.
func (s *Segmenter) Segment(seq int) ([]byte, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, seg := range s.segs {
		if seg.seq == seq {
			return seg.data, true
		}
	}
	return nil, false
}

// run is the background goroutine: reads chunks from the ring buffer, iterates
// over individual 188-byte packets, uses PCR timestamps to decide when to cut.
func (s *Segmenter) run() {
	// Start at the current live edge — don't replay stale buffer history.
	cursor := s.buf.Head()
	if cursor < 0 {
		cursor = -1
	}

	var (
		acc      []byte    // TS packets accumulated for the current segment
		segStart float64 = -1 // PCR of first packet in current segment (-1 = not set)
		localSeq int
	)

	flushTimer := time.NewTimer(time.Duration(flushTimeoutSec * float64(time.Second)))
	defer flushTimer.Stop()

	resetFlush := func() {
		if !flushTimer.Stop() {
			select {
			case <-flushTimer.C:
			default:
			}
		}
		flushTimer.Reset(time.Duration(flushTimeoutSec * float64(time.Second)))
	}

	for {
		chunks, newCursor := s.buf.ReadAfter(cursor, 5)

		if len(chunks) > 0 {
			cursor = newCursor
			resetFlush()

			for _, chunk := range chunks {
				// Search for the first sync byte in the chunk if we're not aligned.
				for off := 0; off+ts.PacketSize <= len(chunk); {
					if chunk[off] != ts.SyncByte {
						next := bytes.IndexByte(chunk[off+1:], ts.SyncByte)
						if next < 0 {
							break
						}
						off += next + 1
						continue
					}
					pkt := chunk[off : off+ts.PacketSize]
					acc = append(acc, pkt...)
					off += ts.PacketSize

					res := ts.FindPCR(pkt)
					if res.Discontinuity {
						slog.Info("TS discontinuity detected", "stream", s.contentID)
						// The current packet was already appended to acc. Remove it so
						// it becomes the first packet of the new segment, not the last
						// packet of the closing one — otherwise segment boundary timing
						// is off by one packet and PCR duration measurements are wrong.
						acc = acc[:len(acc)-ts.PacketSize]
						if len(acc) >= ts.PacketSize {
							dur := s.targetDur
							if segStart >= 0 && res.HasPCR {
								dur = res.Value - segStart
							}
							s.pushSegment(localSeq, acc, dur)
							localSeq++
						}
						// New segment begins with the discontinuity packet.
						acc = append(acc[:0], pkt...)
						segStart = -1
						if res.HasPCR {
							segStart = res.Value
						}
						continue
					}

					if !res.HasPCR {
						continue
					}

					pcr := res.Value
					if segStart < 0 {
						segStart = pcr
						continue
					}

					// Handle PCR rollover (~26.5 hour wrap) or large backwards jumps
					elapsed := pcr - segStart
					if elapsed < -pcrRollover/2 {
						// Likely rollover
						elapsed += pcrRollover
					} else if elapsed < 0 || elapsed > 60.0 {
						// Large jump (backwards or forwards) - force cut
						slog.Info("large PCR jump detected, forcing cut", "stream", s.contentID, "jump", elapsed)
						s.pushSegment(localSeq, acc, s.targetDur)
						localSeq++
						acc = nil
						segStart = pcr
						continue
					}

					// Force cut if we've gone way over target duration without finding a keyframe
					maxDur := s.targetDur * 2.5
					
					if elapsed >= s.targetDur {
						isKeyframe := res.RandomAccess || ts.IsKeyframe(pkt)
						if isKeyframe || elapsed >= maxDur {
							if elapsed >= maxDur && !isKeyframe {
								slog.Debug("forcing segment cut on non-keyframe (timeout)", "stream", s.contentID, "elapsed", elapsed)
							}
							s.pushSegment(localSeq, acc, elapsed)
							localSeq++
							acc = nil
							segStart = pcr // next segment starts at this PCR
						}
					}
				}
			}

		} else {
			// No new data — wait for ring buffer or force-flush on silence.
			select {
			case <-s.ctx.Done():
				return
			case <-s.buf.Wait(cursor):
				continue
			case <-flushTimer.C:
				// Flush whatever we have aligned to 188-byte boundary.
				if len(acc) >= ts.PacketSize {
					aligned := (len(acc) / ts.PacketSize) * ts.PacketSize
					dur := s.targetDur // best estimate when PCR unavailable
					if segStart >= 0 {
						// Use wall-clock as fallback duration estimate
						dur = flushTimeoutSec
					}
					s.pushSegment(localSeq, acc[:aligned], dur)
					localSeq++
					acc = acc[aligned:]
					segStart = -1
				}
				flushTimer.Reset(time.Duration(flushTimeoutSec * float64(time.Second)))
				continue
			}
		}
	}
}

func (s *Segmenter) pushSegment(seq int, data []byte, dur float64) {
	// Ensure TS-aligned copy
	aligned := (len(data) / ts.PacketSize) * ts.PacketSize
	if aligned == 0 {
		return
	}
	seg := make([]byte, aligned)
	copy(seg, data[:aligned])

	s.mu.Lock()
	s.segs = append(s.segs, &hlsSegment{seq: seq, data: seg, duration: dur})
	if len(s.segs) > s.windowSize {
		s.segs = s.segs[len(s.segs)-s.windowSize:]
	}
	s.mu.Unlock()

	slog.Debug("hls segment ready", "stream", s.contentID, "seq", seq,
		"bytes", aligned, "dur_s", fmt.Sprintf("%.3f", dur))
}
