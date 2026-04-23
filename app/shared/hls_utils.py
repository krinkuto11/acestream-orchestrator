"""
Utility functions for HLS manifest processing and keep-alive protection.
"""
import random
import string

# Standard MPEG-TS NULL packet (188 bytes)
TS_NULL_PACKET = b"\x47\x1f\xff\x10" + b"\xff" * 184

def get_ts_null_padding(size_bytes: int = 8272, cc: int = 0) -> tuple[bytes, int]:
    """
    Generate a block of MPEG-TS NULL packets for segment-level prebuffering.
    Returns a tuple of (bytes, next_cc).
    """
    count = size_bytes // 188
    packets = []
    current_cc = cc
    for _ in range(count):
        header = b"\x47\x1f\xff" + bytes([0x10 | (current_cc & 0x0F)])
        packets.append(header + b"\xff" * 184)
        current_cc = (current_cc + 1) & 0x0F
    
    return b"".join(packets), current_cc

def get_hls_padding_comment(size_bytes: int = 1880) -> bytes:
    """
    Generate a large HLS comment to act as padding (Fat Keep-Alive).
    """
    padding_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    header = f"# PADDING-{padding_id}: "
    current_overhead = len(header) + 1
    padding_count = max(0, size_bytes - current_overhead)
    content = "A" * padding_count
    result = f"{header}{content}\n".encode("utf-8")
    return result[:size_bytes]
