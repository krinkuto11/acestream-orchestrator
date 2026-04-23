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
	"github.com/acestream/proxy/internal/telemetry"
	"github.com/acestream/proxy/internal/ts"
)

const (
	pacingGracePeriod  = 2 * time.Second
	maxPacingSleep     = 500 * time.Millisecond
	starvationLogEvery = 10

	// minRunwayChunks is the number of ring-buffer chunks the proxy tries to
	// keep ahead of any client. Keeping a reserve means short upstream stalls
	// do not immediately stall the player.
	minRunwayChunks = 3

	// pcrWarmupBytes is the minimum number of bytes the ring buffer must have
	// received before we trust its PCR-derived bitrate. A typical stream
	// delivers the first PCR within the first 20–50 TS packets (~4–10 KB), but
	// we wait for a second sample (one full PCR interval, ~100 ms) before using
	// the measurement. 256 KB is a conservative threshold that covers the
	// worst-case first PCR interval.
	pcrWarmupBytes = 256 * 1024
)

// ClientStreamer delivers buffered chunks to one HTTP client with bitrate pacing.
// Call Stream() in a goroutine; write to w until context is done or stream ends.
type ClientStreamer struct {
	contentID string
	clientID  string
	clientIP  string
	userAgent string
	seekback  int
	manager   *Manager
	buf       *buffer.RingBuffer
	cm        *ClientManager
	w         io.Writer
	flusher   interface{ Flush() }

	// stats
	bytesSent  int64
	chunksSent int64
	localIndex int64

	// pacing
	pacingStart    time.Time
	videoByteSent  int64
	streamBitrate  int     // engine-reported bytes/s; fallback before PCR stabilises
	pacingBurstSec float64 // seconds of pre-download allowance, set once at delivery start

	// chunk rate EMA (fallback when PCR bitrate is unavailable)
	chunkRateEMA      float64
	lastChunkRateTime time.Time

	// null packet continuity counter
	nullCC uint8

	tag string
}

// NewClientStreamer creates a streamer for the given client.
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
		pacingBurstSec:    config.C.PacingBurstSeconds, // may be reduced by initPacingBurst
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

	// Compute burst allowance now that we know the runway depth.
	cs.initPacingBurst()

	// ── Main delivery loop ────────────────────────────────────────────────────
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
			telemetry.DefaultTelemetry.ObserveEgress("TS", n)
			cs.cm.UpdateStats(cs.clientID, n, delta)
			cs.localIndex = newIdx
			cs.updateChunkRate(int(delta))
			cs.updateClientPosition()
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
			telemetry.DefaultTelemetry.ObserveEgress("TS", n)
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
// Sends null TS packets every 0.5 s to keep the HTTP connection alive.
func (cs *ClientStreamer) applyPrebuffer(ctx context.Context, seconds int) {
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

// ─── Bitrate ─────────────────────────────────────────────────────────────────

// effectiveBPS returns the bitrate to use for pacing (bytes/s).
//
// Priority:
//  1. PCR-derived video bitrate from the ring buffer — accurate from the first
//     complete PCR interval, immune to AceStream's initial data-transfer burst.
//  2. Engine-reported estimate — used while PCR hasn't stabilised yet.
func (cs *ClientStreamer) effectiveBPS() int {
	if vbr := cs.buf.VideoBitrate(); vbr >= pcrWarmupBytes {
		return int(vbr)
	}
	return cs.streamBitrate
}

// ─── Pacing ──────────────────────────────────────────────────────────────────

// initPacingBurst computes cs.pacingBurstSec once, just before the main
// delivery loop. The config default is reduced when the current ring-buffer
// runway is shallower than PacingBurstSeconds × bitrate, so the burst cannot
// drain the ring below minRunwayChunks of reserve.
func (cs *ClientStreamer) initPacingBurst() {
	runway := cs.buf.Head() - cs.localIndex
	safeRunway := runway - int64(minRunwayChunks)
	if safeRunway <= 0 {
		return // runway already at/below the floor — keep config default
	}
	bps := cs.effectiveBPS()
	if bps <= 0 {
		return
	}
	runwaySec := float64(safeRunway) * float64(cs.buf.TargetChunkSize()) / float64(bps)
	if runwaySec < cs.pacingBurstSec {
		cs.pacingBurstSec = runwaySec
		slog.Debug("pacing burst capped by runway",
			"stream", cs.contentID, "client", cs.clientID,
			"burst_s", math.Round(runwaySec*10)/10,
			"runway_chunks", runway)
	}
}

// applyPacing sleeps to keep delivery at effectiveBPS × pacing_multiplier.
//
// Multiplier tiers (highest to lowest priority):
//   runway > 30 chunks  → ProxyMaxCatchupMultiplier (2×) — ring is very full, drain fast
//   runway > 15 chunks  → 1.5×
//   runway < 3 chunks   → 1.0× — ring almost empty, stop over-consuming
//   otherwise           → PacingBitrateMultiplier (1.2×)
func (cs *ClientStreamer) applyPacing() {
	if cs.pacingStart.IsZero() {
		return
	}
	elapsed := time.Since(cs.pacingStart).Seconds()
	if elapsed < pacingGracePeriod.Seconds() {
		return
	}

	cfg := config.C
	runway := int(cs.buf.Head() - cs.localIndex)

	mult := cs.manager.PacingMultiplier()
	if mult <= 0 {
		mult = cfg.PacingBitrateMultiplier
	}

	switch {
	case runway > 30:
		mult = cfg.ProxyMaxCatchupMultiplier
	case runway > 15:
		if mult < 1.5 {
			mult = 1.5
		}
	case runway < minRunwayChunks:
		// Ring nearly empty — pace at exactly 1× to stop draining it.
		mult = 1.0
	}

	bps := cs.effectiveBPS()

	if bps > 0 {
		effectiveBR := float64(bps) * mult
		burst := effectiveBR * cs.pacingBurstSec
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

	// ── Fallback: chunk-count pacing when bitrate is unknown ─────────────────
	rate := cs.chunkRateEMA
	if rate <= 0 {
		return
	}
	expected := elapsed * rate
	burst := cs.pacingBurstSec * rate
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

// updateClientPosition recomputes the runway in seconds and stores it in the
// ClientRecord so the heartbeat can flush it to Redis. Called on every
// successful write; cheap enough to run inline.
func (cs *ClientStreamer) updateClientPosition() {
	bps := cs.effectiveBPS()
	if bps <= 0 {
		return
	}
	runway := cs.buf.Head() - cs.localIndex
	if runway < 0 {
		runway = 0
	}
	secondsBehind := float64(runway) * float64(cs.buf.TargetChunkSize()) / float64(bps)
	cs.cm.UpdatePosition(cs.clientID, secondsBehind)
}

// updateChunkRate maintains the chunk-rate EMA used as pacing fallback.
// Prefers the PCR-derived bitrate when available to avoid egress-speed bias.
func (cs *ClientStreamer) updateChunkRate(n int) {
	if vbr := cs.buf.VideoBitrate(); vbr >= pcrWarmupBytes {
		chunkRate := vbr / float64(cs.buf.TargetChunkSize())
		if cs.chunkRateEMA == 0 {
			cs.chunkRateEMA = chunkRate
		} else {
			cs.chunkRateEMA = 0.2*chunkRate + 0.8*cs.chunkRateEMA
		}
		return
	}

	// Fallback: measure from actual delivery timing.
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
