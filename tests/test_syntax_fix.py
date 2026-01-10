#!/usr/bin/env python3
"""
Test to verify the IndentationError fix in app/main.py.
This test ensures the module can be compiled without syntax errors.
"""

import py_compile
import os
import sys

def test_main_syntax():
    """Test that app/main.py has valid Python syntax."""
    print("\n🧪 Testing app/main.py syntax...")
    
    # Get the path to main.py
    repo_root = os.path.dirname(os.path.dirname(__file__))
    main_path = os.path.join(repo_root, 'app', 'main.py')
    
    # Ensure the file exists
    assert os.path.exists(main_path), f"main.py not found at {main_path}"
    
    # Try to compile the file - this will raise SyntaxError if there are issues
    try:
        py_compile.compile(main_path, doraise=True)
        print("✅ app/main.py has valid Python syntax")
        return True
    except SyntaxError as e:
        print(f"❌ Syntax error in app/main.py: {e}")
        raise


if __name__ == "__main__":
    success = test_main_syntax()
    sys.exit(0 if success else 1)
