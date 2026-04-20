package ts

// PCRResult contains the results of a TS packet scan.
type PCRResult struct {
	Value         float64
	HasPCR        bool
	Discontinuity bool
}

// FindPCR scans one 188-byte TS packet for timing info.
func FindPCR(pkt []byte) PCRResult {
	if len(pkt) < PacketSize || pkt[0] != SyncByte {
		return PCRResult{}
	}
	// Byte 3 bits [5:4]: 01 = adaptation only, 11 = adaptation + payload
	adaptFlag := (pkt[3] >> 5) & 0x01
	if adaptFlag == 0 {
		return PCRResult{}
	}
	adaptLen := int(pkt[4])
	if adaptLen < 1 || 5+adaptLen > PacketSize {
		return PCRResult{}
	}

	res := PCRResult{}
	// Byte pkt[5]: adaptation field flags
	flags := pkt[5]
	res.Discontinuity = (flags & 0x80) != 0

	// bit 4 = PCR_flag
	if flags&0x10 != 0 && adaptLen >= 7 {
		// PCR base: 33 bits spread across pkt[6..10], MSB first
		base := (uint64(pkt[6]) << 25) |
			(uint64(pkt[7]) << 17) |
			(uint64(pkt[8]) << 9) |
			(uint64(pkt[9]) << 1) |
			(uint64(pkt[10]) >> 7)
		res.Value = float64(base) / 90000.0
		res.HasPCR = true
	}
	return res
}
