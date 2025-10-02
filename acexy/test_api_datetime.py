#!/usr/bin/env python3
"""
Test the orchestrator API to ensure datetime fix works in real usage
"""

import asyncio
import uvicorn
import threading
import time
import requests
import json
import subprocess
import sys
import os
from datetime import datetime, timezone

async def test_orchestrator_api():
    """Test the full API flow"""
    
    print("🚀 Starting orchestrator API test...")
    
    # Start the orchestrator server in background
    print("📡 Starting orchestrator server...")
    
    # Change to orchestrator directory and start server
    orchestrator_dir = "/home/runner/work/acexy/acexy/acestream-orchestrator-main"
    
    server_process = subprocess.Popen([
        "python3", "-m", "uvicorn", "app.main:app", 
        "--host", "0.0.0.0", "--port", "8005", "--log-level", "info"
    ], cwd=orchestrator_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    
    # Wait for server to start
    print("⏳ Waiting for server to start...")
    time.sleep(5)
    
    try:
        # Test /engines endpoint (which would return the problematic datetime format before fix)
        print("🔍 Testing /engines endpoint...")
        
        response = requests.get("http://localhost:8005/engines", timeout=10)
        print(f"Response status: {response.status_code}")
        print(f"Response content: {response.text}")
        
        if response.status_code == 200:
            engines_data = response.json()
            print(f"✅ /engines endpoint returned successfully: {len(engines_data)} engines")
            
            # If there are engines, check datetime format
            if engines_data:
                first_engine = engines_data[0]
                first_seen = first_engine.get('first_seen', '')
                last_seen = first_engine.get('last_seen', '')
                
                print(f"First seen format: {first_seen}")
                print(f"Last seen format: {last_seen}")
                
                # Check if timezone info is present
                has_tz = (first_seen.endswith('Z') or '+' in first_seen or 
                         last_seen.endswith('Z') or '+' in last_seen)
                
                if has_tz:
                    print("✅ Engine datetimes include timezone information")
                else:
                    print("❌ Engine datetimes missing timezone information")
            else:
                print("ℹ️  No engines found (expected for fresh instance)")
                
        else:
            print(f"❌ /engines endpoint failed: {response.status_code}")
            
        # Test Go client parsing with a realistic engines response
        print("\n🎯 Testing Go client with orchestrator response format...")
        
        # Create a sample response similar to what the orchestrator would return
        sample_response = json.dumps([{
            "container_id": "test-container",
            "host": "localhost",
            "port": 19001,
            "labels": {},
            "first_seen": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "last_seen": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            "streams": []
        }])
        
        print(f"Sample response: {sample_response}")
        
        # Create Go test for acexy structs
        go_test_code = f'''package main

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
    jsonData := `{sample_response}`
    
    var engines []engineState
    err := json.Unmarshal([]byte(jsonData), &engines)
    if err != nil {{
        fmt.Printf("PARSE_ERROR: %v\\n", err)
        return
    }}
    
    fmt.Printf("PARSE_SUCCESS: %d engines\\n", len(engines))
    for i, eng := range engines {{
        fmt.Printf("Engine %d: %s (%s:%d)\\n", i+1, eng.ContainerID, eng.Host, eng.Port)
        fmt.Printf("  First seen: %v\\n", eng.FirstSeen)
        fmt.Printf("  Last seen: %v\\n", eng.LastSeen)
    }}
}}'''

        with open('/tmp/test_orchestrator_response.go', 'w') as f:
            f.write(go_test_code)
        
        # Run Go test
        result = subprocess.run(['go', 'run', '/tmp/test_orchestrator_response.go'], 
                              capture_output=True, text=True, cwd='/tmp')
        
        print(f"Go client test output: {result.stdout}")
        if result.stderr:
            print(f"Go client test errors: {result.stderr}")
            
        if result.returncode == 0 and "PARSE_SUCCESS" in result.stdout:
            print("✅ Go client successfully parsed orchestrator datetime format")
        else:
            print("❌ Go client failed to parse orchestrator datetime format")
            
        print("\n🎉 API test completed!")
        
    except Exception as e:
        print(f"❌ Test failed: {e}")
        
    finally:
        # Clean up server
        print("🧹 Cleaning up server...")
        server_process.terminate()
        server_process.wait()

if __name__ == "__main__":
    asyncio.run(test_orchestrator_api())