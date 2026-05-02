package ts

import (
	"bytes"
	"log/slog"
	"time"
)

const (
	PacketSize    = 188
	SyncByte      = 0x47
	SyncByteIndex = 0
	CCIndex       = 3 // Continuity counter location: byte 3
	CCMask        = 0x0F
	PUSIBit       = 0x40 // Payload unit start indicator (byte 1)

	maxInvalidSync     = 5
	defaultConfirms    = 3
	huntBackoffMs      = 10 // Backoff between hunt mode scans
	ccValidationWindow = 10 // Only validate CC after N consecutive locked packets
)

// LockState represents the SyncHunter's synchronization state.
type LockState int

const (
	StateHunting LockState = iota
	StateTentativeLock
	StateLocked
)

// CreateNullPacket returns a 188-byte MPEG-TS null packet with the given continuity counter.
func CreateNullPacket(cc uint8) []byte {
	pkt := make([]byte, PacketSize)
	pkt[0] = SyncByte
	pkt[1] = 0x1F // null PID high
	pkt[2] = 0xFF // null PID low
	pkt[3] = 0x10 | (cc & 0x0F)
	return pkt
}

// CreateNullChunk returns n×188 null packets concatenated.
func CreateNullChunk(n int, startCC uint8) []byte {
	out := make([]byte, 0, n*PacketSize)
	for i := 0; i < n; i++ {
		out = append(out, CreateNullPacket((startCC+uint8(i))&0x0F)...)
	}
	return out
}

// SyncHunter finds 188-byte TS packet boundaries from a raw byte stream
// with robust state management and continuity counter validation.
//
// State machine:
//
//	Hunting -> TentativeLock (3 valid packets + correct CC)
//	TentativeLock -> Locked (10 consecutive valid packets + CC)
//	Locked -> TentativeLock (3 bad packets: sync or CC error)
//	TentativeLock -> Hunting (5 bad packets total)
//
// This reduces false lock-ups and adds hysteresis to prevent thrashing.
type SyncHunter struct {
	buf                   []byte
	slideWindow           []byte // Last few packets for context on loss
	state                 LockState
	invalidSyncCount      int
	invalidCCCount        int
	goodPacketsSinceSync  int
	requiredConfirmations int
	alignToFrame          bool
	lastCC                uint8
	lastCCCheckTime       time.Time
	huntBackoffTime       time.Time
	consecutiveValidCC    int
}

// NewSyncHunter creates a SyncHunter ready for use.
func NewSyncHunter() *SyncHunter {
	return &SyncHunter{
		requiredConfirmations: defaultConfirms,
		alignToFrame:          true,
		state:                 StateHunting,
		slideWindow:           make([]byte, 0, PacketSize*3),
		lastCC:                0xFF, // Invalid initial value
	}
}

// Reset clears the internal state so a new upstream connection starts cleanly.
func (h *SyncHunter) Reset() {
	h.buf = h.buf[:0]
	h.slideWindow = h.slideWindow[:0]
	h.state = StateHunting
	h.invalidSyncCount = 0
	h.invalidCCCount = 0
	h.goodPacketsSinceSync = 0
	h.lastCC = 0xFF
	h.lastCCCheckTime = time.Time{}
	h.huntBackoffTime = time.Time{}
	h.consecutiveValidCC = 0
}

// Feed accepts raw bytes and returns perfectly 188-byte-aligned TS packets.
// Returns nil until the hunter has locked onto a boundary.
// Implements multi-state locking with CC validation and hysteresis.
func (h *SyncHunter) Feed(data []byte) []byte {
	if len(data) == 0 {
		return nil
	}
	h.buf = append(h.buf, data...)

	switch h.state {
	case StateHunting:
		return h.huntPath()
	case StateTentativeLock:
		return h.tentativePath()
	case StateLocked:
		return h.lockedPath()
	}
	return nil
}

// lockedPath processes packets when fully synchronized.
// On sync or CC violation, downgrades to tentative lock.
func (h *SyncHunter) lockedPath() []byte {
	validLen := (len(h.buf) / PacketSize) * PacketSize
	if validLen == 0 {
		return nil
	}

	out := make([]byte, 0, validLen)
	for i := 0; i < validLen; i += PacketSize {
		pkt := h.buf[i : i+PacketSize]

		// Check sync byte
		if pkt[SyncByteIndex] != SyncByte {
			h.invalidSyncCount++
			if h.invalidSyncCount >= 3 {
				slog.Warn("ts sync lost (Locked->Tentative)",
					"invalid_sync", h.invalidSyncCount,
					"position", len(out))
				h.state = StateTentativeLock
				h.invalidSyncCount = 0
				h.invalidCCCount = 0
				h.goodPacketsSinceSync = 0
				h.updateSlideWindow(h.buf[:i])
				h.buf = h.buf[i:]
				return out
			}
			continue
		}

		// Validate continuity counter after warm-up
		if h.goodPacketsSinceSync >= ccValidationWindow && !h.validateCC(pkt) {
			h.invalidCCCount++
			if h.invalidCCCount >= 2 {
				slog.Warn("ts sync lost - CC violation (Locked->Tentative)",
					"invalid_cc", h.invalidCCCount,
					"expected_cc", (h.lastCC+1)&CCMask,
					"got_cc", pkt[CCIndex]&CCMask)
				h.state = StateTentativeLock
				h.invalidSyncCount = 0
				h.invalidCCCount = 0
				h.goodPacketsSinceSync = 0
				h.updateSlideWindow(h.buf[:i])
				h.buf = h.buf[i:]
				return out
			}
			continue
		}

		// Valid packet
		out = append(out, pkt...)
		h.lastCC = pkt[CCIndex] & CCMask
		h.invalidSyncCount = 0
		h.invalidCCCount = 0
		h.goodPacketsSinceSync++
	}
	h.buf = h.buf[validLen:]
	return out
}

