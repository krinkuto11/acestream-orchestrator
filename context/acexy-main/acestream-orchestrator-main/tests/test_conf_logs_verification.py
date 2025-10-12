#!/usr/bin/env python3
"""
Test to verify CONF environment variable fix by examining acestream container logs.
This test provisions acestream containers and analyzes their logs to confirm the fix works.
"""

import os
import sys
import time
import subprocess
import requests
import docker
import json
import signal
from datetime import datetime

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_conf_logs_verification():
    """Test CONF fix by examining actual acestream container logs."""
    print("🧪 Testing CONF Environment Variable Fix via Container Logs")
    print("=" * 70)
    
    # Set up test environment
    test_env = os.environ.copy()
    test_env.update({
        'MIN_REPLICAS': '0',  # Don't auto-create containers
        'MAX_REPLICAS': '10',
        'APP_PORT': '8004',
        'TARGET_IMAGE': 'ghcr.io/krinkuto11/acestream-http-proxy:latest',
        'CONTAINER_LABEL': 'conf.test=true',
        'STARTUP_TIMEOUT_S': '30',
        'API_KEY': 'test-conf-fix-123',
        'PORT_RANGE_HOST': '39000-39999',
        'ACE_HTTP_RANGE': '55000-55999',
        'ACE_HTTPS_RANGE': '56000-56999',
        'ACE_MAP_HTTPS': 'false',
        'DOCKER_NETWORK': '',  # Use default network for simplicity
        'DB_URL': 'sqlite:///./test_conf.db'
    })
    
    proc = None
    created_containers = []
    docker_client = None
    
    try:
        # Initialize Docker client
        print(f"\n📋 Step 1: Setting up Docker client...")
        docker_client = docker.from_env()
        print("✅ Docker client initialized")
        
        # Start orchestrator
        print(f"\n📋 Step 2: Starting orchestrator...")
        proc = subprocess.Popen([
            sys.executable, '-m', 'uvicorn',
            'app.main:app',
            '--host', '0.0.0.0',
            '--port', '8004'
        ], env=test_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Wait for orchestrator to start
        print("⏳ Waiting for orchestrator to start...")
        time.sleep(8)
        
        # Check if orchestrator is running
        try:
            response = requests.get('http://localhost:8004/engines', timeout=5)
            if response.status_code == 200:
                print("✅ Orchestrator started successfully")
            else:
                print(f"⚠️ Orchestrator responded with status {response.status_code}")
        except Exception as e:
            print(f"❌ Orchestrator not accessible: {e}")
            return False
        
        # Test 1: Provision acestream container with default CONF
        print(f"\n📋 Step 3: Testing default CONF (no user-provided CONF)...")
        test1_success = test_default_conf(docker_client, created_containers)
        
        # Test 2: Provision acestream container with custom CONF
        print(f"\n📋 Step 4: Testing custom CONF (Docker Compose scenario)...")
        test2_success = test_custom_conf(docker_client, created_containers)
        
        # Overall result
        overall_success = test1_success and test2_success
        print(f"\n🎯 Overall Test Result: {'PASSED' if overall_success else 'FAILED'}")
        
        if overall_success:
            print("✅ CONF environment variable fix is working correctly!")
            print("✅ Container logs confirm proper configuration handling")
        else:
            print("❌ CONF fix verification failed - check logs above")
        
        return overall_success
        
    except Exception as e:
        print(f"❌ Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        return False
        
    finally:
        # Cleanup
        print(f"\n🧹 Cleaning up...")
        
        # Stop orchestrator
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            print("✅ Orchestrator stopped")
        
        # Remove test containers
        if docker_client and created_containers:
            for container_id in created_containers:
                try:
                    container = docker_client.containers.get(container_id)
                    container.stop(timeout=5)
                    container.remove()
                    print(f"✅ Removed container {container_id[:12]}")
                except Exception as e:
                    print(f"⚠️ Failed to remove container {container_id[:12]}: {e}")

def test_default_conf(docker_client, created_containers):
    """Test default CONF generation (no user-provided CONF)."""
    print("🧪 Testing default CONF generation...")
    
    try:
        # Provision acestream container with no CONF specified
        response = requests.post('http://localhost:8004/provision/acestream', 
            headers={'Authorization': 'Bearer test-conf-fix-123'},
            json={
                'env': {},  # No CONF provided
                'labels': {'test': 'default-conf'}
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            container_id = result['container_id']
            created_containers.append(container_id)
            
            print(f"✅ Container created: {container_id[:12]}")
            print(f"   Host HTTP port: {result['host_http_port']}")
            print(f"   Container HTTP port: {result['container_http_port']}")
            print(f"   Container HTTPS port: {result['container_https_port']}")
            
            # Wait a moment for container to start
            time.sleep(5)
            
            # Get container and examine environment
            container = docker_client.containers.get(container_id)
            env_vars = container.attrs['Config']['Env']
            
            # Find CONF environment variable
            conf_env = None
            for env_var in env_vars:
                if env_var.startswith('CONF='):
                    conf_env = env_var[5:]  # Remove 'CONF=' prefix
                    break
            
            if conf_env:
                print(f"✅ Found CONF environment variable:")
                print(f"   CONF = {repr(conf_env)}")
                
                # Verify it contains expected default configuration
                expected_http = f"--http-port={result['container_http_port']}"
                expected_https = f"--https-port={result['container_https_port']}"
                
                if expected_http in conf_env and expected_https in conf_env and "--bind-all" in conf_env:
                    print("✅ Default CONF contains expected configuration")
                    
                    # Verify no duplicates
                    lines = conf_env.split('\n')
                    if len(lines) == 3:  # Should be exactly 3 lines
                        print("✅ No duplicate configuration lines")
                        return True
                    else:
                        print(f"❌ Unexpected number of config lines: {len(lines)}")
                        print(f"   Lines: {lines}")
                        return False
                else:
                    print("❌ Default CONF missing expected configuration")
                    return False
            else:
                print("❌ CONF environment variable not found")
                return False
                
        else:
            print(f"❌ Failed to provision container: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error testing default CONF: {e}")
        return False

def test_custom_conf(docker_client, created_containers):
    """Test custom CONF (Docker Compose scenario)."""
    print("🧪 Testing custom CONF (Docker Compose scenario)...")
    
    try:
        # Custom CONF from Docker Compose example
        custom_conf = "--http-port=6879\n--https-port=6880\n--bind-all"
        
        # Provision acestream container with custom CONF
        response = requests.post('http://localhost:8004/provision/acestream',
            headers={'Authorization': 'Bearer test-conf-fix-123'},
            json={
                'env': {'CONF': custom_conf},
                'labels': {'test': 'custom-conf'}
            },
            timeout=30
        )
        
        if response.status_code == 200:
            result = response.json()
            container_id = result['container_id']
            created_containers.append(container_id)
            
            print(f"✅ Container created: {container_id[:12]}")
            print(f"   Host HTTP port: {result['host_http_port']}")
            
            # Wait a moment for container to start
            time.sleep(5)
            
            # Get container and examine environment
            container = docker_client.containers.get(container_id)
            env_vars = container.attrs['Config']['Env']
            
            # Find CONF environment variable
            conf_env = None
            for env_var in env_vars:
                if env_var.startswith('CONF='):
                    conf_env = env_var[5:]  # Remove 'CONF=' prefix
                    break
            
            if conf_env:
                print(f"✅ Found CONF environment variable:")
                print(f"   CONF = {repr(conf_env)}")
                
                # Verify it matches exactly what we provided
                if conf_env == custom_conf:
                    print("✅ Custom CONF preserved exactly as provided!")
                    
                    # Verify no orchestrator defaults were added
                    if "--http-port=6879" in conf_env and "--https-port=6880" in conf_env:
                        lines = conf_env.split('\n')
                        http_count = sum(1 for line in lines if '--http-port=' in line)
                        https_count = sum(1 for line in lines if '--https-port=' in line)
                        bind_count = sum(1 for line in lines if '--bind-all' in line)
                        
                        if http_count == 1 and https_count == 1 and bind_count == 1:
                            print("✅ No duplicate configuration detected!")
                            print("✅ Custom ports (6879/6880) are being used")
                            return True
                        else:
                            print(f"❌ Duplicate configuration detected!")
                            print(f"   HTTP port entries: {http_count}")
                            print(f"   HTTPS port entries: {https_count}")
                            print(f"   Bind-all entries: {bind_count}")
                            return False
                    else:
                        print("❌ Custom CONF doesn't contain expected ports")
                        return False
                else:
                    print("❌ Custom CONF was modified!")
                    print(f"   Expected: {repr(custom_conf)}")
                    print(f"   Got:      {repr(conf_env)}")
                    return False
            else:
                print("❌ CONF environment variable not found")
                return False
                
        else:
            print(f"❌ Failed to provision container: {response.status_code}")
            print(f"   Response: {response.text}")
            return False
            
    except Exception as e:
        print(f"❌ Error testing custom CONF: {e}")
        return False

if __name__ == "__main__":
    try:
        success = test_conf_logs_verification()
        print(f"\n🎯 Test Result: {'PASSED - CONF fix verified!' if success else 'FAILED - Issues detected!'}")
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)