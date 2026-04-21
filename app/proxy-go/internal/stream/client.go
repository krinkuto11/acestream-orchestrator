package stream

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"math"
	"time"

	"github.com/acestream/proxy/internal/buffer"
	"github.com/acestream/proxy/internal/config"
	"github.com/acestream/proxy/internal/ts"
)

const (
	pacingGracePeriod   = 2 * time.Second
	maxPacingSleep      = 500 * time.Millisecond
	starvationLogEvery  = 10 // log every N consecutive empty polls
)

// ClientStreamer delivers buffered chunks to one HTTP client with bitrate pacing.
// Call Stream() in a goroutine; write to w until context is done or stream ends.
type ClientStreamer struct {
	contentID   string
	clientID    string
	clientIP    string
	userAgent   string
	seekback    int
	manager     *Manager
	buf         *buffer.RingBuffer
	cm          *ClientManager
	w           io.Writer
	flusher     interface{ Flush() }

	// stats
	bytesSent  int64
	chunksSent int64
	localIndex int64

	// pacing
	pacingStart     time.Time
	videoByteSent   int64
	streamBitrate   int // bytes/s

	// chunk rate EMA
	chunkRateEMA      float64
	lastChunkRateTime time.Time

	// null packet continuity counter
	nullCC uint8

	tag string
}

// NewClientStreamer creates a streamer for the given client.
// flusher may be nil if the writer does not support flushing.
func NewClientStreamer(contentID, clientID, ip, userAgent string, seekback int,
	mgr *Manager, buf *buffer.RingBuffer, cm *ClientManager,
	w io.Writer, flusher interface{ Flush() }) *ClientStreamer {

	return &ClientStreamer{
		contentID:         contentID,
		clientID:          clientID,
		clientIP:          ip,
		userAgent:         userAgent,
		seekback:          seekback,
		manager:           mgr,
		buf:               buf,
		cm:                cm,
		w:                 w,
		flusher:           flusher,
		lastChunkRateTime: time.Now(),
		tag:               fmt.Sprintf("[ts:%s][client:%s]", contentID, clientID),
	}
}

// Stream runs the main delivery loop until ctx is cancelled or the stream starves.
// Pattern: wait for ready → send first real chunk (probe) → hold with null packets
// until enough runway has accumulated → resume normal delivery.
func (cs *ClientStreamer) Stream(ctx context.Context) {
	if !cs.waitForReady(ctx) {
		return
	}

	startIndex := cs.buf.Head()
	if startIndex < 0 {
		startIndex = 0
	}
	cs.localIndex = startIndex

	if !cs.cm.Add(cs.clientID, cs.clientIP, cs.userAgent, cs.localIndex) {
		slog.Warn("client rejected (max capacity)", "stream", cs.contentID, "client", cs.clientID)
		return
	}
	defer cs.cm.Remove(cs.clientID)

	cs.streamBitrate = cs.manager.Bitrate()
	cs.pacingStart = time.Now()

	slog.Info("client stream starting", "stream", cs.contentID, "client", cs.clientID,
		"ip", cs.clientIP, "bitrate_bps", cs.streamBitrate*8)

	// --- Probe: send first real chunk before entering prebuffer hold ---
	pbSec := cs.manager.PrebufferSeconds()
	if pbSec <= 0 {
		pbSec = config.C.ProxyPrebufferSeconds
	}
	if pbSec > 0 {
		if !cs.sendFirstChunk(ctx) {
			return
		}
		cs.applyPrebuffer(ctx, pbSec)
	}

	// --- Main delivery loop ---
	cfg := config.C
	maxEmpty := cfg.NoDataTimeoutChecks
	emptyCount := 0

	for {
		select {
		case <-ctx.Done():
			return
		default:
		}

		n, newIdx, err := cs.buf.WriteAfterTo(cs.localIndex, 15, cs.w)
		if n > 0 {
			emptyCount = 0
			cs.bytesSent += n
			cs.videoByteSent += n
			delta := newIdx - cs.localIndex
			cs.chunksSent += delta
			if cs.flusher != nil {
				cs.flusher.Flush()
			}
			cs.cm.UpdateStats(cs.clientID, n, delta)
			cs.localIndex = newIdx
			cs.updateChunkRate(int(delta))
			cs.applyPacing()
		} else if err != nil {
			slog.Debug("client write error", "stream", cs.contentID, "client", cs.clientID, "err", err)
			return
		} else {
			emptyCount++
			if emptyCount > maxEmpty {
				slog.Info("stream ended (no data timeout)", "stream", cs.contentID,
					"client", cs.clientID, "empty_polls", emptyCount)
				return
			}
			select {
			case <-ctx.Done():
				return
			case <-cs.buf.Wait(cs.localIndex):
			case <-time.After(cfg.NoDataCheckInterval):
			}
		}
	}
}

