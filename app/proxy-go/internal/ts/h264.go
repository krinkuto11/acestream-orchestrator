package ts

import "bytes"

// IsKeyframe returns true if the MPEG-TS packet contains the start of an
// H.264 IDR frame (keyframe) or a sequence/picture parameter set (SPS/PPS).
//
// It performs a "shallow" scan of the packet's payload for H.264 NAL unit
// start codes (0x000001 or 0x00000001).
func IsKeyframe(pkt []byte) bool {
	if len(pkt) < PacketSize || pkt[0] != SyncByte {
		return false
	}

	// Payload Unit Start Indicator (PUSI) bit must be set for the start of a frame.
	if (pkt[1] & 0x40) == 0 {
		return false
	}

	// Calculate offset to payload
	payloadStart := 4
	adaptControl := (pkt[3] >> 4) & 0x03
	if adaptControl == 0x02 || adaptControl == 0x03 {
		adaptLen := int(pkt[4])
		payloadStart += 1 + adaptLen
	}

	if payloadStart >= PacketSize {
		return false
	}

	payload := pkt[payloadStart:]

	// Look for PES header start code: 00 00 01
	if len(payload) < 6 || !bytes.HasPrefix(payload, []byte{0x00, 0x00, 0x01}) {
		return false
	}

	// Stream ID must be video (0xE0 - 0xEF)
	streamID := payload[3]
	if streamID < 0xE0 || streamID > 0xEF {
		return false
	}

	// PES header length
	pesHeaderLen := 6 + int(payload[8]) + 3 // simplified, 3 is the length of basic PES fields
	if pesHeaderLen >= len(payload) {
		// Try a direct scan if the PES header parsing is too strict for this chunk
		pesHeaderLen = 9
	}

	es := payload[pesHeaderLen:]

	// Scan for NAL unit start codes (00 00 01)
	// We only check the first few bytes of the ES for efficiency.
	for i := 0; i < len(es)-4; i++ {
		if es[i] == 0x00 && es[i+1] == 0x00 {
			nalIdx := -1
			if es[i+2] == 0x01 {
				nalIdx = i + 3
			} else if es[i+2] == 0x00 && es[i+3] == 0x01 {
				nalIdx = i + 4
			}

			if nalIdx != -1 && nalIdx < len(es) {
				nalType := es[nalIdx] & 0x1F
				// NAL unit types:
				// 5: Coded slice of an IDR picture (Keyframe)
				// 7: Sequence Parameter Set (SPS)
				// 8: Picture Parameter Set (PPS)
				if nalType == 5 || nalType == 7 || nalType == 8 {
					return true
				}
			}
		}
	}

	return false
}
