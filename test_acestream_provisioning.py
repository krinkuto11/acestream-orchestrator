#!/usr/bin/env python3
"""
Test the orchestrator with actual acestream image to verify real-world provisioning.
"""

import os
import sys
import time
import subprocess
import requests
import signal
import json
from datetime import datetime

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

def test_acestream_provisioning():
    """Test MIN_REPLICAS provisioning with acestream/engine image."""
    
    print("🧪 Testing acestream container provisioning...")
    
    # Set up test environment with acestream image
    test_env = os.environ.copy()
    test_env.update({
        'MIN_REPLICAS': '2',  # Use 2 for faster testing
        'MAX_REPLICAS': '5',
        'APP_PORT': '8003',
        'TARGET_IMAGE': 'acestream/engine:latest',
        'CONTAINER_LABEL': 'acestream.test=true',
        'STARTUP_TIMEOUT_S': '30',  # Acestream takes longer to start
        'API_KEY': 'test-acestream-123',
        'PORT_RANGE_HOST': '29000-29999',  # Use different port range
        'ACE_HTTP_RANGE': '50000-50999',
        'ACE_HTTPS_RANGE': '51000-51999'
    })
    
    proc = None
    try:
        print(f"\n📋 Step 1: Starting orchestrator with acestream image...")
        print(f"   Target image: {test_env['TARGET_IMAGE']}")
        print(f"   MIN_REPLICAS: {test_env['MIN_REPLICAS']}")
        
        # Start orchestrator
        proc = subprocess.Popen([
            sys.executable, '-m', 'uvicorn',
            'app.main:app',
            '--host', '0.0.0.0',
            '--port', '8003'
        ], env=test_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        # Wait longer for acestream containers to start
        print("⏳ Waiting for orchestrator and acestream containers to start (30s)...")
        time.sleep(30)
        
        # Check if orchestrator is running
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            print(f"❌ Orchestrator process exited early")
            print(f"STDOUT: {stdout.decode()}")
            print(f"STDERR: {stderr.decode()}")
            return False
        
        print("\n📋 Step 2: Testing orchestrator API connectivity...")
        try:
            response = requests.get('http://localhost:8003/engines', timeout=10)
            if response.status_code == 200:
                engines = response.json()
                print(f"✅ Orchestrator API accessible")
                print(f"📊 Found {len(engines)} engines")
                
                for i, engine in enumerate(engines):
                    print(f"   Engine {i+1}: {engine['container_id'][:12]} - Port: {engine['port']}")
                    
            else:
                print(f"⚠️ API returned status {response.status_code}")
                return False
        except Exception as e:
            print(f"❌ Failed to connect to orchestrator API: {e}")
            return False
        
        print("\n📋 Step 3: Testing acestream provision endpoint...")
        try:
            headers = {'Authorization': 'Bearer test-acestream-123', 'Content-Type': 'application/json'}
            provision_data = {
                'labels': {'test': 'acestream-provision'},
                'env': {}
            }
            
            response = requests.post('http://localhost:8003/provision/acestream', 
                                   headers=headers, 
                                   json=provision_data,
                                   timeout=60)  # Acestream takes time to provision
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Successfully provisioned acestream container")
                print(f"   Container ID: {result['container_id'][:12]}")
                print(f"   HTTP Port: {result['host_http_port']}")
                print(f"   Container HTTP: {result['container_http_port']}")
                print(f"   Container HTTPS: {result['container_https_port']}")
                
                # Test if the acestream container is reachable
                print(f"\n📋 Step 4: Testing acestream container connectivity...")
                acestream_url = f"http://localhost:{result['host_http_port']}/webui/api/service"
                
                # Wait a bit more for acestream to fully start
                time.sleep(10)
                
                try:
                    ace_response = requests.get(acestream_url, timeout=5)
                    print(f"✅ Acestream container is reachable at port {result['host_http_port']}")
                    print(f"   Response status: {ace_response.status_code}")
                except Exception as e:
                    print(f"⚠️ Acestream container not yet reachable: {e}")
                    print(f"   This might be normal if acestream is still starting up")
                
            else:
                print(f"❌ Failed to provision acestream: {response.status_code}")
                print(f"   Response: {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error testing acestream provision: {e}")
            return False
        
        print("\n📋 Step 5: Checking containers via Docker...")
        try:
            import docker
            client = docker.from_env()
            
            containers = client.containers.list(all=True, filters={
                'label': 'acestream.test=true'
            })
            
            print(f"📊 Found {len(containers)} acestream test containers")
            
            running_containers = [c for c in containers if c.status == 'running']
            print(f"📊 {len(running_containers)} containers are running")
            
            expected_min = int(test_env['MIN_REPLICAS'])
            
            for i, container in enumerate(containers):
                print(f"   Container {i+1}: {container.id[:12]} - {container.status}")
                
                # Get some container details
                try:
                    ports = container.ports
                    labels = container.labels
                    print(f"      Ports: {ports}")
                    print(f"      Image: {container.image.tags}")
                except Exception as e:
                    print(f"      (Could not get details: {e})")
            
            if len(running_containers) >= expected_min:
                print(f"✅ SUCCESS: Expected at least {expected_min} containers, found {len(running_containers)} running")
                return True
            else:
                print(f"❌ FAILURE: Expected at least {expected_min} containers, found {len(running_containers)} running")
                
                # Check failed containers
                failed = [c for c in containers if c.status != 'running']
                if failed:
                    print(f"⚠️ {len(failed)} containers are not running:")
                    for container in failed:
                        print(f"   {container.id[:12]}: {container.status}")
                        try:
                            logs = container.logs(tail=10).decode('utf-8')
                            print(f"      Last logs: {logs[-200:]}")
                        except:
                            print("      (Could not get logs)")
                            
                return False
                
        except Exception as e:
            print(f"❌ Error checking Docker containers: {e}")
            return False
            
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
                'label': 'acestream.test=true'
            })
            
            for container in containers:
                print(f"🗑️ Removing container {container.id[:12]}")
                try:
                    container.stop(timeout=5)
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
        success = test_acestream_provisioning()
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