// sendFirstChunk blocks until one real chunk is available and writes it.
// Returns false if ctx is cancelled before a chunk arrives.
func (cs *ClientStreamer) sendFirstChunk(ctx context.Context) bool {
	cfg := config.C
	for {
		n, newIdx, err := cs.buf.WriteAfterTo(cs.localIndex, 1, cs.w)
		if n > 0 {
			cs.bytesSent += n
			cs.videoByteSent += n
			cs.chunksSent++
			if cs.flusher != nil {
				cs.flusher.Flush()
			}
			cs.localIndex = newIdx
			cs.cm.UpdateStats(cs.clientID, n, 1)
			return true
		} else if err != nil {
			return false
		}
		select {
		case <-ctx.Done():
			return false
		case <-cs.buf.Wait(cs.localIndex):
		case <-time.After(cfg.NoDataCheckInterval):
		}
	}
}

// waitForReady blocks until the manager is connected and first data is in the buffer.
func (cs *ClientStreamer) waitForReady(ctx context.Context) bool {
	deadline := time.Now().Add(config.C.ChannelInitGracePeriod)
	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			return false
		default:
		}
		if cs.manager.Connected() && cs.buf.Head() >= 0 {
			return true
		}
		time.Sleep(200 * time.Millisecond)
	}
	slog.Warn("stream init timeout", "stream", cs.contentID, "client", cs.clientID)
	return false
}

// applyPrebuffer holds the stream after the first real chunk (Probe-Then-Hold).
// It sends null TS packets every 0.5s to keep the HTTP connection alive while
// waiting for enough chunks to accumulate as runway.
//
// Exit conditions (mirrors Python _apply_prebuffer_hold):
//  - runway >= targetChunks AND (engine has advanced past initial index OR elapsed >= 2s)
//  - deadline reached (max(30, seconds*2) seconds)
//  - ctx cancelled
func (cs *ClientStreamer) applyPrebuffer(ctx context.Context, seconds int) {
	// Late-binding bitrate: re-read from manager in case engine response arrived after registration.
	bitrate := cs.manager.Bitrate()
	if bitrate <= 0 {
		bitrate = cs.streamBitrate
	}
	if bitrate <= 0 {
		bitrate = 312500 // 2.5 Mbps floor
	}

	chunkSize := cs.buf.TargetChunkSize()
	if chunkSize <= 0 {
		chunkSize = config.C.BufferChunkSize
	}

	targetChunks := int(math.Ceil(float64(seconds) * float64(bitrate) / float64(chunkSize)))

	timeout := seconds * 2
	if timeout < 30 {
		timeout = 30
	}
	deadline := time.Now().Add(time.Duration(timeout) * time.Second)
	holdStart := time.Now()
	initialEngineIndex := cs.localIndex

	slog.Info("prebuffer hold started", "stream", cs.contentID, "client", cs.clientID,
		"target_chunks", targetChunks, "bitrate_bps", bitrate*8, "timeout_s", timeout)

	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			return
		default:
		}

		head := cs.buf.Head()
		runway := int(head - cs.localIndex)
		elapsed := time.Since(holdStart).Seconds()

		// has_progressed: engine has written at least one new chunk since we started
		hasProgressed := head > initialEngineIndex || elapsed >= 2.0

		if runway >= targetChunks && hasProgressed && cs.buf.IsFresh(15*time.Second) {
			slog.Info("prebuffer complete", "stream", cs.contentID, "client", cs.clientID,
				"runway", runway, "elapsed_s", fmt.Sprintf("%.1f", elapsed))
			return
		}

		nullPkts := ts.CreateNullChunk(50, cs.nullCC)
		cs.nullCC = (cs.nullCC + 50) & 0x0F
		if _, err := cs.w.Write(nullPkts); err != nil {
			return
		}
		if cs.flusher != nil {
			cs.flusher.Flush()
		}
		time.Sleep(500 * time.Millisecond)
	}
	slog.Info("prebuffer timeout reached", "stream", cs.contentID, "client", cs.clientID)
}

