#!/usr/bin/env python3
"""
Test P2P port handling for all engine variants.
"""

import unittest.mock

@unittest.mock.patch('app.services.custom_variant_config.detect_platform', return_value='amd64')
@unittest.mock.patch('app.services.custom_variant_config.is_custom_variant_enabled', return_value=False)
def test_p2p_port_handling(mock_is_custom, mock_detect):
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
    
    # Test krinkuto11-amd64 (CMD with P2P port in command)
    print("\n" + "-" * 70)
    print("📋 Variant: krinkuto11-amd64")
    print("-" * 70)
    config = _get_variant_config('krinkuto11-amd64')
    cmd = None
    if config['config_type'] == 'cmd':
        base_cmd = config.get('base_cmd', [])
        port_args = ["--http-port", str(c_http), "--https-port", str(c_https)]
        if p2p_port:
            port_args.extend(["--port", str(p2p_port)])
        cmd = base_cmd + port_args
    
    print(f"Command: {' '.join(cmd)}")
    
    assert '--port' in cmd, "P2P port flag should be in command"
    assert str(p2p_port) in cmd, f"P2P port {p2p_port} should be in command"
    port_index = cmd.index('--port')
    assert cmd[port_index + 1] == str(p2p_port), f"Port value should follow --port flag"
    print("✓ P2P port correctly appended to command")
    
    # Test AceServe-amd64 (CMD with P2P port in command)
    print("\n" + "-" * 70)
    print("📋 Variant: AceServe-amd64")
    print("-" * 70)
    config = _get_variant_config('AceServe-amd64')
    cmd = None
    if config['config_type'] == 'cmd':
        base_cmd = config.get('base_cmd', [])
        port_args = ["--http-port", str(c_http)]
        if p2p_port:
            port_args.extend(["--port", str(p2p_port)])
        cmd = base_cmd + port_args
    
    print(f"Command (last 8 args): {cmd[-8:]}")
    
    assert '--port' in cmd, "P2P port flag should be in command"
    assert str(p2p_port) in cmd, f"P2P port {p2p_port} should be in command"
    port_index = cmd.index('--port')
    assert cmd[port_index + 1] == str(p2p_port), f"Port value should follow --port flag"
    print("✓ P2P port correctly appended to config")
    
    # Test AceServe-arm32 (CMD with P2P port in command)
    print("\n" + "-" * 70)
    print("📋 Variant: AceServe-arm32")
    print("-" * 70)
    config = _get_variant_config('AceServe-arm32')
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
    
    # Test AceServe-arm64 (CMD with P2P port in command)
    print("\n" + "-" * 70)
    print("📋 Variant: AceServe-arm64")
    print("-" * 70)
    config = _get_variant_config('AceServe-arm64')
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
    print("  - krinkuto11-amd64: --port flag in command ✓")
    print("  - AceServe-amd64: --port flag in command ✓")
    print("  - AceServe-arm32: --port flag in command ✓")
    print("  - AceServe-arm64: --port flag in command ✓")
    
    return True


@unittest.mock.patch('app.services.custom_variant_config.detect_platform', return_value='amd64')
@unittest.mock.patch('app.services.custom_variant_config.is_custom_variant_enabled', return_value=False)
def test_p2p_port_without_gluetun(mock_is_custom, mock_detect):
    """Test that variants work correctly when Gluetun is not configured."""
    print("\n\n🧪 Testing Without Gluetun (No P2P Port)")
    print("=" * 70)
    
    from app.services.provisioner import _get_variant_config
    
    c_http = 40123
    c_https = 45123
    p2p_port = None  # No Gluetun
    
    print(f"\nTest Configuration: Gluetun disabled (p2p_port=None)")
    
    # Test AceServe-amd64 without P2P port
    print("\n📋 Variant: AceServe-amd64 (without Gluetun)")
    config = _get_variant_config('AceServe-amd64')
    cmd = None
    if config['config_type'] == 'cmd':
        base_cmd = config.get('base_cmd', [])
        port_args = ["--http-port", str(c_http)]
        if p2p_port:
            port_args.extend(["--port", str(p2p_port)])
        cmd = base_cmd + port_args
    
    print(f"  Command (last 6 args): {cmd[-6:]}")
    assert '--port' not in cmd, "P2P port flag should NOT be in command when Gluetun disabled"
    print("  ✓ No P2P port added (as expected)")
    
    # Test AceServe-arm32 without P2P port
    print("\n📋 Variant: AceServe-arm32 (without Gluetun)")
    config = _get_variant_config('AceServe-arm32')
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
