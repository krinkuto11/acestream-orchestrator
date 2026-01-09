"""
ProxyManager - Entry point for AceStream proxy.
Wraps the battle-tested ProxyServer for FastAPI integration.
"""

from .server import ProxyServer


class ProxyManager:
    """Singleton wrapper around ProxyServer for FastAPI integration."""
    
    @classmethod
    def get_instance(cls):
        """Get the ProxyServer singleton instance."""
        return ProxyServer.get_instance()
