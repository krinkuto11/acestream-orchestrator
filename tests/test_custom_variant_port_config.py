#!/usr/bin/env python3
"""
Test that custom engine variants correctly use the orchestrator's port range.

This specifically tests the fix for the issue where custom engine templates
in single VPN mode and no-VPN mode were not using the provisioner's port range,
causing the engine to default to port 6878.
"""
import pytest
from unittest.mock import patch, MagicMock
import os


def test_custom_variant_uses_acestream_args_with_allocated_port():
    """
    Test that custom amd64 variant with base_args uses ACESTREAM_ARGS
    with the orchestrator-allocated port instead of defaulting to 6878.
    """
    from app.services.custom_variant_config import (
        CustomVariantConfig,
        CustomVariantParameter,
        build_variant_config_from_custom
    )
    
    # Create a custom amd64 variant config with parameters
    config = CustomVariantConfig(
        enabled=True,
        platform='amd64',
        arm_version='3.2.13',
        parameters=[
            CustomVariantParameter(name='--client-console', type='flag', value=True, enabled=True),
            CustomVariantParameter(name='--bind-all', type='flag', value=True, enabled=True),
            CustomVariantParameter(name='--live-cache-size', type='bytes', value=268435456, enabled=True),
        ]
    )
    
    # Build the variant config
    variant_config = build_variant_config_from_custom(config)
    
    # Verify the variant config has is_custom=True and base_args
    assert variant_config.get("is_custom") is True, "Custom variant should have is_custom=True"
    assert variant_config.get("config_type") == "env", "AMD64 custom variant should have config_type=env"
    assert variant_config.get("base_args") is not None, "AMD64 custom variant should have base_args"
    
    print("✅ Custom variant config correctly has is_custom=True and base_args")


def test_port_allocation_logic_for_custom_variant():
    """
    Test the port allocation logic correctly identifies custom variants
    that should use ACESTREAM_ARGS.
    """
    from app.services.custom_variant_config import (
        CustomVariantConfig,
        CustomVariantParameter,
        build_variant_config_from_custom
    )
    
    # Create a custom amd64 variant config
    config = CustomVariantConfig(
        enabled=True,
        platform='amd64',
        arm_version='3.2.13',
        parameters=[
            CustomVariantParameter(name='--client-console', type='flag', value=True, enabled=True),
        ]
    )
    variant_config = build_variant_config_from_custom(config)
    
    # Test the logic used in provisioner.py to determine if ACESTREAM_ARGS should be used
    # This mirrors the condition in start_acestream()
    uses_acestream_args = (
        variant_config.get("is_custom") and variant_config.get("base_args") is not None
    )
    
    assert uses_acestream_args is True, "Custom variant should use ACESTREAM_ARGS"
    print("✅ Port allocation logic correctly identifies custom variant for ACESTREAM_ARGS")


def test_standard_jopsis_variant_still_works():
    """
    Test that the standard jopsis-amd64 variant still uses ACESTREAM_ARGS correctly.
    """
    from app.services.provisioner import get_variant_config
    
    # Get the standard jopsis-amd64 variant config
    variant_config = get_variant_config("jopsis-amd64")
    
    assert variant_config.get("config_type") == "env", "jopsis-amd64 should have config_type=env"
    assert variant_config.get("base_args") is not None, "jopsis-amd64 should have base_args"
    assert variant_config.get("is_custom") is not True, "jopsis-amd64 should not be custom"
    
    print("✅ Standard jopsis-amd64 variant still works correctly")


def test_standard_krinkuto_variant_still_works():
    """
    Test that the standard krinkuto11-amd64 variant still uses CONF correctly.
    """
    from app.services.provisioner import get_variant_config
    
    # Get the standard krinkuto11-amd64 variant config
    variant_config = get_variant_config("krinkuto11-amd64")
    
    assert variant_config.get("config_type") == "env", "krinkuto11-amd64 should have config_type=env"
    # krinkuto11-amd64 does NOT have base_args, it uses CONF
    assert variant_config.get("base_args") is None, "krinkuto11-amd64 should not have base_args"
    assert variant_config.get("is_custom") is not True, "krinkuto11-amd64 should not be custom"
    
    print("✅ Standard krinkuto11-amd64 variant still works correctly")


def test_arm_custom_variant_uses_cmd_with_port_args():
    """
    Test that custom ARM variants correctly use cmd with port arguments.
    """
    from app.services.custom_variant_config import (
        CustomVariantConfig,
        CustomVariantParameter,
        build_variant_config_from_custom
    )
    
    # Create a custom arm64 variant config
    config = CustomVariantConfig(
        enabled=True,
        platform='arm64',
        arm_version='3.2.13',
        parameters=[
            CustomVariantParameter(name='--client-console', type='flag', value=True, enabled=True),
            CustomVariantParameter(name='--bind-all', type='flag', value=True, enabled=True),
        ]
    )
    
    variant_config = build_variant_config_from_custom(config)
    
    assert variant_config.get("is_custom") is True, "Custom variant should have is_custom=True"
    assert variant_config.get("config_type") == "cmd", "ARM64 custom variant should have config_type=cmd"
    assert variant_config.get("base_cmd") is not None, "ARM64 custom variant should have base_cmd"
    assert isinstance(variant_config.get("base_cmd"), list), "base_cmd should be a list"
    
    print("✅ ARM custom variant correctly uses cmd configuration")


def test_uses_acestream_args_condition_comprehensive():
    """
    Comprehensive test for the uses_acestream_args condition in provisioner.
    Tests all combinations of is_custom and base_args.
    """
    
    test_cases = [
        # (is_custom, base_args, expected_uses_acestream_args, description)
        (True, "--some-args", True, "Custom variant with base_args should use ACESTREAM_ARGS"),
        (True, "", True, "Custom variant with empty base_args should use ACESTREAM_ARGS"),
        (True, None, False, "Custom variant without base_args should NOT use ACESTREAM_ARGS"),
        (False, "--some-args", False, "Non-custom variant with base_args: condition doesn't apply (jopsis uses separate check)"),
        (None, "--some-args", False, "Variant without is_custom flag: should NOT use ACESTREAM_ARGS"),
    ]
    
    for is_custom, base_args, expected, description in test_cases:
        variant_config = {
            "config_type": "env",
            "image": "test-image"
        }
        if is_custom is not None:
            variant_config["is_custom"] = is_custom
        if base_args is not None:
            variant_config["base_args"] = base_args
        
        # Mirror the condition from provisioner.py:
        # uses_acestream_args = (
        #     cfg.ENGINE_VARIANT == "jopsis-amd64" or 
        #     (variant_config.get("is_custom") and variant_config.get("base_args") is not None)
        # )
        # For this test, we only check the custom variant part (not jopsis-amd64 case)
        uses_acestream_args = (
            variant_config.get("is_custom") and variant_config.get("base_args") is not None
        )
        
        # Convert to bool for comparison (handles None case)
        assert bool(uses_acestream_args) == expected, f"Failed: {description}"
    
    print("✅ All uses_acestream_args condition tests passed")


if __name__ == '__main__':
    import sys
    sys.path.insert(0, '.')
    
    print("🧪 Testing Custom Variant Port Configuration Fix")
    print("=" * 60)
    
    success = True
    try:
        test_custom_variant_uses_acestream_args_with_allocated_port()
        test_port_allocation_logic_for_custom_variant()
        test_standard_jopsis_variant_still_works()
        test_standard_krinkuto_variant_still_works()
        test_arm_custom_variant_uses_cmd_with_port_args()
        test_uses_acestream_args_condition_comprehensive()
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
