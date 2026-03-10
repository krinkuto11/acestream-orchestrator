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


def test_config_instantiation():
    """Test configuration validation defaults"""
    from app.services.custom_variant_config import CustomVariantConfig
    from pydantic import ValidationError
    
    # Valid config
    config = CustomVariantConfig()
    
    assert config.enabled is False
    assert config.p2p_port is None
    assert config.live_cache_type == "disk"
    print("✅ Valid configuration passed validation")
    
    # Invalid cache type
    try:
        config = CustomVariantConfig(live_cache_type="invalid")
        assert False, "Invalid cache should have raised ValidationError"
    except ValidationError:
        print("✅ Invalid setting correctly rejected by Pydantic")


def test_config_save_load():
    """Test saving and loading configuration"""
    from app.services.custom_variant_config import (
        CustomVariantConfig,
        save_config,
        load_config
    )
    
    # Create temporary config file
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as f:
        temp_path = Path(f.name)
    
    try:
        config = CustomVariantConfig(
            enabled=True,
            name="Test Engine",
            p2p_port=9000
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
        assert loaded.p2p_port == config.p2p_port, "p2p_port mismatch"
        print("✅ Configuration loaded successfully")
        
        # Verify JSON structure
        with open(temp_path) as f:
            data = json.load(f)
        assert 'enabled' in data, "JSON missing enabled"
        assert 'name' in data, "JSON missing name"
        assert 'p2p_port' in data, "JSON missing p2p_port"
        print("✅ JSON structure correct")
        
    finally:
        # Cleanup
        if temp_path.exists():
            temp_path.unlink()


def test_build_variant_config():
    """Test building variant config from custom config"""
    from app.services.custom_variant_config import (
        CustomVariantConfig,
        build_variant_config_from_custom
    )
    import unittest.mock
    
    with unittest.mock.patch('app.services.custom_variant_config.detect_platform', return_value='arm64'):
        config = CustomVariantConfig(
            enabled=True,
            p2p_port=1234,
            download_limit=1000,
            upload_limit=500,
            live_cache_type="disk",
            buffer_time=10,
            stats_interval=5
        )
        
        variant = build_variant_config_from_custom(config)
        
        assert variant['config_type'] == 'cmd'
        assert 'python' in variant['base_cmd']
        assert '--port' in variant['base_cmd']
        assert '1234' in variant['base_cmd']
        assert '--download-limit' in variant['base_cmd']
        assert '1000' in variant['base_cmd']
        assert '--upload-limit' in variant['base_cmd']
        assert '500' in variant['base_cmd']
        assert '--live-cache-type' in variant['base_cmd']
        assert 'disk' in variant['base_cmd']
        assert '--cache-dir' in variant['base_cmd']
        
        print("✅ Custom variant config built correctly")


if __name__ == '__main__':
    import sys
    sys.path.append('.')
    
    test_platform_detection()
    test_config_instantiation()
    test_config_save_load()
    test_build_variant_config()
    print("🎉 ALL TESTS PASSED!")
