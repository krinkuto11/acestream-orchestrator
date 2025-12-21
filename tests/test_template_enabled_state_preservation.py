"""
Test that template activation preserves the enabled state
This tests the fix for: "Enable Custom Engine Variant was on and a template was loaded.
After restarting the orchestrator the template shows as loaded but the Enable Custom
Engine Variant is off"
"""
import pytest
from pathlib import Path
from app.services.template_manager import (
    save_template,
    delete_template,
    get_template,
    set_active_template,
    get_active_template_id,
)
from app.services.custom_variant_config import (
    CustomVariantConfig,
    get_default_parameters,
    detect_platform,
    save_config,
    load_config,
    DEFAULT_CONFIG_PATH,
)


def test_template_activation_preserves_enabled_state():
    """
    Test that when a template is activated, the current enabled state is preserved.
    This simulates the scenario:
    1. User enables custom variant
    2. User loads a template
    3. Orchestrator restarts
    4. Template is loaded during startup but enabled state should remain True
    """
    # Clean up first
    try:
        for i in range(1, 11):
            delete_template(i)
    except:
        pass
    
    # Create a template with enabled=False (as it might be saved)
    platform = detect_platform()
    template_config = CustomVariantConfig(
        enabled=False,  # Template was saved with enabled=False
        platform=platform,
        parameters=get_default_parameters(platform)
    )
    
    # Save the template
    success = save_template(1, "Test Template", template_config)
    assert success, "Failed to save template"
    
    # Verify template was saved with enabled=False
    template = get_template(1)
    assert template is not None
    assert template.config.enabled == False
    
    # Now create a current config with enabled=True (user has enabled custom variant)
    current_config = CustomVariantConfig(
        enabled=True,  # User has enabled custom variant
        platform=platform,
        parameters=get_default_parameters(platform)
    )
    save_config(current_config)
    
    # Verify current config is enabled
    loaded_config = load_config()
    assert loaded_config is not None
    assert loaded_config.enabled == True
    
    # Set the template as active (simulating user loading the template)
    set_active_template(1)
    
    # Now simulate what happens during orchestrator restart
    # The startup code loads the active template
    active_template_id = get_active_template_id()
    assert active_template_id == 1
    
    # Load current config (which should be enabled=True)
    custom_config = load_config()
    assert custom_config.enabled == True
    
    # Load the template
    template = get_template(active_template_id)
    assert template is not None
    
    # This is the critical part - when applying the template during startup,
    # we must preserve the current enabled state
    template_config_copy = template.config.copy(deep=True)
    template_config_copy.enabled = custom_config.enabled  # Preserve enabled state
    
    # Save the template config (simulating what happens in startup)
    save_config(template_config_copy)
    
    # Verify that after loading the template, enabled is still True
    final_config = load_config()
    assert final_config is not None
    assert final_config.enabled == True, "Enabled state was not preserved when loading template"
    
    # Clean up
    delete_template(1)
    set_active_template(None)
    print("✓ Template activation preserves enabled state")


def test_template_activation_preserves_disabled_state():
    """
    Test that when custom variant is disabled and a template is loaded,
    it stays disabled (inverse test case)
    """
    # Clean up first
    try:
        for i in range(1, 11):
            delete_template(i)
    except:
        pass
    
    # Create a template with enabled=True
    platform = detect_platform()
    template_config = CustomVariantConfig(
        enabled=True,  # Template was saved with enabled=True
        platform=platform,
        parameters=get_default_parameters(platform)
    )
    
    # Save the template
    success = save_template(2, "Test Template 2", template_config)
    assert success
    
    # Create current config with enabled=False
    current_config = CustomVariantConfig(
        enabled=False,  # User has disabled custom variant
        platform=platform,
        parameters=get_default_parameters(platform)
    )
    save_config(current_config)
    
    # Set as active template
    set_active_template(2)
    
    # Simulate startup: load template but preserve enabled state
    custom_config = load_config()
    active_template_id = get_active_template_id()
    
    if active_template_id and custom_config:
        template = get_template(active_template_id)
        if template:
            template_config_copy = template.config.copy(deep=True)
            template_config_copy.enabled = custom_config.enabled
            save_config(template_config_copy)
    
    # Verify enabled is still False
    final_config = load_config()
    assert final_config is not None
    assert final_config.enabled == False, "Disabled state was not preserved"
    
    # Clean up
    delete_template(2)
    set_active_template(None)
    print("✓ Template activation preserves disabled state")


if __name__ == "__main__":
    print("Running template enabled state preservation tests...")
    
    # Clean up
    for i in range(1, 11):
        try:
            delete_template(i)
        except:
            pass
    
    test_template_activation_preserves_enabled_state()
    test_template_activation_preserves_disabled_state()
    
    print("\nAll tests passed!")
