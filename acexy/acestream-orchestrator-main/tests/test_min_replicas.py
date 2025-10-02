#!/usr/bin/env python3
"""
Test to verify that MIN_REPLICAS containers are properly provisioned on startup
and can be reached via both Docker socket and orchestrator API.
"""

import os
import sys
import time
import subprocess
import signal
import requests
from datetime import datetime, timedelta
import json

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

def test_min_replicas_provisioning():
    """Test that MIN_REPLICAS=3 causes 3 containers to be started and reachable."""
    
    print("🧪 Testing MIN_REPLICAS provisioning...")
    
    # Set up test environment with MIN_REPLICAS=3
    test_env = os.environ.copy()
    test_env.update({
        'MIN_REPLICAS': '3',
        'MAX_REPLICAS': '10',
        'APP_PORT': '8001',  # Use different port to avoid conflicts
        'TARGET_IMAGE': 'nginx:alpine',  # Use lightweight, long-running image for testing
        'CONTAINER_LABEL': 'test.orchestrator=true',
        'STARTUP_TIMEOUT_S': '10',
        'API_KEY': 'test-key-123'
    })
    
    # Start orchestrator in background
    print("\n📋 Step 1: Starting orchestrator with MIN_REPLICAS=3...")
    
    proc = None
    try:
        proc = subprocess.Popen([
            sys.executable, '-m', 'uvicorn', 
            'app.main:app', 
            '--host', '0.0.0.0', 
            '--port', '8001'
        ], env=test_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Wait for startup
        print("⏳ Waiting for orchestrator to start...")
        time.sleep(10)
        
        # Test 1: Check if orchestrator is responding
        print("\n📋 Step 2: Testing orchestrator API connectivity...")
        try:
            response = requests.get('http://localhost:8001/engines', timeout=5)
            print(f"✅ Orchestrator API accessible, status: {response.status_code}")
            if response.status_code == 200:
                engines = response.json()
                print(f"📊 Found {len(engines)} engines in orchestrator API")
            else:
                print(f"⚠️ Unexpected status code: {response.status_code}")
                print(f"Response: {response.text}")
        except Exception as e:
            print(f"❌ Failed to connect to orchestrator API: {e}")
            return False
        
        # Test 2: Check Docker containers via orchestrator API
        print("\n📋 Step 3: Checking containers via orchestrator API...")
        try:
            headers = {'Authorization': 'Bearer test-key-123'}
            response = requests.get('http://localhost:8001/by-label?key=test.orchestrator&value=true', 
                                  headers=headers, timeout=5)
            if response.status_code == 200:
                containers = response.json()
                print(f"📊 Found {len(containers)} managed containers via API")
                for i, container in enumerate(containers):
                    print(f"   Container {i+1}: {container.get('id', 'unknown')[:12]} - {container.get('status', 'unknown')}")
            else:
                print(f"⚠️ API returned status {response.status_code}: {response.text}")
        except Exception as e:
            print(f"❌ Failed to query containers via API: {e}")
        
        # Test 3: Check Docker containers directly via Docker socket
        print("\n📋 Step 4: Checking containers via Docker socket...")
        try:
            import docker
            client = docker.from_env()
            
            # List all containers with our test label
            containers = client.containers.list(all=True, filters={
                'label': 'test.orchestrator=true'
            })
            
            print(f"📊 Found {len(containers)} containers with test label via Docker socket")
            
            running_containers = [c for c in containers if c.status == 'running']
            print(f"📊 {len(running_containers)} containers are currently running")
            
            for i, container in enumerate(containers):
                print(f"   Container {i+1}: {container.id[:12]} - {container.status} - Image: {container.image.tags}")
                
            # Check if we have the expected number of containers
            expected_count = 3
            if len(running_containers) >= expected_count:
                print(f"✅ SUCCESS: Found {len(running_containers)} running containers (expected: {expected_count})")
                return True
            else:
                print(f"❌ FAILURE: Found {len(running_containers)} running containers (expected: {expected_count})")
                
                # Additional debugging - check if containers failed to start
                failed_containers = [c for c in containers if c.status != 'running']
                if failed_containers:
                    print(f"⚠️ Found {len(failed_containers)} non-running containers:")
                    for container in failed_containers:
                        print(f"   - {container.id[:12]}: {container.status}")
                        try:
                            logs = container.logs().decode('utf-8')
                            print(f"     Logs: {logs[:200]}...")
                        except:
                            print("     (Could not retrieve logs)")
                            
                return False
                
        except Exception as e:
            print(f"❌ Failed to check Docker socket: {e}")
            return False
            
    finally:
        # Clean up
        print("\n🧹 Cleaning up...")
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        
        # Clean up test containers
        try:
            import docker
            client = docker.from_env()
            containers = client.containers.list(all=True, filters={
                'label': 'test.orchestrator=true'
            })
            for container in containers:
                print(f"🗑️ Removing test container {container.id[:12]}")
                try:
                    container.stop(timeout=1)
                except:
                    pass
                try:
                    container.remove(force=True)
                except:
                    pass
        except Exception as e:
            print(f"⚠️ Error during cleanup: {e}")

if __name__ == "__main__":
    try:
        success = test_min_replicas_provisioning()
        print(f"\n🎯 Test result: {'PASSED' if success else 'FAILED'}")
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)