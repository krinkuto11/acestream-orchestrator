#!/usr/bin/env python3
"""
Test to verify that MIN_REPLICAS containers are properly provisioned as AceStream-ready containers
with the correct image and port configuration from .env.example.
"""

import os
import sys
import time
import subprocess
import requests
from datetime import datetime

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def test_acestream_min_replicas():
    """Test that MIN_REPLICAS creates AceStream-ready containers with proper ports and image."""
    
    print("🧪 Testing AceStream MIN_REPLICAS provisioning...")
    print("=" * 70)
    
    # Use configuration from .env.example 
    test_env = os.environ.copy()
    test_env.update({
        'MIN_REPLICAS': '3',
        'MAX_REPLICAS': '10',
        'APP_PORT': '8002',  # Use different port to avoid conflicts
        'TARGET_IMAGE': 'ghcr.io/krinkuto11/acestream-http-proxy:latest',  # Image from .env.example
        'CONTAINER_LABEL': 'test.acestream=true',
        'STARTUP_TIMEOUT_S': '25',
        'API_KEY': 'test-acestream-key',
        'DOCKER_NETWORK': 'orchestrator',
        'PORT_RANGE_HOST': '19000-19999',
        'ACE_HTTP_RANGE': '40000-44999',
        'ACE_HTTPS_RANGE': '45000-49999',
        'ACE_MAP_HTTPS': 'false'
    })
    
    print(f"📋 Test Configuration:")
    print(f"   Target Image: {test_env['TARGET_IMAGE']}")
    print(f"   MIN_REPLICAS: {test_env['MIN_REPLICAS']}")
    print(f"   Docker Network: {test_env['DOCKER_NETWORK']}")
    print(f"   Expected AceStream containers: 3")
    
    proc = None
    try:
        # Ensure docker network exists
        import docker
        client = docker.from_env()
        try:
            client.networks.get('orchestrator')
            print("✅ Docker network 'orchestrator' exists")
        except docker.errors.NotFound:
            print("⚠️ Creating Docker network 'orchestrator'...")
            client.networks.create('orchestrator')
            print("✅ Created Docker network 'orchestrator'")
        
        # Start orchestrator in background
        print(f"\n📋 Step 1: Starting orchestrator with AceStream MIN_REPLICAS=3...")
        
        proc = subprocess.Popen([
            sys.executable, '-m', 'uvicorn', 
            'app.main:app', 
            '--host', '0.0.0.0', 
            '--port', '8002'
        ], env=test_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Wait for startup and container provisioning
        print("⏳ Waiting for orchestrator startup and AceStream container provisioning...")
        time.sleep(30)  # Give enough time for AceStream containers to start
        
        # Check if orchestrator is still running
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            print(f"❌ Orchestrator process exited early")
            print(f"STDOUT: {stdout.decode()}")
            print(f"STDERR: {stderr.decode()}")
            return False
        
        # Test orchestrator API connectivity
        print(f"\n📋 Step 2: Testing orchestrator API and AceStream container validation...")
        try:
            response = requests.get('http://localhost:8002/engines', timeout=10)
            if response.status_code == 200:
                engines = response.json()
                print(f"✅ Orchestrator API accessible")
                print(f"📊 Found {len(engines)} engines")
                
                # Verify we have the expected number of engines
                expected_count = int(test_env['MIN_REPLICAS'])
                if len(engines) != expected_count:
                    print(f"❌ Expected {expected_count} engines, found {len(engines)}")
                    return False
                
                print(f"✅ SUCCESS: Expected {expected_count} engines, found {len(engines)}")
                
                # Validate each engine has proper port configuration
                used_ports = set()
                all_have_ports = True
                
                for i, engine in enumerate(engines):
                    port = engine.get('port', 0)
                    container_id = engine.get('container_id', 'unknown')
                    print(f"   Engine {i+1}: {container_id[:12]} - Port: {port}")
                    
                    if port == 0:
                        print(f"   ❌ Engine {i+1} has no port assigned!")
                        all_have_ports = False
                    elif port in used_ports:
                        print(f"   ❌ Port {port} is used by multiple engines!")
                        all_have_ports = False
                    else:
                        used_ports.add(port)
                        # Validate port is in expected range
                        if 19000 <= port <= 19999:
                            print(f"   ✅ Port {port} is in valid range (19000-19999)")
                        else:
                            print(f"   ❌ Port {port} is outside expected range (19000-19999)")
                            all_have_ports = False
                
                if not all_have_ports:
                    print("❌ Not all engines have properly assigned ports!")
                    return False
                
                print("✅ All engines have unique, valid ports")
                
            else:
                print(f"❌ API returned unexpected status {response.status_code}")
                return False
                
        except Exception as e:
            print(f"❌ Failed to connect to orchestrator API: {e}")
            return False
        
        # Verify containers via Docker
        print(f"\n📋 Step 3: Verifying containers via Docker...")
        try:
            containers = client.containers.list(all=True, filters={
                'label': 'test.acestream=true'
            })
            
            print(f"📊 Found {len(containers)} containers via Docker")
            
            acestream_containers = 0
            for i, container in enumerate(containers):
                status = container.status
                ports = container.ports
                image = container.image.tags[0] if container.image.tags else 'unknown'
                
                print(f"   Container {i+1}: {container.id[:12]} - {status}")
                print(f"      Image: {image}")
                print(f"      Ports: {ports}")
                
                # Check if this is an AceStream container (has proper labels indicating AceStream setup)
                labels = container.labels or {}
                has_acestream_http = 'acestream.http_port' in labels
                has_host_http = 'host.http_port' in labels
                
                # For containers that are still starting, check labels instead of ports
                if has_acestream_http and has_host_http:
                    acestream_containers += 1
                    print(f"      ✅ AceStream-ready container (has AceStream labels)")
                    print(f"         HTTP port mapping: {labels.get('acestream.http_port', 'unknown')} -> {labels.get('host.http_port', 'unknown')}")
                    
                    # Verify image is correct
                    if test_env['TARGET_IMAGE'] in image:
                        print(f"      ✅ Using correct image: {test_env['TARGET_IMAGE']}")
                    else:
                        print(f"      ❌ Wrong image! Expected: {test_env['TARGET_IMAGE']}, Found: {image}")
                        return False
                elif any('40000' <= port.split('/')[0] <= '44999' for port in ports.keys() if '/' in port):
                    # Fallback: check if it has proper port mapping (for running containers)
                    acestream_containers += 1
                    print(f"      ✅ AceStream-ready container (has HTTP port mapping)")
                    
                    # Verify image is correct
                    if test_env['TARGET_IMAGE'] in image:
                        print(f"      ✅ Using correct image: {test_env['TARGET_IMAGE']}")
                    else:
                        print(f"      ❌ Wrong image! Expected: {test_env['TARGET_IMAGE']}, Found: {image}")
                        return False
                else:
                    print(f"      ❌ Not AceStream-ready (no AceStream labels or port mapping)")
            
            expected_acestream = int(test_env['MIN_REPLICAS'])
            if acestream_containers == expected_acestream:
                print(f"✅ SUCCESS: All {acestream_containers} containers are AceStream-ready")
            else:
                print(f"❌ Expected {expected_acestream} AceStream-ready containers, found {acestream_containers}")
                return False
                
        except Exception as e:
            print(f"❌ Error checking containers via Docker: {e}")
            return False
        
        print(f"\n🎉 SUCCESS: MIN_REPLICAS correctly provisions AceStream-ready containers!")
        return True
        
    finally:
        # Cleanup
        print(f"\n🧹 Cleaning up...")
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        
        # Remove test containers
        try:
            import docker
            client = docker.from_env()
            containers = client.containers.list(all=True, filters={
                'label': 'test.acestream=true'
            })
            for container in containers:
                try:
                    container.remove(force=True)
                    print(f"🗑️ Removed container {container.id[:12]}")
                except Exception as e:
                    print(f"⚠️ Failed to remove container {container.id[:12]}: {e}")
        except Exception as e:
            print(f"⚠️ Error during cleanup: {e}")

if __name__ == "__main__":
    try:
        success = test_acestream_min_replicas()
        print(f"\n🎯 Test result: {'✅ PASSED' if success else '❌ FAILED'}")
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Test interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)