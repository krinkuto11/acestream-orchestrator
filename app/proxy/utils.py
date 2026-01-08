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


def create_ts_packet(packet_type='null', message=None):
    """
    Create a Transport Stream (TS) packet for various purposes.
    
    Args:
        packet_type (str): Type of packet - 'null', 'error', 'keepalive', etc.
        message (str): Optional message to include in packet payload
        
    Returns:
        bytes: A properly formatted 188-byte TS packet
    """
    packet = bytearray(188)
    
    # TS packet header
    packet[0] = 0x47  # Sync byte
    
    # PID - Use different PIDs based on packet type
    if packet_type == 'error':
        packet[1] = 0x1F  # PID high bits
        packet[2] = 0xFF  # PID low bits
    else:  # null/keepalive packets
        packet[1] = 0x1F  # PID high bits (null packet)
        packet[2] = 0xFF  # PID low bits (null packet)
    
    # Add message to payload if provided
    if message:
        msg_bytes = message.encode('utf-8')
        packet[4:4+min(len(msg_bytes), 180)] = msg_bytes[:180]
    
    return bytes(packet)


def get_logger(component_name=None):
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