// tentativePath processes packets after initial lock but before full confidence.
// Promotes to Locked after 10 good packets, reverts to Hunting after 5 bad packets.
func (h *SyncHunter) tentativePath() []byte {
	validLen := (len(h.buf) / PacketSize) * PacketSize
	if validLen == 0 {
		return nil
	}

	out := make([]byte, 0, validLen)
	for i := 0; i < validLen; i += PacketSize {
		pkt := h.buf[i : i+PacketSize]

		// Check sync byte
		if pkt[SyncByteIndex] != SyncByte {
			h.invalidSyncCount++
			if h.invalidSyncCount >= 5 {
				slog.Warn("ts sync lost, re-entering hunt mode (Tentative->Hunting)",
					"invalid_count", h.invalidSyncCount,
					"good_packets", h.goodPacketsSinceSync)
				h.state = StateHunting
				h.invalidSyncCount = 0
				h.invalidCCCount = 0
				h.goodPacketsSinceSync = 0
				h.consecutiveValidCC = 0
				h.huntBackoffTime = time.Now().Add(time.Duration(huntBackoffMs) * time.Millisecond)
				h.updateSlideWindow(h.buf[:i])
				h.buf = h.buf[i:]
				return out
			}
			continue
		}

		// Validate CC
		if !h.validateCC(pkt) {
			h.invalidCCCount++
			if h.invalidCCCount >= 3 {
				slog.Warn("ts sync lost - repeated CC errors (Tentative->Hunting)",
					"cc_errors", h.invalidCCCount)
				h.state = StateHunting
				h.invalidSyncCount = 0
				h.invalidCCCount = 0
				h.goodPacketsSinceSync = 0
				h.consecutiveValidCC = 0
				h.huntBackoffTime = time.Now().Add(time.Duration(huntBackoffMs) * time.Millisecond)
				h.updateSlideWindow(h.buf[:i])
				h.buf = h.buf[i:]
				return out
			}
			continue
		}

		// Valid packet
		out = append(out, pkt...)
		h.lastCC = pkt[CCIndex] & CCMask
		h.invalidSyncCount = 0
		h.invalidCCCount = 0
		h.goodPacketsSinceSync++
		h.consecutiveValidCC++

		// Promote to full lock after 10 consecutive valid packets
		if h.consecutiveValidCC >= 10 {
			slog.Info("ts sync fully locked (Tentative->Locked)",
				"after_packets", h.goodPacketsSinceSync)
			h.state = StateLocked
		}
	}
	h.buf = h.buf[validLen:]
	return out
}

// huntPath searches for sync byte alignment with hysteresis and rate limiting.
// On finding valid sync chain, enters TentativeLock state.
func (h *SyncHunter) huntPath() []byte {
	// Rate limit hunt mode to prevent thrashing
	if time.Now().Before(h.huntBackoffTime) {
		maxWindow := PacketSize * 3
		if len(h.buf) > maxWindow {
			h.buf = h.buf[len(h.buf)-maxWindow:]
		}
		return nil
	}

	need := PacketSize * h.requiredConfirmations
	for len(h.buf) >= need {
		idx := bytes.IndexByte(h.buf, SyncByte)
		if idx < 0 {
			maxWindow := PacketSize * 3
			if len(h.buf) > maxWindow {
				h.buf = h.buf[len(h.buf)-maxWindow:]
			}
			h.buf = h.buf[len(h.buf)-1:]
			return nil
		}
		if idx > 0 {
			h.buf = h.buf[idx:]
			if len(h.buf) < need {
				break
			}
		}

		// Optional: check PUSI bit (frame boundary)
		if h.alignToFrame && (h.buf[1]&PUSIBit == 0) {
			h.buf = h.buf[1:]
			continue
		}

		// Verify sync byte chain
		verified := true
		for i := 1; i < h.requiredConfirmations; i++ {
			if h.buf[i*PacketSize] != SyncByte {
				verified = false
				break
			}
		}

		if verified {
			// Check CC validity for first packet
			firstCC := h.buf[CCIndex] & CCMask
			h.lastCC = firstCC
			h.state = StateTentativeLock
			h.invalidSyncCount = 0
			h.invalidCCCount = 0
			h.goodPacketsSinceSync = h.requiredConfirmations
			h.consecutiveValidCC = h.requiredConfirmations

			slog.Info("ts sync hunter locked (Hunting->Tentative)",
				"confirmations", h.requiredConfirmations,
				"first_cc", firstCC)
			return h.tentativePath()
		}

		// False sync — skip one byte and continue
		h.buf = h.buf[1:]
	}
	h.updateSlideWindow(h.buf)
	return nil
}

// validateCC checks if the packet's continuity counter is valid.
// Returns true if CC follows expected sequence or on first packet.
func (h *SyncHunter) validateCC(pkt []byte) bool {
	if len(pkt) < CCIndex+1 {
		return false
	}

	cc := pkt[CCIndex] & CCMask

	// First packet after sync lock
	if h.lastCC == 0xFF {
		return true
	}

	// Check for expected next CC (with wrap-around)
	expectedCC := (h.lastCC + 1) & CCMask
	return cc == expectedCC
}

// updateSlideWindow keeps a rolling window of recent packets for diagnostics.
func (h *SyncHunter) updateSlideWindow(data []byte) {
	// Keep only the last 3 packets (564 bytes)
	maxWindow := PacketSize * 3
	if len(data) > maxWindow {
		data = data[len(data)-maxWindow:]
	}
	h.slideWindow = append(h.slideWindow[:0], data...)
}
