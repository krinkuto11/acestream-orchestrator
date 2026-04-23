import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def sanitize_stream_id(val: str) -> str:
    """
    Unified sanitization for all stream identifiers (content_id, infohash, monitor_id).
    Ensures absolute parity between API client, data plane, and orchestrator state.
    """
    raw = str(val or "").strip()
    if not raw:
        return ""
    
    # 1. Strip common binary/shell/JSON junk characters: \ { } ' "
    # We strip these entirely as they are almost certainly copy-paste artifacts.
    stripped = raw.strip().strip("\\{}'\"").strip()
    if not stripped:
        return "unknown"
    
    # 2. Regular expression for filesystem and URL safety.
    # Replaces any remaining non-alphanumeric (except _.-) with underscores.
    # We enforce lowercase for absolute case-insensitivity across the stack.
    return re.sub(r"[^a-zA-Z0-9_|.-]", "_", stripped).lower()

def get_client_ip(request):
    """
    Extract client IP address from FastAPI request.
    Handles cases where request is behind a proxy by checking X-Forwarded-For.
    """
    x_forwarded_for = request.headers.get('X-Forwarded-For')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.client.host if request.client else "unknown"
    return ip

def get_logger(name=None):
    """Standardized logger for shared components."""
    if name:
        return logging.getLogger(f"ace_proxy.{name}")
    return logging.getLogger("ace_proxy")

class SyncHunter:
    """
    TS Sync Hunter - Ensures stream alignment by finding the first 0x47 sync byte
    and verifying the packet structure before allowing data through.
    """
    MAX_INVALID_SYNC = 5
    
    def __init__(self, required_confirmations=3, align_to_frame=True):
        self.buffer = bytearray()
        self.is_locked = False
        self.packet_size = 188
        self.sync_byte = 0x47
        self.required_confirmations = required_confirmations
        self.align_to_frame = align_to_frame
        self.invalid_sync_count = 0
        self.logger = get_logger("SyncHunter")

    def feed(self, data: bytes) -> bytes:
        if not data:
            return b""
            
        self.buffer.extend(data)
        
        if self.is_locked:
            valid_length = (len(self.buffer) // self.packet_size) * self.packet_size
            if valid_length == 0:
                return b""

            to_output = bytearray()
            for i in range(0, valid_length, self.packet_size):
                if self.buffer[i] == self.sync_byte:
                    to_output.extend(self.buffer[i:i+self.packet_size])
                    self.invalid_sync_count = 0
                else:
                    self.invalid_sync_count += 1
                    if self.invalid_sync_count >= self.MAX_INVALID_SYNC:
                        self.logger.warning(f"Sync lost after {self.invalid_sync_count} invalid bytes. Re-entering hunting mode.")
                        self.is_locked = False
                        del self.buffer[:i]
                        return bytes(to_output)
            
            del self.buffer[:valid_length]
            return bytes(to_output)

        while len(self.buffer) >= (self.packet_size * self.required_confirmations):
            try:
                first_sync = self.buffer.index(self.sync_byte)
            except ValueError:
                del self.buffer[:-1]
                return b""

            if first_sync > 0:
                del self.buffer[:first_sync]
                if len(self.buffer) < (self.packet_size * self.required_confirmations):
                    break
            
            if self.align_to_frame:
                is_start_of_frame = bool(self.buffer[1] & 0x40)
                if not is_start_of_frame:
                    del self.buffer[:1]
                    continue

            verified = True
            for i in range(1, self.required_confirmations):
                if self.buffer[i * self.packet_size] != self.sync_byte:
                    verified = False
                    break
            
            if verified:
                self.is_locked = True
                self.invalid_sync_count = 0
                self.logger.info(f"Sync Hunter locked onto frame boundary (verified {self.required_confirmations} packets)")
                
                valid_length = (len(self.buffer) // self.packet_size) * self.packet_size
                aligned_data = bytes(self.buffer[:valid_length])
                del self.buffer[:valid_length]
                return aligned_data
            else:
                del self.buffer[:1]
                continue
                
        return b""

    def reset(self):
        self.is_locked = False
        self.buffer.clear()
