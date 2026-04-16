"""
Utility functions for HLS manifest processing and keep-alive protection.
"""
import random
import string

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
