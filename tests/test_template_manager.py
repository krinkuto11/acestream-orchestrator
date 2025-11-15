"""
Test template management system
"""
import pytest
import json
from pathlib import Path
from app.services.template_manager import (
    list_templates,
    get_template,
    save_template,
    delete_template,
    export_template,
    import_template,
    TEMPLATE_DIR
)
from app.services.custom_variant_config import (
    CustomVariantConfig,
    get_default_parameters,
    detect_platform
)


def test_list_templates_initial():
    """Test listing templates when none exist"""
    templates = list_templates()
    assert len(templates) == 10
    assert all(not t["exists"] for t in templates)
    assert all(t["slot_id"] >= 1 and t["slot_id"] <= 10 for t in templates)


def test_save_and_load_template():
    """Test saving and loading a template"""
    # Create a test configuration
    platform = detect_platform()
    config = CustomVariantConfig(
        enabled=True,
        platform=platform,
        parameters=get_default_parameters(platform)
    )
    
    # Save template
    success = save_template(1, "Test Template", config)
    assert success
    
    # Load template
    template = get_template(1)
    assert template is not None
    assert template.slot_id == 1
    assert template.name == "Test Template"
    assert template.config.enabled == True
    assert template.config.platform == platform
    
    # Clean up
    delete_template(1)


def test_delete_template():
    """Test deleting a template"""
    # Create a test template
    platform = detect_platform()
    config = CustomVariantConfig(
        enabled=True,
        platform=platform,
        parameters=get_default_parameters(platform)
    )
    save_template(2, "Delete Test", config)
    
    # Delete it
    success = delete_template(2)
    assert success
    
    # Verify it's gone
    template = get_template(2)
    assert template is None


def test_export_template():
    """Test exporting a template"""
    # Create a test template
    platform = detect_platform()
    config = CustomVariantConfig(
        enabled=True,
        platform=platform,
        parameters=get_default_parameters(platform)
    )
    save_template(3, "Export Test", config)
    
    # Export it
    json_data = export_template(3)
    assert json_data is not None
    
    # Parse and verify
    data = json.loads(json_data)
    assert data["slot_id"] == 3
    assert data["name"] == "Export Test"
    assert "config" in data
    
    # Clean up
    delete_template(3)


def test_import_template():
    """Test importing a template"""
    # Create test data
    platform = detect_platform()
    config = CustomVariantConfig(
        enabled=True,
        platform=platform,
        parameters=get_default_parameters(platform)
    )
    
    template_data = {
        "slot_id": 4,
        "name": "Import Test",
        "config": config.dict()
    }
    json_data = json.dumps(template_data)
    
    # Import it
    success, error = import_template(4, json_data)
    assert success
    assert error is None
    
    # Verify it was imported
    template = get_template(4)
    assert template is not None
    assert template.name == "Import Test"
    
    # Clean up
    delete_template(4)


def test_invalid_slot_id():
    """Test that invalid slot IDs are rejected"""
    with pytest.raises(ValueError):
        get_template(0)
    
    with pytest.raises(ValueError):
        get_template(11)


def test_multiple_templates():
    """Test managing multiple templates"""
    platform = detect_platform()
    
    # Save multiple templates
    for i in range(1, 6):
        config = CustomVariantConfig(
            enabled=True,
            platform=platform,
            parameters=get_default_parameters(platform)
        )
        save_template(i, f"Template {i}", config)
    
    # List them
    templates = list_templates()
    existing = [t for t in templates if t["exists"]]
    assert len(existing) >= 5
    
    # Clean up
    for i in range(1, 6):
        delete_template(i)


if __name__ == "__main__":
    # Run tests
    print("Running template manager tests...")
    
    # Clean up any existing test templates
    for i in range(1, 11):
        try:
            delete_template(i)
        except:
            pass
    
    test_list_templates_initial()
    print("✓ List templates initial")
    
    test_save_and_load_template()
    print("✓ Save and load template")
    
    test_delete_template()
    print("✓ Delete template")
    
    test_export_template()
    print("✓ Export template")
    
    test_import_template()
    print("✓ Import template")
    
    test_invalid_slot_id()
    print("✓ Invalid slot ID")
    
    test_multiple_templates()
    print("✓ Multiple templates")
    
    print("\nAll tests passed!")
