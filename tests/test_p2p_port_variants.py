#!/usr/bin/env python3
"""
Test P2P port handling with unified global engine command adapter.
"""

from unittest.mock import patch

from app.services.engine_config import EngineConfig


@patch('app.services.engine_config.detect_platform', return_value='amd64')
@patch('app.services.engine_config.get_config', return_value=EngineConfig())
def test_p2p_port_handling(mock_get_config, mock_detect):
    """P2P port should be appended by orchestrator when available."""
    from app.services.provisioner import _get_variant_config

    c_http = 40123
    c_api = 40124
    p2p_port = 12345

    for variant in ('global', 'AceServe-amd64', 'AceServe-arm32', 'AceServe-arm64'):
        config = _get_variant_config(variant)
        base_cmd = config.get('base_cmd', [])
        cmd = base_cmd + ['--http-port', str(c_http), '--api-port', str(c_api), '--port', str(p2p_port)]

        assert '--port' in cmd
        port_index = cmd.index('--port')
        assert cmd[port_index + 1] == str(p2p_port)


@patch('app.services.engine_config.detect_platform', return_value='amd64')
@patch('app.services.engine_config.get_config', return_value=EngineConfig())
def test_p2p_port_without_gluetun(mock_get_config, mock_detect):
    """P2P port flag should not be added when no forwarded port is available."""
    from app.services.provisioner import _get_variant_config

    c_http = 40123
    c_api = 40124
    p2p_port = None

    for variant in ('global', 'AceServe-amd64', 'AceServe-arm32', 'AceServe-arm64'):
        config = _get_variant_config(variant)
        base_cmd = config.get('base_cmd', [])
        cmd = base_cmd + ['--http-port', str(c_http), '--api-port', str(c_api)]
        if p2p_port:
            cmd.extend(['--port', str(p2p_port)])

        assert '--port' not in cmd


if __name__ == '__main__':
    import sys

    success = True
    try:
        test_p2p_port_handling()
        test_p2p_port_without_gluetun()
    except Exception as e:
        print(f"\nTest failed with error: {e}")
        import traceback

        traceback.print_exc()
        success = False

    sys.exit(0 if success else 1)
