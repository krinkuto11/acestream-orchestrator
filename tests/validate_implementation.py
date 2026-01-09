#!/usr/bin/env python3
"""
Syntax and structure validation for the custom engines bug fix and backup system.
This validates code structure without requiring dependencies.
"""

import ast
import sys
from pathlib import Path

def validate_python_file(filepath):
    """Validate Python file syntax"""
    try:
        with open(filepath, 'r') as f:
            code = f.read()
        ast.parse(code)
        return True, None
    except SyntaxError as e:
        return False, str(e)

def check_function_exists(filepath, function_name):
    """Check if a function exists in a Python file"""
    try:
        with open(filepath, 'r') as f:
            code = f.read()
        tree = ast.parse(code)
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == function_name:
                    return True
        return False
    except Exception as e:
        print(f"Error checking function {function_name}: {e}")
        return False

def main():
    """Run validation checks"""
    print("=" * 60)
    print("Syntax and Structure Validation")
    print("=" * 60)
    print()
    
    base_path = Path(__file__).parent.parent
    
    # Files to validate
    files_to_check = [
        ("app/main.py", [
            "export_settings",
            "import_settings_data",
            "get_engines",
        ]),
        ("app/services/custom_variant_config.py", [
            "detect_platform",
            "validate_config",
            "is_custom_variant_enabled",
        ]),
        ("app/services/template_manager.py", [
            "get_template",
            "save_template",
            "set_active_template",
            "get_active_template_id",
            "get_active_template_name",
        ]),
        ("app/static/panel-react/src/App.jsx", []),
        ("app/static/panel-react/src/pages/OverviewPage.jsx", []),
        ("app/static/panel-react/src/pages/SettingsPage.jsx", []),
        ("app/static/panel-react/src/pages/settings/BackupSettings.jsx", []),
    ]
    
    all_valid = True
    
    for filepath, functions in files_to_check:
        full_path = base_path / filepath
        print(f"Checking {filepath}...")
        
        # Check file exists
        if not full_path.exists():
            print(f"  ❌ File not found")
            all_valid = False
            continue
        
        # For Python files, validate syntax
        if filepath.endswith('.py'):
            valid, error = validate_python_file(full_path)
            if not valid:
                print(f"  ❌ Syntax error: {error}")
                all_valid = False
                continue
            print(f"  ✓ Syntax valid")
            
            # Check for required functions
            for func in functions:
                if check_function_exists(full_path, func):
                    print(f"  ✓ Function '{func}' exists")
                else:
                    print(f"  ❌ Function '{func}' not found")
                    all_valid = False
        else:
            # For non-Python files, just check they exist
            print(f"  ✓ File exists")
    
    print()
    print("=" * 60)
    
    # Check specific fixes
    print("\nChecking specific bug fixes...")
    
    # Check 1: Active Streams filter in App.jsx
    app_jsx = base_path / "app/static/panel-react/src/App.jsx"
    with open(app_jsx, 'r') as f:
        content = f.read()
        if 'streams?status=started' in content:
            print("✓ Active Streams counter fix applied (status=started filter)")
        else:
            print("❌ Active Streams counter fix not found")
            all_valid = False
    
    # Check 2: set_active_template in startup code
    main_py = base_path / "app/main.py"
    with open(main_py, 'r') as f:
        content = f.read()
        if 'set_active_template(active_template_id)' in content:
            print("✓ Template activation fix applied in startup code")
        else:
            print("❌ Template activation fix not found in startup")
            all_valid = False
        
        if 'def export_settings' in content:
            print("✓ Export settings endpoint exists")
        else:
            print("❌ Export settings endpoint not found")
            all_valid = False
        
        if 'def import_settings_data' in content:
            print("✓ Import settings endpoint exists")
        else:
            print("❌ Import settings endpoint not found")
            all_valid = False
    
    # Check 3: BackupSettings component exists
    backup_jsx = base_path / "app/static/panel-react/src/pages/settings/BackupSettings.jsx"
    if backup_jsx.exists():
        with open(backup_jsx, 'r') as f:
            content = f.read()
            if 'export function BackupSettings' in content:
                print("✓ BackupSettings component exists")
            else:
                print("❌ BackupSettings component export not found")
                all_valid = False
            
            if 'handleExport' in content and 'handleImport' in content:
                print("✓ BackupSettings has export and import handlers")
            else:
                print("❌ BackupSettings missing handlers")
                all_valid = False
    else:
        print("❌ BackupSettings component file not found")
        all_valid = False
    
    # Check 4: SettingsPage includes BackupSettings
    settings_page = base_path / "app/static/panel-react/src/pages/SettingsPage.jsx"
    with open(settings_page, 'r') as f:
        content = f.read()
        if 'BackupSettings' in content and 'backup' in content:
            print("✓ SettingsPage includes BackupSettings tab")
        else:
            print("❌ SettingsPage missing BackupSettings integration")
            all_valid = False
    
    print()
    print("=" * 60)
    
    if all_valid:
        print("✅ All validation checks passed!")
        print("=" * 60)
        return 0
    else:
        print("❌ Some validation checks failed")
        print("=" * 60)
        return 1

if __name__ == "__main__":
    sys.exit(main())
