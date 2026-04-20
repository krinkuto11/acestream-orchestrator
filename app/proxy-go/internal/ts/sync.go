package ts

import "log/slog"

const (
	PacketSize      = 188
	SyncByte        = 0x47
	maxInvalidSync  = 5
	defaultConfirms = 3
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

// SyncHunter finds 188-byte TS packet boundaries from a raw byte stream.
// It must see `requiredConfirmations` consecutive sync bytes before locking.
// Once locked, it verifies each outgoing packet and drops back to hunting if
// more than maxInvalidSync consecutive sync bytes are wrong.
type SyncHunter struct {
	buf                  []byte
	locked               bool
	invalidSyncCount     int
	requiredConfirmations int
	alignToFrame         bool
}

// NewSyncHunter creates a SyncHunter ready for use.
func NewSyncHunter() *SyncHunter {
	return &SyncHunter{
		requiredConfirmations: defaultConfirms,
		alignToFrame:          true,
	}
}

// Feed accepts raw bytes and returns perfectly 188-byte-aligned TS packets.
// Returns nil until the hunter has locked onto a boundary.
func (h *SyncHunter) Feed(data []byte) []byte {
	if len(data) == 0 {
		return nil
	}
	h.buf = append(h.buf, data...)

	if h.locked {
		return h.lockedPath()
	}
	return h.huntPath()
}

func (h *SyncHunter) lockedPath() []byte {
	validLen := (len(h.buf) / PacketSize) * PacketSize
	if validLen == 0 {
		return nil
	}

	out := make([]byte, 0, validLen)
	for i := 0; i < validLen; i += PacketSize {
		if h.buf[i] == SyncByte {
			out = append(out, h.buf[i:i+PacketSize]...)
			h.invalidSyncCount = 0
		} else {
			h.invalidSyncCount++
			if h.invalidSyncCount >= maxInvalidSync {
				slog.Warn("ts sync lost, re-entering hunt mode", "invalid_count", h.invalidSyncCount)
				h.locked = false
				h.buf = h.buf[i:]
				return out
			}
		}
	}
	h.buf = h.buf[validLen:]
	return out
}

func (h *SyncHunter) huntPath() []byte {
	need := PacketSize * h.requiredConfirmations
	for len(h.buf) >= need {
		idx := indexByte(h.buf, SyncByte)
		if idx < 0 {
			// No sync byte at all — keep last byte in case it starts a packet
			h.buf = h.buf[len(h.buf)-1:]
			return nil
		}
		if idx > 0 {
			h.buf = h.buf[idx:]
			if len(h.buf) < need {
				break
			}
		}

		// Optional: wait for PUSI (payload unit start indicator) bit
		if h.alignToFrame && (h.buf[1]&0x40 == 0) {
			h.buf = h.buf[1:]
			continue
		}

		// Verify the chain of sync bytes
		verified := true
		for i := 1; i < h.requiredConfirmations; i++ {
			if h.buf[i*PacketSize] != SyncByte {
				verified = false
				break
			}
		}
		if verified {
			h.locked = true
			h.invalidSyncCount = 0
			slog.Info("ts sync hunter locked", "confirmations", h.requiredConfirmations)
			return h.lockedPath()
		}
		// False sync — skip one byte and continue
		h.buf = h.buf[1:]
	}
	return nil
}

func (h *SyncHunter) Reset() {
	h.locked = false
	h.buf = h.buf[:0]
}

func indexByte(b []byte, c byte) int {
	for i, v := range b {
		if v == c {
			return i
		}
	}
	return -1
}
