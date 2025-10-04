#!/usr/bin/env python3
"""
Simple verification script for the React dashboard build.
"""
import os
import sys

def verify_panel_build():
    """Verify that the panel build is complete and valid."""
    panel_dir = "app/static/panel"
    required_files = [
        "index.html",
    ]
    
    print("Verifying React dashboard build...")
    
    # Check if panel directory exists
    if not os.path.exists(panel_dir):
        print(f"❌ Panel directory not found: {panel_dir}")
        return False
    
    # Check required files
    missing_files = []
    for file in required_files:
        file_path = os.path.join(panel_dir, file)
        if not os.path.exists(file_path):
            missing_files.append(file)
        else:
            print(f"✓ Found: {file}")
    
    if missing_files:
        print(f"❌ Missing files: {', '.join(missing_files)}")
        return False
    
    # Check if index.html contains React bundle reference
    index_path = os.path.join(panel_dir, "index.html")
    with open(index_path, 'r') as f:
        content = f.read()
        if 'assets/index-' not in content:
            print("❌ index.html does not contain React bundle reference")
            return False
        print("✓ React bundle reference found in index.html")
    
    # Check if assets directory exists
    assets_dir = os.path.join(panel_dir, "assets")
    if not os.path.exists(assets_dir):
        print(f"❌ Assets directory not found: {assets_dir}")
        return False
    print(f"✓ Assets directory exists")
    
    # Check if there are JavaScript files in assets
    js_files = [f for f in os.listdir(assets_dir) if f.endswith('.js')]
    if not js_files:
        print("❌ No JavaScript files found in assets directory")
        return False
    print(f"✓ Found {len(js_files)} JavaScript file(s) in assets")
    
    print("\n✅ All checks passed! The React dashboard build is valid.")
    return True

if __name__ == "__main__":
    success = verify_panel_build()
    sys.exit(0 if success else 1)
