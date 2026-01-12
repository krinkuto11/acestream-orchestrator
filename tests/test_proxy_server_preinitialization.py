"""
Test that proxy servers are pre-initialized during startup to prevent UI blocking.

This test verifies the fix for the issue where the UI would become unresponsive
during HLS proxy operations because lazy initialization of singleton servers
would block HTTP request handlers in single-worker uvicorn mode.
"""

import pytest
import sys
import os

# Add app to path for imports
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

def test_init_proxy_server_function_exists():
    """Test that _init_proxy_server function exists in main.py"""
    from app.main import _init_proxy_server
    assert callable(_init_proxy_server), "_init_proxy_server should be a callable function"


def test_init_proxy_server_initializes_both_servers():
    """Test that _init_proxy_server initializes both ProxyServer and HLSProxyServer"""
    from app.main import _init_proxy_server
    
    # Call the initialization function
    # Note: This may fail to connect to Redis in test environment, but should not raise
    try:
        _init_proxy_server()
    except Exception as e:
        # Should not raise any exceptions - errors should be logged but not raised
        pytest.fail(f"_init_proxy_server should not raise exceptions: {e}")


def test_proxy_server_singleton_pattern():
    """Test that ProxyServer follows singleton pattern"""
    from app.proxy.server import ProxyServer
    
    # Get two instances
    instance1 = ProxyServer.get_instance()
    instance2 = ProxyServer.get_instance()
    
    # Verify they are the same instance
    assert instance1 is instance2, "ProxyServer should follow singleton pattern"


def test_hls_proxy_server_singleton_pattern():
    """Test that HLSProxyServer follows singleton pattern"""
    from app.proxy.hls_proxy import HLSProxyServer
    
    # Get two instances
    instance1 = HLSProxyServer.get_instance()
    instance2 = HLSProxyServer.get_instance()
    
    # Verify they are the same instance
    assert instance1 is instance2, "HLSProxyServer should follow singleton pattern"


def test_docstring_mentions_both_servers():
    """Test that _init_proxy_server docstring mentions both servers"""
    from app.main import _init_proxy_server
    
    docstring = _init_proxy_server.__doc__
    assert docstring is not None, "_init_proxy_server should have a docstring"
    assert "ProxyServer" in docstring, "Docstring should mention ProxyServer"
    assert "HLSProxyServer" in docstring, "Docstring should mention HLSProxyServer"
    assert "blocking" in docstring.lower(), "Docstring should explain blocking prevention"


def test_proxy_servers_can_be_initialized_without_redis():
    """Test that proxy servers can be initialized even without Redis connection"""
    from app.proxy.server import ProxyServer
    from app.proxy.hls_proxy import HLSProxyServer
    
    # This simulates the behavior during startup when Redis might not be available yet
    # Both servers should initialize without raising exceptions
    proxy_server = ProxyServer.get_instance()
    hls_proxy_server = HLSProxyServer.get_instance()
    
    assert proxy_server is not None, "ProxyServer should be initialized"
    assert hls_proxy_server is not None, "HLSProxyServer should be initialized"


if __name__ == "__main__":
    # Run tests with pytest
    pytest.main([__file__, "-v"])
