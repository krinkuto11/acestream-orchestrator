"""
Utility functions for AceStream Proxy.
Adapted from ts_proxy utils - removed Django dependencies.
"""

import logging
import re
from urllib.parse import urlparse
import inspect


logger = logging.getLogger("ace_proxy")


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
    """
    
    def __init__(self, required_confirmations=3):
        self.buffer = bytearray()
        self.is_locked = False
        self.packet_size = 188
        self.sync_byte = 0x47
        self.required_confirmations = required_confirmations
        self.logger = get_logger("SyncHunter")

    def feed(self, data: bytes) -> bytes:
        """
        Feed raw data and return perfectly aligned 188-byte TS packets.
        Returns empty bytes until synchronization is 'Locked'.
        """
        if not data:
            return b""
            
        self.buffer.extend(data)
        
        # If already locked, just return what we have (preserving alignment)
        if self.is_locked:
            valid_length = (len(self.buffer) // self.packet_size) * self.packet_size
            if valid_length > 0:
                aligned_data = bytes(self.buffer[:valid_length])
                del self.buffer[:valid_length]
                return aligned_data
            return b""

        # HUNTING MODE: Find the first sync byte and verify sequence
        while len(self.buffer) >= (self.packet_size * self.required_confirmations):
            # Find the first 0x47
            try:
                first_sync = self.buffer.index(self.sync_byte)
            except ValueError:
                # No sync byte in the entire buffer, discard all except the very last byte
                # in case it's the start of a multi-byte sequence (not applicable for single 0x47 though)
                self.buffer.clear()
                return b""

            # Discard junk before the sync byte
            if first_sync > 0:
                del self.buffer[:first_sync]
            
            # Verify the sequence of sync bytes
            verified = True
            for i in range(1, self.required_confirmations):
                if self.buffer[i * self.packet_size] != self.sync_byte:
                    verified = False
                    break
            
            if verified:
                self.is_locked = True
                self.logger.info(f"Sync Hunter locked after {first_sync} bytes of junk.")
                
                # Return all complete packets
                valid_length = (len(self.buffer) // self.packet_size) * self.packet_size
                aligned_data = bytes(self.buffer[:valid_length])
                del self.buffer[:valid_length]
                return aligned_data
            else:
                # This 0x47 was a 'false sync' (found in payload). Discard it and keep hunting.
                # Correct fix: skip this sync byte and look for the next one.
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
