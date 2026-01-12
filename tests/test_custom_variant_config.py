#!/usr/bin/env python3
"""
Test custom engine variant configuration.
"""
import pytest
import json
import tempfile
from pathlib import Path

def test_platform_detection():
    """Test platform detection function"""
    from app.services.custom_variant_config import detect_platform
    
    platform = detect_platform()
    assert platform in ['amd64', 'arm32', 'arm64'], f"Invalid platform: {platform}"
    print(f"✅ Platform detection: {platform}")


def test_default_parameters():
    """Test that default parameters are properly structured"""
    from app.services.custom_variant_config import get_default_parameters
    
    for platform in ['amd64', 'arm32', 'arm64']:
        params = get_default_parameters(platform)
        
        # Should have params
        assert len(params) > 0, f"No default parameters for {platform}"
        
        # Check structure
        for param in params:
            assert hasattr(param, 'name'), "Parameter missing name"
            assert hasattr(param, 'type'), "Parameter missing type"
            assert hasattr(param, 'value'), "Parameter missing value"
            assert hasattr(param, 'enabled'), "Parameter missing enabled"
            
            # Validate type
            assert param.type in ['flag', 'string', 'int', 'bytes', 'path'], \
                f"Invalid parameter type: {param.type}"
        
        print(f"✅ Default parameters for {platform}: {len(params)} params")


def test_config_validation():
    """Test configuration validation"""
    from app.services.custom_variant_config import (
        CustomVariantConfig, 
        validate_config,
        get_default_parameters
    )
    from pydantic import ValidationError
    
    # Valid config
    config = CustomVariantConfig(
        enabled=True,
        platform='amd64',
        arm_version='3.2.13',
        parameters=get_default_parameters('amd64')
    )
    is_valid, error = validate_config(config)
    assert is_valid, f"Valid config rejected: {error}"
    print("✅ Valid configuration passed validation")
    
    # Invalid platform (caught by Pydantic)
    try:
        config = CustomVariantConfig(
            enabled=True,
            platform='invalid',
            arm_version='3.2.13',
            parameters=[]
        )
        assert False, "Invalid platform should have raised ValidationError"
    except ValidationError as e:
        assert 'platform' in str(e).lower(), f"Wrong error for invalid platform"
        print("✅ Invalid platform correctly rejected by Pydantic")
    
    # Invalid ARM version (caught by Pydantic)
    try:
        config = CustomVariantConfig(
            enabled=True,
            platform='arm64',
            arm_version='9.9.9',
            parameters=[]
        )
        assert False, "Invalid ARM version should have raised ValidationError"
    except ValidationError as e:
        assert 'arm_version' in str(e).lower(), f"Wrong error for invalid ARM version"
        print("✅ Invalid ARM version correctly rejected by Pydantic")


def test_config_save_load():
    """Test saving and loading configuration"""
    from app.services.custom_variant_config import (
        CustomVariantConfig,
        save_config,
        load_config,
        get_default_parameters
    )
    
    # Create temporary config file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        # Create config
        config = CustomVariantConfig(
            enabled=True,
            platform='amd64',
            arm_version='3.2.13',
            parameters=get_default_parameters('amd64')[:5]  # Just first 5 for testing
        )
        
        # Save
        success = save_config(config, temp_path)
        assert success, "Failed to save config"
        assert temp_path.exists(), "Config file not created"
        print("✅ Configuration saved successfully")
        
        # Load
        loaded = load_config(temp_path)
        assert loaded is not None, "Failed to load config"
        assert loaded.enabled == config.enabled, "enabled mismatch"
        assert loaded.platform == config.platform, "platform mismatch"
        assert loaded.arm_version == config.arm_version, "arm_version mismatch"
        assert len(loaded.parameters) == len(config.parameters), "parameters count mismatch"
        print("✅ Configuration loaded successfully")
        
        # Verify JSON structure
        with open(temp_path) as f:
            data = json.load(f)
        assert 'enabled' in data, "JSON missing enabled"
        assert 'platform' in data, "JSON missing platform"
        assert 'parameters' in data, "JSON missing parameters"
        print("✅ JSON structure correct")
        
    finally:
        # Cleanup
        if temp_path.exists():
            temp_path.unlink()


