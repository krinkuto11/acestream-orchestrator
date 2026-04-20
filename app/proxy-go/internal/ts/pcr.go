package ts

// FindPCR scans one 188-byte TS packet for a PCR value in the adaptation field.
// Returns the PCR in seconds (PCR base / 90000) and true if found.
// PCR base is a 33-bit 90 kHz counter; we ignore the 9-bit 27 MHz extension
// since ±33 ms accuracy is more than sufficient for HLS segment cutting.
func FindPCR(pkt []byte) (float64, bool) {
	if len(pkt) < PacketSize || pkt[0] != SyncByte {
		return 0, false
	}
	// Byte 3 bits [5:4]: 01 = adaptation only, 11 = adaptation + payload
	adaptFlag := (pkt[3] >> 5) & 0x01
	if adaptFlag == 0 {
		return 0, false
	}
	adaptLen := int(pkt[4])
	// Need at least flags byte + 6 PCR bytes
	if adaptLen < 7 || 5+adaptLen > PacketSize {
		return 0, false
	}
	// Byte pkt[5]: adaptation field flags; bit 4 = PCR_flag
	if pkt[5]&0x10 == 0 {
		return 0, false
	}
	// PCR base: 33 bits spread across pkt[6..10], MSB first
	base := (uint64(pkt[6]) << 25) |
		(uint64(pkt[7]) << 17) |
		(uint64(pkt[8]) << 9) |
		(uint64(pkt[9]) << 1) |
		(uint64(pkt[10]) >> 7)
	return float64(base) / 90000.0, true
}
