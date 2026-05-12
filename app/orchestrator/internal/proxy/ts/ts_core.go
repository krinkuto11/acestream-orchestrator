package ts

const (
	PacketSize    = 188
	SyncByte      = 0x47
	SyncByteIndex = 0
	CCIndex       = 3 // Continuity counter location: byte 3
	CCMask        = 0x0F
	PUSIBit       = 0x40 // Payload unit start indicator (byte 1)
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
