#!/usr/bin/env python3
"""
Verification test to check acestream-http-proxy container logs to confirm
the port mapping fix works correctly. This test actually starts containers
and examines their logs to verify they bind to the correct ports.
"""

import os
import sys
import time
import subprocess
import requests
import docker
import re
import signal
from datetime import datetime

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_acestream_logs_verification():
    """Test that acestream containers bind to correct ports by examining logs."""
    print("🧪 Verifying AceStream Port Binding via Container Logs")
    print("=" * 70)
    
    # Set up test environment
    test_env = os.environ.copy()
    test_env.update({
        'MIN_REPLICAS': '0',  # Don't auto-create containers
        'MAX_REPLICAS': '10',
        'APP_PORT': '8005',
        'TARGET_IMAGE': 'ghcr.io/krinkuto11/acestream-http-proxy:latest',
        'CONTAINER_LABEL': 'acestream.logs.test=true',
        'STARTUP_TIMEOUT_S': '45',  # Give acestream time to start
        'API_KEY': 'test-logs-verification-123',
        'PORT_RANGE_HOST': '25000-25999',
        'ACE_HTTP_RANGE': '30000-30999',
        'ACE_HTTPS_RANGE': '35000-35999'
    })
    
    docker_client = None
    orchestrator_proc = None
    created_containers = []
    
    try:
        # Initialize Docker client
        print(f"\n📋 Step 1: Setting up Docker client...")
        docker_client = docker.from_env()
        print("✅ Docker client initialized")
        
        # Start orchestrator
        print(f"\n📋 Step 2: Starting orchestrator...")
        orchestrator_proc = subprocess.Popen([
            sys.executable, '-m', 'uvicorn',
            'app.main:app',
            '--host', '0.0.0.0',
            '--port', '8005'
        ], env=test_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Wait for orchestrator to start
        time.sleep(3)
        orchestrator_url = "http://localhost:8005"
        
        # Verify orchestrator is running
        try:
            response = requests.get(f"{orchestrator_url}/engines", timeout=5)
            if response.status_code == 200:
                print("✅ Orchestrator started successfully")
            else:
                print(f"❌ Orchestrator not responding correctly: {response.status_code}")
                return False
        except requests.exceptions.RequestException as e:
            print(f"❌ Failed to connect to orchestrator: {e}")
            return False
        
        # Test 1: Provision container with Docker Compose CONF (6879/6880)
        print(f"\n📋 Step 3: Testing Docker Compose scenario (HTTP=6879, HTTPS=6880)...")
        docker_compose_conf = "--http-port=6879\n--https-port=6880\n--bind-all"
        
        try:
            response = requests.post(f"{orchestrator_url}/provision/acestream", json={
                "env": {"CONF": docker_compose_conf},
                "labels": {"test.scenario": "docker-compose"}
            }, headers={"Authorization": "Bearer test-logs-verification-123"}, timeout=30)
            
            if response.status_code == 200:
                result = response.json()
                container_id = result['container_id']
                created_containers.append(container_id)
                
                print(f"✅ Container provisioned: {container_id[:12]}")
                print(f"   Container HTTP port: {result['container_http_port']}")
                print(f"   Container HTTPS port: {result['container_https_port']}")
                print(f"   Host HTTP port: {result['host_http_port']}")
                
                # Wait for container to start and acestream to initialize
                print(f"\n📋 Step 4: Waiting for acestream to initialize...")
                time.sleep(15)  # Give acestream time to start and log
                
                # Get container and examine logs
                container = docker_client.containers.get(container_id)
                logs = container.logs(stdout=True, stderr=True).decode('utf-8', errors='ignore')
                
                print(f"\n📋 Step 5: Analyzing container logs...")
                print(f"   Container status: {container.status}")
                
                # Look for the specific log line that shows port binding
                # Expected format: "bound on ('0.0.0.0', 6879)"
                bound_pattern = r"bound on \('0\.0\.0\.0', (\d+)\)"
                bound_matches = re.findall(bound_pattern, logs)
                
                if bound_matches:
                    actual_port = int(bound_matches[-1])  # Get the last (most recent) binding
                    print(f"✅ Found port binding in logs: acestream bound to port {actual_port}")
                    
                    if actual_port == 6879:
                        print(f"✅ SUCCESS: Acestream bound to correct port 6879 (matches CONF)")
                        
                        # Verify CONF was set correctly
                        env_vars = container.attrs['Config']['Env']
                        conf_env = None
                        for env_var in env_vars:
                            if env_var.startswith('CONF='):
                                conf_env = env_var[5:]
                                break
                        
                        if conf_env and "6879" in conf_env:
                            print(f"✅ CONF environment variable contains expected port 6879")
                            print(f"   CONF = {repr(conf_env)}")
                        else:
                            print(f"❌ CONF environment variable issue")
                            print(f"   CONF = {repr(conf_env)}")
                        
                        # Show relevant log lines
                        print(f"\n📋 Relevant log lines:")
                        log_lines = logs.split('\n')
                        for line in log_lines:
                            if 'bound on' in line or 'http-port' in line:
                                print(f"   {line}")
                        
                        return True
                        
                    else:
                        print(f"❌ FAIL: Acestream bound to port {actual_port}, expected 6879")
                        print(f"   This indicates the fix is not working correctly")
                        
                        # Show debugging info
                        print(f"\n📋 Debug info:")
                        print(f"   Expected container port: 6879 (from CONF)")
                        print(f"   Actual binding port: {actual_port}")
                        print(f"   Container HTTP port (orchestrator): {result['container_http_port']}")
                        
                        # Show all log lines for debugging
                        print(f"\n📋 Full container logs:")
                        print(logs[-2000:])  # Last 2000 chars
                        
                        return False
                else:
                    print(f"❌ No port binding found in logs")
                    print(f"   This might indicate acestream failed to start properly")
                    
                    # Show logs for debugging
                    print(f"\n📋 Container logs (for debugging):")
                    print(logs[-1500:])  # Last 1500 chars
                    
                    return False
                    
            else:
                print(f"❌ Failed to provision container: {response.status_code}")
                print(f"   Response: {response.text}")
                return False
                
        except requests.exceptions.RequestException as e:
            print(f"❌ Error provisioning container: {e}")
            return False
        
    except Exception as e:
        print(f"❌ Test failed with error: {e}")
        return False
        
    finally:
        # Cleanup
        print(f"\n📋 Cleanup: Removing test containers...")
        
        for container_id in created_containers:
            try:
                if docker_client:
                    container = docker_client.containers.get(container_id)
                    container.stop(timeout=10)
                    container.remove()
                    print(f"✅ Removed container {container_id[:12]}")
            except Exception as e:
                print(f"⚠️ Error removing container {container_id[:12]}: {e}")
        
        # Stop orchestrator
        if orchestrator_proc:
            try:
                orchestrator_proc.terminate()
                orchestrator_proc.wait(timeout=10)
                print(f"✅ Orchestrator stopped")
            except:
                orchestrator_proc.kill()
                print(f"✅ Orchestrator killed")


def main():
    """Main test function."""
    print("🎯 AceStream Port Binding Log Verification")
    print("=" * 70)
    
    print("\n🔍 This test verifies that the port mapping fix works by:")
    print("1. Starting an orchestrator with acestream-http-proxy image")
    print("2. Provisioning container with Docker Compose CONF (--http-port=6879)")
    print("3. Examining container logs for 'bound on' messages")
    print("4. Confirming acestream binds to the correct port (6879)")
    
    success = test_acestream_logs_verification()
    
    print("\n" + "=" * 70)
    
    if success:
        print("✅ LOG VERIFICATION SUCCESSFUL")
        print("\n📋 Summary:")
        print("✅ Acestream container bound to correct port from CONF")
        print("✅ Port mapping fix is working correctly")
        print("✅ Docker Compose scenario validated with real container logs")
        print("\n🎉 The fix resolves the acestream port mismatch issue!")
    else:
        print("❌ LOG VERIFICATION FAILED")
        print("\n🔧 Issues found:")
        print("❌ Acestream container logs show incorrect port binding")
        print("❌ Port mapping fix may not be working correctly")
        print("\n💡 Check the logs above for debugging information")
    
    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)