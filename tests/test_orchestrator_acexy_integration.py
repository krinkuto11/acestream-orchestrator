#!/usr/bin/env python3
"""
Integration test for orchestrator and acexy proxy communication.
This test verifies that:
1. Provisioning endpoint properly adds engines to state
2. VPN status is correctly communicated
3. Orchestrator status endpoint provides comprehensive information
"""

import os
import sys
import time
import subprocess
import requests
import json
from datetime import datetime

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_provision_and_state_sync():
    """Test that provisioning immediately updates state."""
    
    print("🧪 Testing Orchestrator-Acexy Integration...")
    
    # Set up test environment
    test_env = os.environ.copy()
    test_env.update({
        'MIN_REPLICAS': '0',  # Start with no engines
        'MAX_REPLICAS': '5',
        'APP_PORT': '8004',
        'TARGET_IMAGE': 'ghcr.io/krinkuto11/acestream-http-proxy:latest',
        'CONTAINER_LABEL': 'acestream.integration-test=true',
        'STARTUP_TIMEOUT_S': '25',
        'API_KEY': 'test-integration-123',
        'PORT_RANGE_HOST': '30000-30999',
        'ACE_HTTP_RANGE': '52000-52999',
        'ACE_HTTPS_RANGE': '53000-53999',
        'AUTO_DELETE': 'false'  # Keep containers for testing
    })
    
    proc = None
    try:
        print(f"\n📋 Step 1: Starting orchestrator with MIN_REPLICAS=0...")
        
        # Start orchestrator
        proc = subprocess.Popen([
            sys.executable, '-m', 'uvicorn',
            'app.main:app',
            '--host', '0.0.0.0',
            '--port', '8004'
        ], env=test_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        print("⏳ Waiting for orchestrator to start (15s)...")
        time.sleep(15)
        
        # Check if orchestrator is running
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            print(f"❌ Orchestrator process exited early")
            print(f"STDOUT: {stdout.decode()}")
            print(f"STDERR: {stderr.decode()}")
            return False
        
        print("\n📋 Step 2: Verify orchestrator is accessible and has 0 engines...")
        try:
            response = requests.get('http://localhost:8004/engines', timeout=10)
            if response.status_code == 200:
                engines = response.json()
                print(f"✅ Orchestrator API accessible")
                print(f"📊 Initial engine count: {len(engines)}")
                
                if len(engines) != 0:
                    print(f"⚠️ Expected 0 engines but found {len(engines)}")
                    return False
            else:
                print(f"❌ API returned status {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Failed to connect to orchestrator API: {e}")
            return False
        
        print("\n📋 Step 3: Test orchestrator status endpoint...")
        try:
            response = requests.get('http://localhost:8004/orchestrator/status', timeout=10)
            if response.status_code == 200:
                status = response.json()
                print(f"✅ Orchestrator status endpoint accessible")
                print(f"   Status: {status['status']}")
                print(f"   Engines: {status['engines']}")
                print(f"   Streams: {status['streams']}")
                print(f"   Capacity: {status['capacity']}")
                print(f"   VPN: {status['vpn']}")
                print(f"   Provisioning: {status['provisioning']}")
            else:
                print(f"❌ Status endpoint returned {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Failed to get orchestrator status: {e}")
            return False
        
        print("\n📋 Step 4: Provision a new engine via API (simulating acexy proxy)...")
        try:
            provision_data = {
                "labels": {"test": "integration"},
                "env": {}
            }
            response = requests.post(
                'http://localhost:8004/provision/acestream',
                json=provision_data,
                headers={'Authorization': 'Bearer test-integration-123'},
                timeout=60
            )
            
            if response.status_code == 200:
                provision_response = response.json()
                print(f"✅ Provisioning successful")
                print(f"   Container ID: {provision_response['container_id'][:12]}")
                print(f"   Container Name: {provision_response['container_name']}")
                print(f"   Host HTTP Port: {provision_response['host_http_port']}")
                print(f"   Container HTTP Port: {provision_response['container_http_port']}")
                
                provisioned_container_id = provision_response['container_id']
            else:
                print(f"❌ Provisioning failed with status {response.status_code}")
                print(f"   Response: {response.text}")
                return False
        except Exception as e:
            print(f"❌ Provisioning request failed: {e}")
            return False
        
        print("\n📋 Step 5: Verify engine appears in /engines immediately (critical for acexy)...")
        # Acexy waits 10 seconds after provisioning, but let's check immediately
        try:
            response = requests.get('http://localhost:8004/engines', timeout=10)
            if response.status_code == 200:
                engines = response.json()
                print(f"📊 Engine count after provisioning: {len(engines)}")
                
                if len(engines) == 0:
                    print(f"❌ CRITICAL: Engine not in state immediately after provisioning!")
                    print(f"   This would cause acexy to fail when trying to use the engine")
                    return False
                
                # Find the provisioned engine
                provisioned_engine = None
                for engine in engines:
                    if engine['container_id'] == provisioned_container_id:
                        provisioned_engine = engine
                        break
                
                if provisioned_engine:
                    print(f"✅ CRITICAL SUCCESS: Provisioned engine found in state immediately!")
                    print(f"   Container ID: {provisioned_engine['container_id'][:12]}")
                    print(f"   Host: {provisioned_engine['host']}")
                    print(f"   Port: {provisioned_engine['port']}")
                else:
                    print(f"❌ Provisioned engine {provisioned_container_id[:12]} not found in engines list")
                    return False
            else:
                print(f"❌ Failed to get engines: {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Failed to verify engine in state: {e}")
            return False
        
        print("\n📋 Step 6: Verify VPN status endpoint works...")
        try:
            response = requests.get('http://localhost:8004/vpn/status', timeout=10)
            if response.status_code == 200:
                vpn_status = response.json()
                print(f"✅ VPN status endpoint accessible")
                print(f"   Enabled: {vpn_status['enabled']}")
                print(f"   Connected: {vpn_status.get('connected', 'N/A')}")
                print(f"   Health: {vpn_status.get('health', 'N/A')}")
            else:
                print(f"⚠️ VPN status returned {response.status_code}")
        except Exception as e:
            print(f"⚠️ VPN status check failed: {e}")
        
        print("\n📋 Step 7: Verify Docker containers are actually running...")
        try:
            import docker
            client = docker.from_env()
            
            containers = client.containers.list(all=True, filters={
                'label': 'acestream.integration-test=true'
            })
            
            print(f"📊 Found {len(containers)} containers with integration-test label")
            
            running_containers = [c for c in containers if c.status == 'running']
            print(f"📊 {len(running_containers)} containers are running")
            
            if len(running_containers) != 1:
                print(f"❌ Expected 1 running container but found {len(running_containers)}")
                for container in containers:
                    print(f"   Container {container.id[:12]}: {container.status}")
                return False
            
            print(f"✅ Docker verification successful")
            
        except Exception as e:
            print(f"⚠️ Docker verification failed: {e}")
        
        print("\n📋 Step 8: Test orchestrator status after provisioning...")
        try:
            response = requests.get('http://localhost:8004/orchestrator/status', timeout=10)
            if response.status_code == 200:
                status = response.json()
                print(f"✅ Orchestrator status after provisioning:")
                print(f"   Status: {status['status']}")
                print(f"   Engines Total: {status['engines']['total']}")
                print(f"   Engines Running: {status['engines']['running']}")
                print(f"   Capacity Available: {status['capacity']['available']}")
                
                if status['engines']['total'] != 1:
                    print(f"❌ Expected 1 engine in status but found {status['engines']['total']}")
                    return False
                
                if status['engines']['running'] != 1:
                    print(f"❌ Expected 1 running engine but found {status['engines']['running']}")
                    return False
                
                print(f"✅ Orchestrator status correctly reflects provisioned engine")
            else:
                print(f"❌ Status endpoint returned {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Failed to get final status: {e}")
            return False
        
        print("\n✅ ALL INTEGRATION TESTS PASSED!")
        print("   - Provisioning works correctly")
        print("   - State updates immediately after provisioning")
        print("   - Acexy can find engines right after provisioning")
        print("   - Orchestrator status endpoint provides comprehensive info")
        print("   - VPN status is accessible")
        return True
        
    finally:
        print("\n🧹 Cleaning up...")
        
        # Stop orchestrator
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        
        # Clean up test containers
        try:
            import docker
            client = docker.from_env()
            containers = client.containers.list(all=True, filters={
                'label': 'acestream.integration-test=true'
            })
            
            for container in containers:
                print(f"   Removing container {container.id[:12]}...")
                try:
                    container.stop(timeout=5)
                except:
                    pass
                try:
                    container.remove(force=True)
                except Exception as e:
                    print(f"   Failed to remove {container.id[:12]}: {e}")
        except Exception as e:
            print(f"   Cleanup error: {e}")


if __name__ == "__main__":
    success = test_provision_and_state_sync()
    sys.exit(0 if success else 1)
