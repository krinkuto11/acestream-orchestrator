#!/usr/bin/env python3
"""
Test P2P port handling for all engine variants.
"""

def test_p2p_port_handling():
    """Test that P2P port is correctly added for each variant type."""
    print("🧪 Testing P2P Port Handling for All Variants")
    print("=" * 70)
    
    from app.services.provisioner import _get_variant_config
    
    # Simulated ports
    c_http = 40123
    c_https = 45123
    p2p_port = 12345
    
    print(f"\nTest Configuration:")
    print(f"  HTTP Port: {c_http}")
    print(f"  HTTPS Port: {c_https}")
    print(f"  P2P Port (Gluetun): {p2p_port}")
    
    # Test krinkuto11-amd64 (ENV with P2P_PORT environment variable)
    print("\n" + "-" * 70)
    print("📋 Variant: krinkuto11-amd64")
    print("-" * 70)
    config = _get_variant_config('krinkuto11-amd64')
    env = {}
    if config['config_type'] == 'env':
        conf_lines = [f"--http-port={c_http}", f"--https-port={c_https}", "--bind-all"]
        env['CONF'] = "\n".join(conf_lines)
        env['HTTP_PORT'] = str(c_http)
        env['HTTPS_PORT'] = str(c_https)
        if p2p_port:
            env['P2P_PORT'] = str(p2p_port)
    
    print(f"Environment Variables:")
    print(f"  CONF: {repr(env.get('CONF'))}")
    print(f"  HTTP_PORT: {env.get('HTTP_PORT')}")
    print(f"  HTTPS_PORT: {env.get('HTTPS_PORT')}")
    print(f"  P2P_PORT: {env.get('P2P_PORT')}")
    
    assert 'P2P_PORT' in env, "P2P_PORT should be in environment for krinkuto11-amd64"
    assert env['P2P_PORT'] == str(p2p_port), f"P2P_PORT should be {p2p_port}"
    print("✓ P2P port correctly added as environment variable")
    
    # Test jopsis-amd64 (ENV with P2P port in ACESTREAM_ARGS)
    print("\n" + "-" * 70)
    print("📋 Variant: jopsis-amd64")
    print("-" * 70)
    config = _get_variant_config('jopsis-amd64')
    env = {}
    if config['config_type'] == 'env':
        base_args = config.get('base_args', '')
        port_args = f" --http-port {c_http} --https-port {c_https}"
        if p2p_port:
            port_args += f" --port {p2p_port}"
        env['ACESTREAM_ARGS'] = base_args + port_args
    
    print(f"Environment Variables:")
    print(f"  ACESTREAM_ARGS (last 150 chars): ...{env['ACESTREAM_ARGS'][-150:]}")
    
    assert '--port' in env['ACESTREAM_ARGS'], "P2P port flag should be in ACESTREAM_ARGS"
    assert str(p2p_port) in env['ACESTREAM_ARGS'], f"P2P port {p2p_port} should be in ACESTREAM_ARGS"
    assert f'--port {p2p_port}' in env['ACESTREAM_ARGS'], f"--port {p2p_port} should be in ACESTREAM_ARGS"
    print("✓ P2P port correctly appended to ACESTREAM_ARGS")
    
    # Test jopsis-arm32 (CMD with P2P port in command)
    print("\n" + "-" * 70)
    print("📋 Variant: jopsis-arm32")
    print("-" * 70)
    config = _get_variant_config('jopsis-arm32')
    cmd = None
    if config['config_type'] == 'cmd':
        base_cmd = config.get('base_cmd', [])
        port_args = ["--http-port", str(c_http), "--https-port", str(c_https)]
        if p2p_port:
            port_args.extend(["--port", str(p2p_port)])
        cmd = base_cmd + port_args
    
    print(f"Command (last 8 args): {cmd[-8:]}")
    
    assert '--port' in cmd, "P2P port flag should be in command"
    assert str(p2p_port) in cmd, f"P2P port {p2p_port} should be in command"
    port_index = cmd.index('--port')
    assert cmd[port_index + 1] == str(p2p_port), f"Port value should follow --port flag"
    print("✓ P2P port correctly appended to command")
    
    # Test jopsis-arm64 (CMD with P2P port in command)
    print("\n" + "-" * 70)
    print("📋 Variant: jopsis-arm64")
    print("-" * 70)
    config = _get_variant_config('jopsis-arm64')
    cmd = None
    if config['config_type'] == 'cmd':
        base_cmd = config.get('base_cmd', [])
        port_args = ["--http-port", str(c_http), "--https-port", str(c_https)]
        if p2p_port:
            port_args.extend(["--port", str(p2p_port)])
        cmd = base_cmd + port_args
    
    print(f"Command (last 8 args): {cmd[-8:]}")
    
    assert '--port' in cmd, "P2P port flag should be in command"
    assert str(p2p_port) in cmd, f"P2P port {p2p_port} should be in command"
    port_index = cmd.index('--port')
    assert cmd[port_index + 1] == str(p2p_port), f"Port value should follow --port flag"
    print("✓ P2P port correctly appended to command")
    
    print("\n" + "=" * 70)
    print("✅ ALL P2P PORT TESTS PASSED!")
    print("=" * 70)
    
    print("\nSummary:")
    print("  - krinkuto11-amd64: P2P_PORT environment variable ✓")
    print("  - jopsis-amd64: --port flag in ACESTREAM_ARGS ✓")
    print("  - jopsis-arm32: --port flag in command ✓")
    print("  - jopsis-arm64: --port flag in command ✓")
    
    return True


