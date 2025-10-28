#!/usr/bin/env python3
"""
Test to verify the uvicorn command in Dockerfile uses correct syntax.
"""

import subprocess
import sys
import re


def test_dockerfile_cmd_syntax():
    """Test that the Dockerfile CMD uses correct uvicorn syntax."""
    print("🧪 Testing Dockerfile CMD syntax...")
    
    try:
        # Read Dockerfile
        with open('Dockerfile', 'r') as f:
            dockerfile_content = f.read()
        
        # Extract CMD line
        cmd_match = re.search(r'CMD\s+\[(.*?)\]', dockerfile_content, re.DOTALL)
        if not cmd_match:
            print("❌ Could not find CMD in Dockerfile")
            return False
        
        cmd_line = cmd_match.group(1)
        print(f"   Found CMD: {cmd_line}")
        
        # Check that it doesn't contain the incorrect syntax
        if '"--access-log", "false"' in cmd_line:
            print("❌ Dockerfile still contains incorrect '--access-log false' syntax")
            return False
        
        # Check that it contains the correct syntax
        if '"--no-access-log"' in cmd_line:
            print("✅ Dockerfile uses correct '--no-access-log' syntax")
        else:
            print("⚠️  Dockerfile doesn't disable access log (this is OK)")
        
        # Verify the CMD can be parsed as valid JSON array
        import json
        try:
            cmd_array = json.loads(f'[{cmd_line}]')
            print(f"✅ CMD is valid JSON array with {len(cmd_array)} arguments")
            print(f"   Command: {' '.join(cmd_array)}")
        except json.JSONDecodeError as e:
            print(f"❌ CMD is not valid JSON: {e}")
            return False
        
        # Test that uvicorn accepts --no-access-log flag
        print("\n🧪 Testing uvicorn --no-access-log flag...")
        result = subprocess.run(
            ['uvicorn', '--help'],
            capture_output=True,
            text=True,
            timeout=5
        )
        
        if '--no-access-log' in result.stdout:
            print("✅ uvicorn supports --no-access-log flag")
        else:
            print("❌ uvicorn does not support --no-access-log flag")
            return False
        
        print("\n✅ All Dockerfile CMD syntax tests passed")
        return True
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = test_dockerfile_cmd_syntax()
    sys.exit(0 if success else 1)