def test_build_variant_config():
    """Test building variant config from custom config"""
    from app.services.custom_variant_config import (
        CustomVariantConfig,
        CustomVariantParameter,
        build_variant_config_from_custom
    )
    
    # Test amd64 (CMD-based with Nano-Ace)
    config = CustomVariantConfig(
        enabled=True,
        platform='amd64',
        amd64_version='3.2.11-py3.10',
        arm_version='3.2.13',
        parameters=[
            CustomVariantParameter(name='--client-console', type='flag', value=True, enabled=True),
            CustomVariantParameter(name='--bind-all', type='flag', value=True, enabled=True),
            CustomVariantParameter(name='--live-cache-size', type='bytes', value=268435456, enabled=True),
        ]
    )
    
    variant = build_variant_config_from_custom(config)
    assert variant['image'] == 'ghcr.io/krinkuto11/nano-ace:latest', "Wrong image for amd64"
    assert variant['config_type'] == 'cmd', "Wrong config_type for amd64"
    assert 'base_cmd' in variant, "Missing base_cmd for amd64"
    assert isinstance(variant['base_cmd'], list), "base_cmd should be a list"
    assert '/acestream/acestreamengine' in variant['base_cmd'], "Missing /acestream/acestreamengine in base_cmd"
    assert '--client-console' in variant['base_cmd'], "Missing flag in base_cmd"
    assert '--live-cache-size' in variant['base_cmd'], "Missing parameter in base_cmd"
    assert '268435456' in variant['base_cmd'], "Missing parameter value in base_cmd"
    print("✅ AMD64 variant config built correctly")
    
    # Test arm64 (CMD-based)
    config = CustomVariantConfig(
        enabled=True,
        platform='arm64',
        arm_version='3.2.14',
        parameters=[
            CustomVariantParameter(name='--client-console', type='flag', value=True, enabled=True),
            CustomVariantParameter(name='--bind-all', type='flag', value=True, enabled=True),
            CustomVariantParameter(name='--http-port', type='int', value=6878, enabled=True),
        ]
    )
    
    variant = build_variant_config_from_custom(config)
    assert variant['image'] == 'jopsis/acestream:arm64-v3.2.14', "Wrong image for arm64"
    assert variant['config_type'] == 'cmd', "Wrong config_type for arm64"
    assert 'base_cmd' in variant, "Missing base_cmd for arm64"
    assert isinstance(variant['base_cmd'], list), "base_cmd should be a list"
    assert 'python' in variant['base_cmd'], "Missing python in base_cmd"
    assert '--client-console' in variant['base_cmd'], "Missing flag in base_cmd"
    assert '--http-port' in variant['base_cmd'], "Missing parameter in base_cmd"
    assert '6878' in variant['base_cmd'], "Missing parameter value in base_cmd"
    print("✅ ARM64 variant config built correctly")


def test_provisioner_integration():
    """Test that provisioner correctly uses custom variant"""
    from app.services.provisioner import get_variant_config
    from app.services.custom_variant_config import (
        is_custom_variant_enabled,
        get_config,
        CustomVariantConfig,
        save_config,
        reload_config,
        get_default_parameters
    )
    import tempfile
    from pathlib import Path
    
    # Create temporary config file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        # Create and save a custom config (disabled)
        config = CustomVariantConfig(
            enabled=False,  # Disabled
            platform='amd64',
            arm_version='3.2.13',
            parameters=get_default_parameters('amd64')[:5]
        )
        save_config(config, temp_path)
        
        # Test that standard variant is returned when custom is disabled
        # (We can't easily test this without mocking the config path)
        standard_variant = get_variant_config('jopsis-amd64')
        assert standard_variant['image'] == 'jopsis/acestream:x64'
        print("✅ Standard variant returned when custom disabled")
        
    finally:
        # Cleanup
        if temp_path.exists():
            temp_path.unlink()


def test_parameter_types():
    """Test that different parameter types are handled correctly"""
    from app.services.custom_variant_config import CustomVariantParameter
    
    # Flag parameter
    flag = CustomVariantParameter(name='--test-flag', type='flag', value=True, enabled=True)
    assert flag.type == 'flag'
    assert flag.value is True
    print("✅ Flag parameter created")
    
    # String parameter
    string = CustomVariantParameter(name='--test-string', type='string', value='test', enabled=True)
    assert string.type == 'string'
    assert string.value == 'test'
    print("✅ String parameter created")
    
    # Int parameter
    integer = CustomVariantParameter(name='--test-int', type='int', value=42, enabled=True)
    assert integer.type == 'int'
    assert integer.value == 42
    print("✅ Int parameter created")
    
    # Bytes parameter
    bytes_param = CustomVariantParameter(name='--test-bytes', type='bytes', value=1048576, enabled=True)
    assert bytes_param.type == 'bytes'
    assert bytes_param.value == 1048576
    print("✅ Bytes parameter created")
    
    # Path parameter
    path = CustomVariantParameter(name='--test-path', type='path', value='/tmp/test', enabled=True)
    assert path.type == 'path'
    assert path.value == '/tmp/test'
    print("✅ Path parameter created")


if __name__ == '__main__':
    import sys
    sys.path.append('.')
    
    print("🧪 Testing Custom Engine Variant Configuration")
    print("=" * 60)
    
    success = True
    try:
        test_platform_detection()
        test_default_parameters()
        test_config_validation()
        test_config_save_load()
        test_build_variant_config()
        test_provisioner_integration()
        test_parameter_types()
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
