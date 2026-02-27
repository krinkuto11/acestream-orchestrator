#!/usr/bin/env python3
"""
Test AceStream engine variants configuration.
"""

import unittest.mock

@unittest.mock.patch('app.services.custom_variant_config.detect_platform', return_value='amd64')
@unittest.mock.patch('app.services.custom_variant_config.is_custom_variant_enabled', return_value=False)
def test_variant_configs(mock_is_custom, mock_detect):
    """Test that all variants have proper configuration."""
    print("🧪 Testing Engine Variant Configurations")
    print("=" * 60)
    
    from app.services.provisioner import _get_variant_config
    
    variants = ['krinkuto11-amd64', 'jopsis-amd64', 'jopsis-arm32', 'jopsis-arm64']
    
    for variant in variants:
        config = _get_variant_config(variant)
        print(f"\n📋 Testing variant: {variant}")
        print(f"   Image: {config['image']}")
        print(f"   Config type: {config['config_type']}")
        
        # Validate required fields
        assert 'image' in config, f"Missing 'image' for {variant}"
        assert 'config_type' in config, f"Missing 'config_type' for {variant}"
        assert config['config_type'] in ['env', 'cmd'], f"Invalid config_type for {variant}"
        
        # Validate type-specific fields
        if config['config_type'] == 'env':
            # Custom variants or legacy paths
            print(f"   ✓ ENV-based variant")
        else:
            assert 'base_cmd' in config, f"Missing 'base_cmd' for {variant}"
            assert isinstance(config['base_cmd'], list), f"base_cmd should be a list for {variant}"
            # krinkuto11-amd64 uses /acestream/acestreamengine, Jopsis variants use python
            if variant == 'krinkuto11-amd64':
                assert '/acestream/acestreamengine' in config['base_cmd'], f"Missing /acestream/acestreamengine in base_cmd for {variant}"
            else:
                assert 'python' in config['base_cmd'], f"Missing python in base_cmd for {variant}"
                assert '--bind-all' in config['base_cmd'], f"Missing --bind-all in base_cmd for {variant}"
                assert '--disable-upnp' in config['base_cmd'], f"Missing --disable-upnp in base_cmd for {variant}"
            print(f"   ✓ CMD-based variant with {len(config['base_cmd'])} args")
    
    print(f"\n✅ All {len(variants)} variants configured correctly!")
    return True


@unittest.mock.patch('app.services.custom_variant_config.detect_platform', return_value='amd64')
@unittest.mock.patch('app.services.custom_variant_config.is_custom_variant_enabled', return_value=False)
def test_variant_environment_building(mock_is_custom, mock_detect):
    """Test that environment variables are built correctly for each variant."""
    print("\n🧪 Testing Environment Variable Building")
    print("=" * 60)
    
    from app.services.provisioner import _get_variant_config
    
    # Test ports
    c_http = 6879
    c_https = 6880
    
    # Test krinkuto11-amd64 (CMD-based)
    print("\n📋 Testing krinkuto11-amd64 command:")
    config = _get_variant_config('krinkuto11-amd64')
    cmd = None
    if config['config_type'] == 'cmd':
        base_cmd = config.get('base_cmd', [])
        port_args = ["--http-port", str(c_http), "--https-port", str(c_https)]
        cmd = base_cmd + port_args
    print(f"   Command: {' '.join(cmd[:5])}... (total {len(cmd)} args)")
    assert '/acestream/acestreamengine' in cmd, "Missing /acestream/acestreamengine in cmd"
    assert '--http-port' in cmd, "Missing http-port in cmd"
    assert '6879' in cmd, "Missing port value in cmd"
    print("   ✓ Command built correctly with base + ports")
    
    # Test jopsis-amd64 (Now CMD-based)
    print("\n📋 Testing jopsis-amd64 command:")
    config = _get_variant_config('jopsis-amd64')
    cmd = None
    if config['config_type'] == 'cmd':
        base_cmd = config.get('base_cmd', [])
        # In default mode, Jopsis only gets --http-port
        port_args = ["--http-port", str(c_http)]
        cmd = base_cmd + port_args
    print(f"   Command: {' '.join(cmd[:5])}... (total {len(cmd)} args)")
    assert 'python' in cmd, "Missing python in cmd"
    assert '--http-port' in cmd, "Missing http-port in cmd"
    assert '6879' in cmd, "Missing port value in cmd"
    assert '--disable-upnp' in cmd, "Missing base args in cmd"
    assert '--https-port' not in cmd, "Jopsis default should NOT have https-port"
    print("   ✓ Command built correctly with base settings + minimal ports")
    
    # Test jopsis-arm32 (CMD-based)
    print("\n📋 Testing jopsis-arm32 command:")
    config = _get_variant_config('jopsis-arm32')
    cmd = None
    if config['config_type'] == 'cmd':
        base_cmd = config.get('base_cmd', [])
        port_args = ["--http-port", str(c_http), "--https-port", str(c_https)]
        cmd = base_cmd + port_args
    print(f"   Command: {' '.join(cmd[:5])}... (total {len(cmd)} args)")
    assert 'python' in cmd, "Missing python in cmd"
    assert '--http-port' in cmd, "Missing http-port in cmd"
    assert '6879' in cmd, "Missing port value in cmd"
    print("   ✓ Command built correctly with base + ports")
    
    # Test jopsis-arm64 (CMD-based)
    print("\n📋 Testing jopsis-arm64 command:")
    config = _get_variant_config('jopsis-arm64')
    cmd = None
    if config['config_type'] == 'cmd':
        base_cmd = config.get('base_cmd', [])
        port_args = ["--http-port", str(c_http), "--https-port", str(c_https)]
        cmd = base_cmd + port_args
    print(f"   Command: {' '.join(cmd[:5])}... (total {len(cmd)} args)")
    assert '--live-cache-type' in cmd, "Missing base args in cmd"
    assert '--http-port' in cmd, "Missing http-port in cmd"
    print("   ✓ Command built correctly with base + ports")
    
    print("\n✅ All environment/command building tests passed!")
    return True


@unittest.mock.patch('app.services.custom_variant_config.detect_platform', return_value='amd64')
@unittest.mock.patch('app.services.custom_variant_config.is_custom_variant_enabled', return_value=False)
def test_config_loading(mock_is_custom, mock_detect):
    """Test that ENGINE_VARIANT config loads correctly."""
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
    assert cfg.ENGINE_VARIANT == 'krinkuto11-amd64', "Default should be krinkuto11-amd64"
    print("   ✓ Default value correct")
    
    # Test each valid variant
    for variant in ['krinkuto11-amd64', 'jopsis-amd64', 'jopsis-arm32', 'jopsis-arm64']:
        if 'app.core.config' in sys.modules:
            del sys.modules['app.core.config']
        os.environ['ENGINE_VARIANT'] = variant
        from app.core.config import Cfg
        cfg = Cfg()
        print(f"\n📋 Testing ENGINE_VARIANT={variant}")
        assert cfg.ENGINE_VARIANT == variant, f"Should load {variant}"
        print(f"   ✓ Loaded correctly")
    
    print("\n✅ Configuration loading tests passed!")
    return True


if __name__ == "__main__":
    import sys
    sys.path.append('.')
    
    success = True
    try:
        success = test_variant_configs() and success
        success = test_variant_environment_building() and success
        success = test_config_loading() and success
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