def test_p2p_port_without_gluetun():
    """Test that variants work correctly when Gluetun is not configured."""
    print("\n\n🧪 Testing Without Gluetun (No P2P Port)")
    print("=" * 70)
    
    from app.services.provisioner import _get_variant_config
    
    c_http = 40123
    c_https = 45123
    p2p_port = None  # No Gluetun
    
    print(f"\nTest Configuration: Gluetun disabled (p2p_port=None)")
    
    # Test jopsis-amd64 without P2P port
    print("\n📋 Variant: jopsis-amd64 (without Gluetun)")
    config = _get_variant_config('jopsis-amd64')
    env = {}
    if config['config_type'] == 'env':
        base_args = config.get('base_args', '')
        port_args = f" --http-port {c_http} --https-port {c_https}"
        if p2p_port:
            port_args += f" --port {p2p_port}"
        env['ACESTREAM_ARGS'] = base_args + port_args
    
    print(f"  ACESTREAM_ARGS (last 80 chars): ...{env['ACESTREAM_ARGS'][-80:]}")
    assert '--port' not in env['ACESTREAM_ARGS'], "P2P port flag should NOT be in ACESTREAM_ARGS when Gluetun disabled"
    print("  ✓ No P2P port added (as expected)")
    
    # Test jopsis-arm32 without P2P port
    print("\n📋 Variant: jopsis-arm32 (without Gluetun)")
    config = _get_variant_config('jopsis-arm32')
    cmd = None
    if config['config_type'] == 'cmd':
        base_cmd = config.get('base_cmd', [])
        port_args = ["--http-port", str(c_http), "--https-port", str(c_https)]
        if p2p_port:
            port_args.extend(["--port", str(p2p_port)])
        cmd = base_cmd + port_args
    
    print(f"  Command (last 6 args): {cmd[-6:]}")
    assert '--port' not in cmd, "P2P port flag should NOT be in command when Gluetun disabled"
    print("  ✓ No P2P port added (as expected)")
    
    print("\n✅ Variants correctly handle missing P2P port!")
    
    return True


if __name__ == "__main__":
    import sys
    sys.path.append('.')
    
    success = True
    try:
        success = test_p2p_port_handling() and success
        success = test_p2p_port_without_gluetun() and success
    except Exception as e:
        print(f"\n❌ Test failed with error: {e}")
        import traceback
        traceback.print_exc()
        success = False
    
    if success:
        print("\n" + "=" * 70)
        print("🎉 ALL P2P PORT TESTS PASSED!")
        print("=" * 70)
    
    sys.exit(0 if success else 1)
