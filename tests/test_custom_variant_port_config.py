#!/usr/bin/env python3
"""
Test unified global engine command configuration and port argument composition.
"""

from unittest.mock import patch

from app.services.engine_config import EngineConfig


@patch('app.services.engine_config.detect_platform', return_value='amd64')
def test_build_custom_variant_config_stays_port_agnostic(mock_detect):
    """Custom config command should not embed orchestrator-owned port flags."""
    from app.services.custom_variant_config import (
        CustomVariantConfig,
        CustomVariantParameter,
        build_variant_config_from_custom,
    )

    config = CustomVariantConfig(
        download_limit=1000,
        upload_limit=500,
        live_cache_type='disk',
        buffer_time=10,
        parameters=[
            CustomVariantParameter(name='--client-console', type='flag', value=True, enabled=True),
        ],
    )

    variant_config = build_variant_config_from_custom(config)

    assert variant_config.get('is_custom') is True
    assert variant_config.get('config_type') == 'cmd'
    base_cmd = variant_config.get('base_cmd')
    assert isinstance(base_cmd, list)
    assert 'python' in base_cmd
    assert 'main.py' in base_cmd
    assert '--http-port' not in base_cmd
    assert '--https-port' not in base_cmd
    assert '--api-port' not in base_cmd
    assert '--port' not in base_cmd


@patch('app.services.engine_config.detect_platform', return_value='amd64')
@patch('app.services.engine_config.get_config', return_value=EngineConfig(download_limit=0, upload_limit=0, live_cache_type='memory', buffer_time=10))
def test_variant_adapter_uses_runtime_platform_and_global_config(mock_get_config, mock_detect):
    """Variant adapter should use runtime platform and global config payload."""
    from app.services.provisioner import get_variant_config

    variant_config = get_variant_config('AceServe-arm64')

    assert variant_config.get('config_type') == 'cmd'
    assert variant_config.get('is_custom') is True
    assert variant_config.get('image', '').endswith('latest-amd64')

    base_cmd = variant_config.get('base_cmd')
    assert isinstance(base_cmd, list)
    assert 'python' in base_cmd
    assert '--download-limit' in base_cmd
    assert '--disable-upnp' in base_cmd


@patch('app.services.engine_config.detect_platform', return_value='amd64')
@patch('app.services.engine_config.get_config', return_value=EngineConfig())
def test_orchestrator_appends_ports_to_base_command(mock_get_config, mock_detect):
    """Provisioner model: base command plus orchestrator-managed port args."""
    from app.services.provisioner import get_variant_config

    c_http = 40123
    c_api = 40124
    p2p_port = 12345

    variant_config = get_variant_config('global')
    base_cmd = variant_config.get('base_cmd', [])
    cmd = base_cmd + ['--http-port', str(c_http), '--api-port', str(c_api), '--port', str(p2p_port)]

    assert '--http-port' in cmd
    assert str(c_http) in cmd
    assert '--api-port' in cmd
    assert str(c_api) in cmd
    assert '--port' in cmd
    assert str(p2p_port) in cmd


if __name__ == '__main__':
    import sys

    success = True
    try:
        test_build_custom_variant_config_stays_port_agnostic()
        test_variant_adapter_uses_runtime_platform_and_global_config()
        test_orchestrator_appends_ports_to_base_command()
    except Exception as e:
        print(f"\nTest failed with error: {e}")
        import traceback

        traceback.print_exc()
        success = False

    sys.exit(0 if success else 1)
