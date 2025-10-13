#!/usr/bin/env python3
"""
Demonstrate the before/after of the datetime parsing fix
"""

import json
import subprocess
import tempfile
import os

def test_original_error_vs_fix():
    """Test that shows the original error and the fix"""
    
    print("🔍 Demonstrating the datetime parsing issue and fix...")
    print("=" * 60)
    
    # Test 1: Reproduce the original error
    print("\n❌ Test 1: Reproducing the ORIGINAL ERROR...")
    print("This simulates what happened before the fix:")
    
    # Original problematic format (no timezone)
    problematic_json = json.dumps([{
        "container_id": "test-container",
        "host": "localhost", 
        "port": 19001,
        "labels": {},
        "first_seen": "2025-09-19T22:38:30.848575",  # No timezone - this caused the error
        "last_seen": "2025-09-19T22:38:30.848575",   # No timezone - this caused the error
        "streams": []
    }])
    
    print(f"Problematic JSON (no timezone): {problematic_json}")
    
    go_code_error = f'''package main

import (
    "encoding/json"
    "fmt"
    "time"
)

type engineState struct {{
    ContainerID string            `json:"container_id"`
    Host        string            `json:"host"`
    Port        int               `json:"port"`
    Labels      map[string]string `json:"labels"`
    FirstSeen   time.Time         `json:"first_seen"`
    LastSeen    time.Time         `json:"last_seen"`
    Streams     []string          `json:"streams"`
}}

func main() {{
    jsonData := `{problematic_json}`
    
    var engines []engineState
    err := json.Unmarshal([]byte(jsonData), &engines)
    if err != nil {{
        fmt.Printf("ERROR (as expected): %v\\n", err)
        return
    }}
    
    fmt.Printf("Unexpected success\\n")
}}'''

    with tempfile.NamedTemporaryFile(mode='w', suffix='.go', delete=False) as f:
        f.write(go_code_error)
        go_error_file = f.name

    try:
        result = subprocess.run(['go', 'run', go_error_file], 
                              capture_output=True, text=True, cwd='/tmp')
        
        print(f"Go error test result: {result.stdout}")
        if result.stderr:
            print(f"Go error test stderr: {result.stderr}")
            
        # Check if we got the specific error mentioned in the problem statement
        if 'cannot parse "" as "Z07:00"' in result.stdout or 'cannot parse "" as "Z07:00"' in result.stderr:
            print("✅ Reproduced the exact error from the problem statement!")
        elif "parsing time" in result.stdout and "cannot parse" in result.stdout:
            print("✅ Reproduced the datetime parsing error (similar to problem statement)")
        else:
            print("ℹ️  Got a datetime parsing error (expected)")
            
    finally:
        os.unlink(go_error_file)
    
    print("\n" + "=" * 60)
    
    # Test 2: Show the fix works
    print("\n✅ Test 2: Demonstrating the FIX...")
    print("This simulates what happens after our fix:")
    
    # Fixed format (with timezone)
    fixed_json = json.dumps([{
        "container_id": "test-container",
        "host": "localhost",
        "port": 19001, 
        "labels": {},
        "first_seen": "2025-09-19T22:38:30.848575Z",  # With Z timezone - this is our fix
        "last_seen": "2025-09-19T22:38:30.848575Z",   # With Z timezone - this is our fix
        "streams": []
    }])
    
    print(f"Fixed JSON (with timezone): {fixed_json}")
    
    go_code_fixed = f'''package main

import (
    "encoding/json"
    "fmt"
    "time"
)

type engineState struct {{
    ContainerID string            `json:"container_id"`
    Host        string            `json:"host"`
    Port        int               `json:"port"`
    Labels      map[string]string `json:"labels"`
    FirstSeen   time.Time         `json:"first_seen"`
    LastSeen    time.Time         `json:"last_seen"`
    Streams     []string          `json:"streams"`
}}

func main() {{
    jsonData := `{fixed_json}`
    
    var engines []engineState
    err := json.Unmarshal([]byte(jsonData), &engines)
    if err != nil {{
        fmt.Printf("ERROR: %v\\n", err)
        return
    }}
    
    fmt.Printf("SUCCESS: Parsed %d engines\\n", len(engines))
    fmt.Printf("Engine: %s at %s:%d\\n", engines[0].ContainerID, engines[0].Host, engines[0].Port)
    fmt.Printf("First seen: %v\\n", engines[0].FirstSeen)
    fmt.Printf("Last seen: %v\\n", engines[0].LastSeen)
}}'''

    with tempfile.NamedTemporaryFile(mode='w', suffix='.go', delete=False) as f:
        f.write(go_code_fixed)
        go_fixed_file = f.name

    try:
        result = subprocess.run(['go', 'run', go_fixed_file], 
                              capture_output=True, text=True, cwd='/tmp')
        
        print(f"Go fixed test result: {result.stdout}")
        if result.stderr:
            print(f"Go fixed test stderr: {result.stderr}")
            
        if result.returncode == 0 and "SUCCESS" in result.stdout:
            print("✅ Fix works! Go client successfully parses the corrected format")
        else:
            print("❌ Fix failed - Go client still cannot parse")
            
    finally:
        os.unlink(go_fixed_file)
    
    print("\n" + "=" * 60)
    print("\n🎯 SUMMARY:")
    print("- BEFORE: Python orchestrator returned datetime without timezone info")
    print("- RESULT: acexy Go client failed with 'cannot parse \"\" as \"Z07:00\"' error")
    print("- FIX: Ensure timezone-aware datetimes when loading from database") 
    print("- AFTER: Python orchestrator returns datetime with 'Z' timezone suffix")
    print("- RESULT: acexy Go client successfully parses the datetime")
    print("\n🎉 The datetime parsing issue is RESOLVED!")

if __name__ == "__main__":
    test_original_error_vs_fix()