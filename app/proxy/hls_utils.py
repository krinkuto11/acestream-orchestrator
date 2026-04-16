"""
Utility functions for HLS manifest processing and keep-alive protection.
"""
import random
import string

# Standard MPEG-TS NULL packet (188 bytes)
# Header: 47 1f ff 10 (PID 0x1fff, Continuity Counter 0)
# Payload: 184 bytes of 0xFF
TS_NULL_PACKET = b"\x47\x1f\xff\x10" + b"\xff" * 184

def get_ts_null_padding(size_bytes: int = 8272) -> bytes:
    """
    Generate a block of MPEG-TS NULL packets for segment-level prebuffering.
    Default size 8272 is 188 * 44 (TS aligned).
    """
    count = size_bytes // 188
    return TS_NULL_PACKET * count

def get_hls_padding_comment(size_bytes: int = 1880) -> bytes:
    """
    Generate a large HLS comment to act as padding (Fat Keep-Alive).
    Default size is 1880 bytes to match a TS Fat Keep-Alive (10 packets).
    Returns exactly size_bytes of data.
    """
    # Header: # PADDING-XXXXXXXX: 
    # 8 chars for ID + overhead = ~20 bytes
    padding_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    header = f"# PADDING-{padding_id}: "
    
    # Calculate remainder needed (header length + trailing newline)
    # Ensure we don't underflow
    current_overhead = len(header) + 1 # 1 for \n
    padding_count = max(0, size_bytes - current_overhead)
    
    content = "A" * padding_count
    result = f"{header}{content}\n".encode("utf-8")
    
    # Final safety check/trim to ensure exact size if multi-byte chars were used (though A is 1 byte)
    return result[:size_bytes]
