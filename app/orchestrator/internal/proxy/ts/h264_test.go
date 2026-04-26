package ts

import (
	"testing"
)

func TestIsKeyframe(t *testing.T) {
	// 1. Packet too short
	if IsKeyframe([]byte{0x47}) {
		t.Error("expected false for short packet")
	}

	// 2. No sync byte
	pkt := make([]byte, 188)
	if IsKeyframe(pkt) {
		t.Error("expected false for no sync byte")
	}

	// 3. No PUSI
	pkt[0] = 0x47
	pkt[1] = 0x00
	if IsKeyframe(pkt) {
		t.Error("expected false for no PUSI")
	}

	// 4. Valid H.264 IDR (minimal)
	pkt[1] = 0x40 // PUSI set
	pkt[3] = 0x10 // payload only
	// PES header: 00 00 01, stream id 0xE0, length 0, flags, header len 0
	payload := []byte{0x00, 0x00, 0x01, 0xE0, 0x00, 0x00, 0x80, 0x00, 0x00}
	// ES: NAL start code + IDR (type 5)
	es := []byte{0x00, 0x00, 0x01, 0x05}
	copy(pkt[4:], append(payload, es...))

	if !IsKeyframe(pkt) {
		t.Error("expected true for valid IDR")
	}

	// 5. Valid H.264 SPS
	es = []byte{0x00, 0x00, 0x01, 0x27} // 0x27 & 0x1F = 7 (SPS)
	copy(pkt[4+len(payload):], es)
	if !IsKeyframe(pkt) {
		t.Error("expected true for valid SPS")
	}

	// 6. Valid H.264 PPS
	es = []byte{0x00, 0x00, 0x00, 0x01, 0x28} // 0x28 & 0x1F = 8 (PPS), 4-byte start code
	copy(pkt[4+len(payload):], es)
	if !IsKeyframe(pkt) {
		t.Error("expected true for valid PPS")
	}

	// 7. Non-keyframe (type 1)
	es = []byte{0x00, 0x00, 0x01, 0x21} // 0x21 & 0x1F = 1 (non-IDR slice)
	copy(pkt[4+len(payload):], es)
	if IsKeyframe(pkt) {
		t.Error("expected false for non-keyframe slice")
	}
}
