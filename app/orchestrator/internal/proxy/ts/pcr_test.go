package ts

import (
	"testing"
)

// buildPCRPacket constructs a minimal 188-byte TS packet carrying a PCR.
// pid: 13-bit PID
// base: 33-bit PCR base (in 90 kHz ticks)
// ext: 9-bit PCR extension (in 300 Hz ticks)
func buildPCRPacket(pid uint16, base int64, ext int64) []byte {
	pkt := make([]byte, PacketSize)
	pkt[0] = SyncByte
	pkt[1] = byte(pid>>8) & 0x1F
	pkt[2] = byte(pid)
	// adaptation_field_control = 11 (adaptation + payload), continuity = 0
	pkt[3] = 0x30
	// adaptation field length = 7 (flags byte + 6 PCR bytes)
	pkt[4] = 7
	// flags: PCR_flag set (bit 4)
	pkt[5] = 0x10

	// PCR base (33 bits) packed into bytes 6–10:
	//   byte 6: base[32:25]
	//   byte 7: base[24:17]
	//   byte 8: base[16:9]
	//   byte 9: base[8:1]
	//   byte 10 bit 7: base[0], then bit 6=reserved(1), bits 5:1=reserved(1), bit 0: ext[8]
	pkt[6] = byte(base >> 25)
	pkt[7] = byte(base >> 17)
	pkt[8] = byte(base >> 9)
	pkt[9] = byte(base >> 1)
	// byte 10: [base0][1][111111][ext8]
	pkt[10] = byte((base&1)<<7) | 0x7E | byte(ext>>8)
	pkt[11] = byte(ext)

	return pkt
}

func TestScanForLastPCR_basic(t *testing.T) {
	const wantPID = uint16(0x100)
	const wantBase = int64(810_000_000) // 9000 s at 90 kHz
	const wantExt = int64(42)
	wantTicks := wantBase*300 + wantExt // 243_000_000_042

	pkt := buildPCRPacket(wantPID, wantBase, wantExt)

	ticks, pid, found := ScanForLastPCR(pkt)
	if !found {
		t.Fatal("expected PCR to be found")
	}
	if pid != wantPID {
		t.Errorf("pid: got %d, want %d", pid, wantPID)
	}
	if ticks != wantTicks {
		t.Errorf("ticks: got %d, want %d", ticks, wantTicks)
	}
}

func TestScanForLastPCR_returnsLast(t *testing.T) {
	// Two packets — function must return the second (last) PCR.
	pkt1 := buildPCRPacket(0x100, 1_000_000, 0)
	pkt2 := buildPCRPacket(0x100, 2_000_000, 0)

	data := append(pkt1, pkt2...)
	ticks, _, found := ScanForLastPCR(data)
	if !found {
		t.Fatal("expected PCR to be found")
	}
	want := int64(2_000_000) * 300
	if ticks != want {
		t.Errorf("expected last PCR ticks %d, got %d", want, ticks)
	}
}

func TestScanForLastPCR_noPCR(t *testing.T) {
	// Payload-only packet (no adaptation field).
	pkt := make([]byte, PacketSize)
	pkt[0] = SyncByte
	pkt[3] = 0x10 // adaptation_field_control = 01 (payload only)

	_, _, found := ScanForLastPCR(pkt)
	if found {
		t.Fatal("should not find PCR in payload-only packet")
	}
}

func TestScanForLastPCR_emptyInput(t *testing.T) {
	_, _, found := ScanForLastPCR(nil)
	if found {
		t.Fatal("should not find PCR in nil input")
	}
	_, _, found = ScanForLastPCR([]byte{})
	if found {
		t.Fatal("should not find PCR in empty input")
	}
}

// BenchmarkScanForLastPCR measures throughput on a realistic 1 MB chunk
// (5644 packets, ~1 PCR every 10 packets).
func BenchmarkScanForLastPCR(b *testing.B) {
	const nPkts = 5644
	data := make([]byte, nPkts*PacketSize)
	for i := 0; i < nPkts; i++ {
		off := i * PacketSize
		data[off] = SyncByte
		data[off+3] = 0x10 // payload only — no adaptation field (most packets)
	}
	// Add PCR every 10th packet.
	for i := 0; i < nPkts; i += 10 {
		pkt := buildPCRPacket(0x100, int64(i)*90_000, 0)
		copy(data[i*PacketSize:], pkt)
	}

	b.SetBytes(int64(len(data)))
	b.ResetTimer()
	for n := 0; n < b.N; n++ {
		ScanForLastPCR(data)
	}
}
