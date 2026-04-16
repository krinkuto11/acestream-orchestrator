"""
Utility functions for AceStream Proxy.
Adapted from ts_proxy utils - removed Django dependencies.
"""

import logging
import re
from urllib.parse import urlparse
import inspect


logger = logging.getLogger("ace_proxy")


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
    
    Args:
        request: FastAPI Request object
        
    Returns:
        str: Client IP address
    """
    # Check X-Forwarded-For header first (for proxy scenarios)
    x_forwarded_for = request.headers.get('X-Forwarded-For')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        # Fallback to direct client IP
        ip = request.client.host if request.client else "unknown"
    return ip


def create_ts_packet(packet_type='null', message=None, pid_high=None, pid_low=None, cc=0):
    """
    Create a Transport Stream (TS) packet for various purposes.
    
    Args:
        packet_type (str): Type of packet - 'null', 'error', 'keepalive', etc.
        message (str): Optional message to include in packet payload
        pid_high (int): Optional explicit PID high bits override
        pid_low (int): Optional explicit PID low bits override
        cc (int): Continuity counter value (0-15)
        
    Returns:
        bytes: A properly formatted 188-byte TS packet
    """
    packet = bytearray(188)
    
    # TS packet header
    packet[0] = 0x47  # Sync byte
    
    # PID - Use explicit overrides if provided, otherwise fallback to defaults
    if pid_high is not None and pid_low is not None:
        packet[1] = pid_high & 0x1F  # Mask high bits (PID is 13 bits)
        packet[2] = pid_low & 0xFF
    elif packet_type == 'error':
        packet[1] = 0x1F  # PID high bits
        packet[2] = 0xFF  # PID low bits
    else:  # null/keepalive packets
        packet[1] = 0x1F  # PID high bits (null packet)
        packet[2] = 0xFF  # PID low bits (null packet)
    
    # Adaptation field and payload indicator
    # Set adaptation field control to '01' (payload only) for simplicity
    # Set continuity counter (last 4 bits of byte 3)
    packet[3] = 0x10 | (cc & 0x0F)
    
    # Add message to payload if provided
    if message:
        msg_bytes = message.encode('utf-8')
        packet[4:4+min(len(msg_bytes), 180)] = msg_bytes[:180]
    
    return bytes(packet)


class SyncHunter:
    """
    TS Sync Hunter - Ensures stream alignment by finding the first 0x47 sync byte
    and verifying the packet structure before allowing data through.
    
    Hardened Version:
    1. Wait for PUSI=1 (Start of Frame) before locking to ensure clean decoder starts.
    2. Self-Healing: If sync is lost while locked, drop back to hunting mode.
    """
    
    # Maximum consecutive invalid sync bytes allowed before dropping lock
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
        """
        Feed raw data and return perfectly aligned 188-byte TS packets.
        Returns empty bytes until synchronization is 'Locked'.
        """
        if not data:
            return b""
            
        self.buffer.extend(data)
        
        # --- SELF-HEALING / LOCKED PATH ---
        if self.is_locked:
            valid_length = (len(self.buffer) // self.packet_size) * self.packet_size
            if valid_length == 0:
                return b""

            # Verify sync bytes in the outgoing buffer
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
                        del self.buffer[:i] # Keep the rest for re-hunting
                        return bytes(to_output)
            
            del self.buffer[:valid_length]
            return bytes(to_output)

        # --- HUNTING MODE ---
        # We need at least (confirmations * size) to verify a sequence
        while len(self.buffer) >= (self.packet_size * self.required_confirmations):
            # Find the first 0x47
            try:
                first_sync = self.buffer.index(self.sync_byte)
            except ValueError:
                # No sync byte at all, keep only the last partial bit for next feed
                del self.buffer[:-1]
                return b""

            # Discard junk before the sync byte
            if first_sync > 0:
                del self.buffer[:first_sync]
                if len(self.buffer) < (self.packet_size * self.required_confirmations):
                    break
            
            # OPTIONAL: Frame Alignment (Wait for PUSI=1)
            # PES packets (H.264 frames) start with the PUSI bit set in the TS header.
            # packet[1] & 0x40 == 1
            if self.align_to_frame:
                is_start_of_frame = bool(self.buffer[1] & 0x40)
                if not is_start_of_frame:
                    del self.buffer[:1]
                    continue

            # Verify the sequence of sync bytes
            verified = True
            for i in range(1, self.required_confirmations):
                if self.buffer[i * self.packet_size] != self.sync_byte:
                    verified = False
                    break
            
            if verified:
                self.is_locked = True
                self.invalid_sync_count = 0
                self.logger.info(f"Sync Hunter locked onto frame boundary (verified {self.required_confirmations} packets)")
                
                # Return all complete packets
                valid_length = (len(self.buffer) // self.packet_size) * self.packet_size
                aligned_data = bytes(self.buffer[:valid_length])
                del self.buffer[:valid_length]
                return aligned_data
            else:
                # This 0x47 was a 'false sync'. Skip one byte and keep hunting.
                del self.buffer[:1]
                continue
                
        return b""

    def reset(self):
        """Reset the hunter to hunting mode (e.g. after a failover)"""
        self.is_locked = False
        self.buffer.clear()


def get_logger(component_name=None):
    # (Existing implementation kept for reference or move it above if needed)
    # ...
    """
    Get a standardized logger with ace_proxy prefix and optional component name.
    
    Args:
        component_name (str, optional): Name of the component. If not provided,
                                      will try to detect from the calling module.
                                      
    Returns:
        logging.Logger: A configured logger with standardized naming.
    """
    if component_name:
        logger_name = f"ace_proxy.{component_name}"
    else:
        # Try to get the calling module name if not explicitly specified
        frame = inspect.currentframe().f_back
        module = inspect.getmodule(frame)
        if module:
            # Extract just the filename without extension
            module_name = module.__name__.split('.')[-1]
            logger_name = f"ace_proxy.{module_name}"
        else:
            # Default if detection fails
            logger_name = "ace_proxy"
    
    return logging.getLogger(logger_name)
