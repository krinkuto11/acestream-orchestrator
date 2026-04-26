package stream

import (
	"context"
	"fmt"
	"io"
	"log/slog"
	"math"
	"time"

	"github.com/acestream/acestream/internal/proxy/buffer"
	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/proxy/telemetry"
	"github.com/acestream/acestream/internal/proxy/ts"
)

const (
	pacingGracePeriod  = 2 * time.Second
	maxPacingSleep     = 500 * time.Millisecond
	starvationLogEvery = 10

	minRunwayChunks = 3

	pcrMinStableBPS = 256 * 1024
)

// ClientStreamer delivers buffered chunks to one HTTP client with bitrate pacing.
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

	bytesSent  int64
	chunksSent int64
	localIndex int64

	pacingStart    time.Time
	videoByteSent  int64
	streamBitrate  int
	pacingBurstSec float64

	chunkRateEMA      float64
	lastChunkRateTime time.Time

	nullCC uint8

	tag string
}

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
		pacingBurstSec:    config.C.Load().PacingBurstSeconds,
		lastChunkRateTime: time.Now(),
		tag:               fmt.Sprintf("[ts:%s][client:%s]", contentID, clientID),
	}
}

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
		pbSec = config.C.Load().ProxyPrebufferSeconds
	}
	if pbSec > 0 {
		if !cs.sendFirstChunk(ctx) {
			return
		}
		cs.applyPrebuffer(ctx, pbSec)
	}

	cs.initPacingBurst()

	cfg := config.C.Load()
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

func (cs *ClientStreamer) sendFirstChunk(ctx context.Context) bool {
	cfg := config.C.Load()
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

func (cs *ClientStreamer) waitForReady(ctx context.Context) bool {
	deadline := time.Now().Add(config.C.Load().ChannelInitGracePeriod)
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

func (cs *ClientStreamer) applyPrebuffer(ctx context.Context, seconds int) {
	bitrate := cs.manager.Bitrate()
	if bitrate <= 0 {
		bitrate = cs.streamBitrate
	}
	if bitrate <= 0 {
		bitrate = 312500
	}

	chunkSize := cs.buf.TargetChunkSize()
	if chunkSize <= 0 {
		chunkSize = config.C.Load().BufferChunkSize
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

func (cs *ClientStreamer) effectiveBPS() int {
	if vbr := cs.buf.VideoBitrate(); vbr >= pcrMinStableBPS {
		return int(vbr)
	}
	return cs.streamBitrate
}

func (cs *ClientStreamer) initPacingBurst() {
	runway := cs.buf.Head() - cs.localIndex
	safeRunway := runway - int64(minRunwayChunks)
	if safeRunway <= 0 {
		return
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

func (cs *ClientStreamer) applyPacing() {
	if cs.pacingStart.IsZero() {
		return
	}
	elapsed := time.Since(cs.pacingStart).Seconds()
	if elapsed < pacingGracePeriod.Seconds() {
		return
	}

	cfg := config.C.Load()
	runway := int(cs.buf.Head() - cs.localIndex)

	mult := cs.manager.PacingMultiplier()
	if mult <= 0 {
		mult = cfg.PacingBitrateMultiplier
	}

	switch {
	case runway > 30:
		mult = cfg.ProxyMaxCatchupMultiplier
	case runway > 15:
		midMult := cfg.ProxyMaxCatchupMultiplier * 0.75
		if mult < midMult {
			mult = midMult
		}
	case runway < minRunwayChunks:
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

func (cs *ClientStreamer) updateChunkRate(n int) {
	if vbr := cs.buf.VideoBitrate(); vbr >= pcrMinStableBPS {
		chunkRate := vbr / float64(cs.buf.TargetChunkSize())
		if cs.chunkRateEMA == 0 {
			cs.chunkRateEMA = chunkRate
		} else {
			cs.chunkRateEMA = 0.2*chunkRate + 0.8*cs.chunkRateEMA
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
