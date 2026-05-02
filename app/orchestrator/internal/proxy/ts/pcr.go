package ts

// PCRResult contains the results of a TS packet scan.
type PCRResult struct {
	Value         float64
	HasPCR        bool
	Discontinuity bool
	RandomAccess  bool
}

// FindPCR scans one 188-byte TS packet for timing info.
// Returns PCR as seconds (90 kHz base only). Used by the HLS segmenter.
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
	flags := pkt[5]
	res.Discontinuity = (flags & 0x80) != 0
	res.RandomAccess = (flags & 0x40) != 0

	// bit 4 = PCR_flag
	if flags&0x10 != 0 && adaptLen >= 7 {
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

// ScanForLastPCR scans a block of 188-byte-aligned TS packets and returns the
// last PCR value found, its PID, and whether any PCR was found.
//
// The full 42-bit PCR value is returned in 27 MHz ticks:
//   ticks = base×300 + extension
//
// Scanning to the end of the block (rather than stopping at the first hit)
// gives the most recent timestamp, which makes the bitrate measurement more
// accurate when a chunk spans multiple PCR intervals.
//
// No allocations. Safe to call from any goroutine.
func ScanForLastPCR(data []byte) (ticks int64, pid uint16, found bool) {
	for i := 0; i+PacketSize <= len(data); {
		if data[i] != SyncByte {
			next := bytes.IndexByte(data[i+1:], SyncByte)
			if next < 0 {
				break
			}
			i += next + 1
			continue
		}

		pkt := data[i : i+PacketSize]
		// adaptation_field_control is bits [5:4] of byte 3.
		// Value 0x2 (adaptation only) or 0x3 (adaptation+payload) → has adaptation.
		if (pkt[3]>>4)&0x3 >= 2 {
			aflLen := int(pkt[4])
			if aflLen >= 7 && 5+aflLen <= PacketSize {
				if pkt[5]&0x10 != 0 { // PCR_flag set
					p := uint16(pkt[1]&0x1F)<<8 | uint16(pkt[2])
					base := int64(pkt[6])<<25 | int64(pkt[7])<<17 | int64(pkt[8])<<9 |
						int64(pkt[9])<<1 | int64(pkt[10]>>7)
					ext := int64(pkt[10]&0x01)<<8 | int64(pkt[11])
					ticks = base*300 + ext
					pid = p
					found = true
				}
			}
		}
		i += PacketSize
	}
	return
}

