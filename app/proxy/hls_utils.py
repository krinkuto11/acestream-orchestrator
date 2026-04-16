"""
Utility functions for HLS manifest processing and keep-alive protection.
"""
import random
import string

def get_hls_padding_comment(size_bytes: int = 1880) -> bytes:
    """
    Generate a large HLS comment to act as padding (Fat Keep-Alive).
    Default size is 1880 bytes to match a TS Fat Keep-Alive (10 packets).
    """
    # Using a deterministic character to avoid compression wins and keep data predictable
    padding_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    content = "A" * max(0, size_bytes - 30) # Adjust for header
    return f"# PADDING-{padding_id}: {content}\n".encode("utf-8")
