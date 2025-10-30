#!/usr/bin/env python3
"""
Test AceStream engine variants configuration.
"""

def test_variant_configs():
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
            if variant == 'jopsis-amd64':
                assert 'base_args' in config, f"Missing 'base_args' for {variant}"
                assert '--client-console' in config['base_args'], f"Missing required args for {variant}"
                print(f"   ✓ Has base_args with required settings")
            else:
                print(f"   ✓ ENV-based variant (uses CONF)")
        else:
            assert 'base_cmd' in config, f"Missing 'base_cmd' for {variant}"
            assert isinstance(config['base_cmd'], list), f"base_cmd should be a list for {variant}"
            assert 'python' in config['base_cmd'], f"Missing python in base_cmd for {variant}"
            print(f"   ✓ CMD-based variant with {len(config['base_cmd'])} args")
    
    print(f"\n✅ All {len(variants)} variants configured correctly!")
    return True


def test_variant_environment_building():
    """Test that environment variables are built correctly for each variant."""
    print("\n🧪 Testing Environment Variable Building")
    print("=" * 60)
    
    from app.services.provisioner import _get_variant_config
    
    # Test ports
    c_http = 6879
    c_https = 6880
    
    # Test krinkuto11-amd64 (ENV with CONF)
    print("\n📋 Testing krinkuto11-amd64 environment:")
    config = _get_variant_config('krinkuto11-amd64')
    env = {}
    if config['config_type'] == 'env':
        conf_lines = [f"--http-port={c_http}", f"--https-port={c_https}", "--bind-all"]
        env['CONF'] = "\n".join(conf_lines)
        env['HTTP_PORT'] = str(c_http)
        env['HTTPS_PORT'] = str(c_https)
    print(f"   CONF: {repr(env.get('CONF'))}")
    print(f"   HTTP_PORT: {env.get('HTTP_PORT')}")
    assert '--http-port=6879' in env['CONF'], "Missing http-port in CONF"
    print("   ✓ CONF built correctly")
    
    # Test jopsis-amd64 (ENV with ACESTREAM_ARGS)
    print("\n📋 Testing jopsis-amd64 environment:")
    config = _get_variant_config('jopsis-amd64')
    env = {}
    if config['config_type'] == 'env':
        base_args = config.get('base_args', '')
        port_args = f" --http-port {c_http} --https-port {c_https}"
        env['ACESTREAM_ARGS'] = base_args + port_args
    print(f"   ACESTREAM_ARGS length: {len(env['ACESTREAM_ARGS'])} chars")
    assert '--http-port 6879' in env['ACESTREAM_ARGS'], "Missing http-port in ACESTREAM_ARGS"
    assert '--client-console' in env['ACESTREAM_ARGS'], "Missing base args in ACESTREAM_ARGS"
    print("   ✓ ACESTREAM_ARGS built correctly with base settings + ports")
    
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


def test_config_loading():
    """Test that ENGINE_VARIANT config loads correctly."""
    print("\n🧪 Testing Configuration Loading")
    print("=" * 60)
    
    import os
    import sys
    
    # Test default value
    if 'app.core.config' in sys.modules:
        del sys.modules['app.core.config']
    os.environ.pop('ENGINE_VARIANT', None)
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
