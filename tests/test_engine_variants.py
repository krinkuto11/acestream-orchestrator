#!/usr/bin/env python3
"""
Test unified engine config variant adapter behavior.
"""

import unittest.mock
from app.services.engine_config import EngineConfig

@unittest.mock.patch('app.services.engine_config.detect_platform', return_value='amd64')
@unittest.mock.patch('app.services.engine_config.get_config', return_value=EngineConfig(download_limit=300, upload_limit=150, buffer_time=12, live_cache_type='memory'))
def test_variant_configs(mock_get_config, mock_detect):
    """Test adapter shape for requested variants under unified config."""
    print("🧪 Testing Unified Variant Adapter Configurations")
    print("=" * 60)

    from app.services.provisioner import _get_variant_config

    variants = ['global', 'AceServe-amd64', 'AceServe-arm32', 'AceServe-arm64']

    for variant in variants:
        config = _get_variant_config(variant)
        print(f"\n📋 Testing variant: {variant}")
        print(f"   Image: {config['image']}")
        print(f"   Config type: {config['config_type']}")

        # Validate required fields
        assert 'image' in config, f"Missing 'image' for {variant}"
        assert 'config_type' in config, f"Missing 'config_type' for {variant}"
        assert config['config_type'] == 'cmd', f"Expected cmd config_type for {variant}"
        assert config.get('is_custom') is True, f"Expected is_custom=True for {variant}"

        assert 'base_cmd' in config, f"Missing 'base_cmd' for {variant}"
        assert isinstance(config['base_cmd'], list), f"base_cmd should be a list for {variant}"
        assert 'python' in config['base_cmd'], f"Missing python in base_cmd for {variant}"
        assert 'main.py' in config['base_cmd'], f"Missing main.py in base_cmd for {variant}"
        assert '--download-limit' in config['base_cmd'], f"Missing --download-limit in base_cmd for {variant}"
        assert '300' in config['base_cmd'], f"Missing download-limit value in base_cmd for {variant}"
        assert '--disable-upnp' in config['base_cmd'], f"Missing --disable-upnp in base_cmd for {variant}"
        assert '--bind-all' not in config['base_cmd'], f"Unexpected --bind-all in base_cmd for {variant}"
        print(f"   ✓ CMD-based unified config with {len(config['base_cmd'])} args")

    print(f"\n✅ All {len(variants)} variants configured correctly!")


@unittest.mock.patch('app.services.engine_config.detect_platform', return_value='arm64')
@unittest.mock.patch('app.services.engine_config.get_config', return_value=EngineConfig())
def test_variant_adapter_follows_runtime_platform(mock_get_config, mock_detect):
    """Test that requested variant name does not override runtime platform image."""
    print("\n🧪 Testing Runtime Platform Resolution")
    print("=" * 60)

    from app.services.provisioner import _get_variant_config

    config = _get_variant_config('AceServe-amd64')
    assert config['image'].endswith('latest-arm64')
    print("   ✓ Adapter uses runtime platform for image selection")


def test_config_loading():
    """Test that ENGINE_VARIANT config still loads legacy values."""
    print("\n🧪 Testing Configuration Loading")
    print("=" * 60)

    import os
    import sys

    # Test default value
    if 'app.core.config' in sys.modules:
        del sys.modules['app.core.config']
    os.environ.pop('ENGINE_VARIANT', None)
    with unittest.mock.patch('platform.machine', return_value='x86_64'):
        from app.core.config import Cfg
        cfg = Cfg()
    print(f"\n📋 Default ENGINE_VARIANT: {cfg.ENGINE_VARIANT}")
    assert cfg.ENGINE_VARIANT == 'AceServe-amd64', "Default should be AceServe-amd64"
    print("   ✓ Default value correct")

    # Test each valid variant
    for variant in ['AceServe-amd64', 'AceServe-arm32', 'AceServe-arm64']:
        if 'app.core.config' in sys.modules:
            del sys.modules['app.core.config']
        os.environ['ENGINE_VARIANT'] = variant
        from app.core.config import Cfg
        cfg = Cfg()
        print(f"\n📋 Testing ENGINE_VARIANT={variant}")
        assert cfg.ENGINE_VARIANT == variant, f"Should load {variant}"
        print(f"   ✓ Loaded correctly")

    print("\n✅ Configuration loading tests passed!")


if __name__ == "__main__":
    import sys
    sys.path.append('.')
    
    success = True
    try:
        test_variant_configs()
        test_variant_adapter_follows_runtime_platform()
        test_config_loading()
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        success = False
    
    if success:
        print("\n" + "=" * 60)
        print("🎉 ALL TESTS PASSED!")
        print("=" * 60)
    
    sys.exit(0 if success else 1)