// applyPacing sleeps to keep delivery rate at (bitrate × pacing_multiplier).
func (cs *ClientStreamer) applyPacing() {
	if cs.pacingStart.IsZero() {
		return
	}
	elapsed := time.Since(cs.pacingStart).Seconds()
	if elapsed < pacingGracePeriod.Seconds() {
		return
	}

	cfg := config.C
	mult := cfg.PacingBitrateMultiplier

	// Tiered catch-up based on lag
	runway := int(cs.buf.Head() - cs.localIndex)
	switch {
	case runway > 30:
		mult = cfg.ProxyMaxCatchupMultiplier
	case runway > 15:
		if mult < 1.5 {
			mult = 1.5
		}
	}

	// Prefer the measured ingress rate (source rate × chunk size) over the
	// engine-reported estimate, which is set before buffering begins and may
	// not reflect actual P2P throughput. Fall back to engine value when the
	// ring buffer hasn't had enough data to produce a reliable measurement.
	bps := cs.streamBitrate
	if srcRate := cs.buf.SourceRate(); srcRate > 0.1 {
		if measured := int(srcRate * float64(cs.buf.TargetChunkSize())); measured > 0 {
			bps = measured
		}
	}

	if bps > 0 {
		effectiveBR := float64(bps) * mult
		burst := effectiveBR * cfg.PacingBurstSeconds
		expected := elapsed * effectiveBR
		if float64(cs.videoByteSent) > expected+burst {
			wait := (float64(cs.videoByteSent)-burst)/effectiveBR - elapsed
			if wait > 0 {
				if wait > maxPacingSleep.Seconds() {
					wait = maxPacingSleep.Seconds()
				}
				time.Sleep(time.Duration(wait * float64(time.Second)))
			}
		}
		return
	}

	// Fallback: chunk-count pacing
	rate := cs.chunkRateEMA
	if rate <= 0 {
		rate = cs.buf.SourceRate()
	}
	if rate <= 0 {
		return
	}
	expected := elapsed * rate
	burst := float64(config.C.PacingBurstSeconds) * rate
	if float64(cs.chunksSent) > expected+burst {
		wait := (float64(cs.chunksSent)-burst)/rate - elapsed
		if wait > 0 {
			if wait > maxPacingSleep.Seconds() {
				wait = maxPacingSleep.Seconds()
			}
			time.Sleep(time.Duration(wait * float64(time.Second)))
		}
	}
}

func (cs *ClientStreamer) updateChunkRate(n int) {
	// Prefer source rate from the buffer (avoids egress-speed contamination)
	if r := cs.buf.SourceRate(); r > 0.1 {
		if cs.chunkRateEMA == 0 {
			cs.chunkRateEMA = r
		} else {
			cs.chunkRateEMA = 0.2*r + 0.8*cs.chunkRateEMA
		}
		return
	}

	now := time.Now()
	elapsed := now.Sub(cs.lastChunkRateTime).Seconds()
	cs.lastChunkRateTime = now
	if n <= 0 || elapsed <= 0 {
		return
	}
	instant := float64(n) / elapsed
	if cs.chunkRateEMA == 0 {
		cs.chunkRateEMA = instant
	} else {
		cs.chunkRateEMA = 0.2*instant + 0.8*cs.chunkRateEMA
	}
}

