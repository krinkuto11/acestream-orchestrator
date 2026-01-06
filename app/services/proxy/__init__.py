"""AceStream Proxy Services

This module provides a stateless HTTP proxy for AceStream engines with:
- Intelligent engine selection (prioritizes forwarded, balances load)
- Client multiplexing (multiple clients share same stream)
- Automatic lifecycle management
- VPN-aware provisioning
"""

from .proxy_manager import ProxyManager
from .stream_session import StreamSession
from .client_manager import ClientManager
from .engine_selector import EngineSelector

__all__ = ["ProxyManager", "StreamSession", "ClientManager", "EngineSelector"]
