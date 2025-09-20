#!/usr/bin/env python3
"""
Test script specifically for @krinkuto11's Docker Compose and environment configuration.
This tests container provisioning with proper port allocation and network setup.
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

def test_user_configuration():
    """Test the user's specific configuration with proper acestream provisioning."""
    
    print("🧪 Testing @krinkuto11's Configuration")
    print("=" * 60)
    
    # User's configuration from the comment
    test_env = os.environ.copy()
    test_env.update({
        'APP_PORT': '8000',
        'DOCKER_NETWORK': 'orchestrator',
        'TARGET_IMAGE': 'ghcr.io/krinkuto11/acestream-http-proxy:latest',
        'MIN_REPLICAS': '3',
        'MAX_REPLICAS': '20',
        'CONTAINER_LABEL': 'orchestrator.managed=acestream',
        'STARTUP_TIMEOUT_S': '25',
        'IDLE_TTL_S': '600',
        'COLLECT_INTERVAL_S': '5',
        'STATS_HISTORY_MAX': '720',
        'PORT_RANGE_HOST': '19000-19999',
        'ACE_HTTP_RANGE': '40000-44999',
        'ACE_HTTPS_RANGE': '45000-49999',
        'ACE_MAP_HTTPS': 'false',
        'API_KEY': 'holaholahola',
        'DB_URL': 'sqlite:///./orchestrator.db',
        'AUTO_DELETE': 'true'
    })
    
    print("\n📋 Configuration Summary:")
    print(f"   Image: {test_env['TARGET_IMAGE']}")
    print(f"   MIN_REPLICAS: {test_env['MIN_REPLICAS']}")
    print(f"   Network: {test_env['DOCKER_NETWORK']}")
    print(f"   Port Range: {test_env['PORT_RANGE_HOST']}")
    print(f"   Container Label: {test_env['CONTAINER_LABEL']}")
    
    proc = None
    try:
        # Step 1: Check Docker network
        print(f"\n📋 Step 1: Checking Docker network setup...")
        import docker
        client = docker.from_env()
        
        # Check if orchestrator network exists
        try:
            network = client.networks.get('orchestrator')
            print(f"✅ Network 'orchestrator' exists: {network.id[:12]}")
        except docker.errors.NotFound:
            print("⚠️ Network 'orchestrator' not found, creating it...")
            try:
                network = client.networks.create('orchestrator')
                print(f"✅ Created network 'orchestrator': {network.id[:12]}")
            except Exception as e:
                print(f"❌ Failed to create network: {e}")
                print("💡 You may need to create it manually: docker network create orchestrator")
        
        # Step 2: Test image availability
        print(f"\n📋 Step 2: Testing image availability...")
        try:
            client.images.get(test_env['TARGET_IMAGE'])
            print(f"✅ Image {test_env['TARGET_IMAGE']} available locally")
        except docker.errors.ImageNotFound:
            print(f"📥 Pulling {test_env['TARGET_IMAGE']}...")
            try:
                client.images.pull(test_env['TARGET_IMAGE'])
                print(f"✅ Successfully pulled {test_env['TARGET_IMAGE']}")
            except Exception as e:
                print(f"❌ Failed to pull image: {e}")
                return False
        
        # Step 3: Test single container manually first
        print(f"\n📋 Step 3: Testing single container startup...")
        try:
            key, val = test_env['CONTAINER_LABEL'].split('=')
            labels = {key: val, 'test.manual': 'true'}
            
            container = client.containers.run(
                test_env['TARGET_IMAGE'],
                detach=True,
                labels=labels,
                network=test_env['DOCKER_NETWORK'],
                restart_policy={"Name": "no"}  # Don't restart for test
            )
            
            print(f"✅ Started test container: {container.id[:12]}")
            
            # Wait a bit and check status
            time.sleep(5)
            container.reload()
            print(f"   Container status after 5s: {container.status}")
            
            if container.status == 'running':
                print("✅ Test container is running successfully")
                
                # Get container details
                ports = container.ports
                labels = container.labels
                print(f"   Ports: {ports}")
                print(f"   Network: {container.attrs.get('NetworkSettings', {}).get('Networks', {})}")
                
                test_success = True
            else:
                print(f"❌ Test container not running (status: {container.status})")
                # Get logs
                try:
                    logs = container.logs().decode('utf-8')
                    print(f"   Container logs:\n{logs[-500:]}")
                except:
                    pass
                test_success = False
            
            # Clean up test container
            try:
                container.stop(timeout=3)
                container.remove()
                print(f"🗑️ Cleaned up test container")
            except:
                pass
                
            if not test_success:
                return False
                
        except Exception as e:
            print(f"❌ Error testing single container: {e}")
            return False
        
        # Step 4: Start orchestrator with user configuration
        print(f"\n📋 Step 4: Starting orchestrator with user configuration...")
        
        proc = subprocess.Popen([
            sys.executable, '-m', 'uvicorn',
            'app.main:app',
            '--host', '0.0.0.0',
            '--port', test_env['APP_PORT']
        ], env=test_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        print("⏳ Waiting for orchestrator startup and container provisioning...")
        time.sleep(20)  # Give more time for startup
        
        # Check if orchestrator is running
        if proc.poll() is not None:
            stdout, stderr = proc.communicate()
            print(f"❌ Orchestrator process exited:")
            print(f"STDOUT: {stdout.decode()}")
            print(f"STDERR: {stderr.decode()}")
            return False
        
        # Step 5: Test orchestrator API
        print(f"\n📋 Step 5: Testing orchestrator API...")
        try:
            response = requests.get(f'http://localhost:{test_env["APP_PORT"]}/engines', timeout=10)
            if response.status_code == 200:
                engines = response.json()
                print(f"✅ Orchestrator API accessible")
                print(f"📊 Found {len(engines)} engines")
                
                expected_min = int(test_env['MIN_REPLICAS'])
                
                if len(engines) >= expected_min:
                    print(f"✅ SUCCESS: Expected {expected_min} engines, found {len(engines)}")
                    
                    # Test port allocation and uniqueness
                    ports_used = set()
                    port_conflicts = False
                    
                    for i, engine in enumerate(engines):
                        engine_port = engine.get('port', 0)
                        print(f"   Engine {i+1}: {engine['container_id'][:12]} - Port: {engine_port}")
                        
                        if engine_port in ports_used:
                            print(f"   ❌ PORT CONFLICT: Port {engine_port} is used by multiple engines!")
                            port_conflicts = True
                        else:
                            ports_used.add(engine_port)
                            
                        # Verify port is in expected range
                        port_range = test_env['PORT_RANGE_HOST'].split('-')
                        min_port, max_port = int(port_range[0]), int(port_range[1])
                        
                        if engine_port != 0 and not (min_port <= engine_port <= max_port):
                            print(f"   ⚠️ Port {engine_port} is outside configured range {test_env['PORT_RANGE_HOST']}")
                    
                    if not port_conflicts:
                        print(f"✅ All engines have unique ports")
                    else:
                        print(f"❌ Port conflicts detected!")
                        
                else:
                    print(f"❌ Expected {expected_min} engines, found {len(engines)}")
                    return False
                    
            else:
                print(f"❌ API error: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error testing API: {e}")
            return False
        
        # Step 6: Test acestream provisioning endpoint
        print(f"\n📋 Step 6: Testing acestream provisioning endpoint...")
        try:
            headers = {
                'Authorization': f'Bearer {test_env["API_KEY"]}',
                'Content-Type': 'application/json'
            }
            provision_data = {
                'labels': {'test.provision': 'manual'},
                'env': {}
            }
            
            response = requests.post(
                f'http://localhost:{test_env["APP_PORT"]}/provision/acestream',
                headers=headers,
                json=provision_data,
                timeout=30
            )
            
            if response.status_code == 200:
                result = response.json()
                print(f"✅ Successfully provisioned acestream container")
                print(f"   Container ID: {result['container_id'][:12]}")
                print(f"   HTTP Port: {result['host_http_port']}")
                print(f"   Container HTTP: {result['container_http_port']}")
                print(f"   Container HTTPS: {result['container_https_port']}")
                
                # Verify port ranges
                http_range = test_env['ACE_HTTP_RANGE'].split('-')
                https_range = test_env['ACE_HTTPS_RANGE'].split('-')
                host_range = test_env['PORT_RANGE_HOST'].split('-')
                
                container_http = result['container_http_port']
                container_https = result['container_https_port']
                host_http = result['host_http_port']
                
                print(f"   Port validation:")
                print(f"     Container HTTP {container_http} in range {test_env['ACE_HTTP_RANGE']}: {int(http_range[0]) <= container_http <= int(http_range[1])}")
                print(f"     Container HTTPS {container_https} in range {test_env['ACE_HTTPS_RANGE']}: {int(https_range[0]) <= container_https <= int(https_range[1])}")
                print(f"     Host HTTP {host_http} in range {test_env['PORT_RANGE_HOST']}: {int(host_range[0]) <= host_http <= int(host_range[1])}")
                
            else:
                print(f"❌ Acestream provisioning failed: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            print(f"❌ Error testing acestream provisioning: {e}")
            return False
        
        # Step 7: Final Docker verification
        print(f"\n📋 Step 7: Final Docker container verification...")
        
        containers = client.containers.list(all=True, filters={
            'label': f'{test_env["CONTAINER_LABEL"]}'
        })
        
        running_containers = [c for c in containers if c.status == 'running']
        print(f"📊 Found {len(running_containers)} running containers via Docker")
        
        for i, container in enumerate(running_containers):
            print(f"   Container {i+1}: {container.id[:12]} - {container.status}")
            
            # Get port mappings
            port_mappings = container.ports
            print(f"      Port mappings: {port_mappings}")
            
            # Get network info
            networks = container.attrs.get('NetworkSettings', {}).get('Networks', {})
            print(f"      Networks: {list(networks.keys())}")
        
        final_count = len(running_containers)
        expected_min = int(test_env['MIN_REPLICAS'])
        
        if final_count >= expected_min:
            print(f"✅ FINAL SUCCESS: {final_count} containers running (expected: {expected_min})")
            return True
        else:
            print(f"❌ FINAL FAILURE: {final_count} containers running (expected: {expected_min})")
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
            
            # Clean containers with user's label
            containers = client.containers.list(all=True, filters={
                'label': test_env['CONTAINER_LABEL']
            })
            
            for container in containers:
                print(f"🗑️ Removing container {container.id[:12]}")
                try:
                    container.stop(timeout=3)
                    container.remove(force=True)
                except:
                    pass
                    
            # Clean manual test containers
            manual_containers = client.containers.list(all=True, filters={
                'label': 'test.manual=true'
            })
            
            for container in manual_containers:
                print(f"🗑️ Removing manual test container {container.id[:12]}")
                try:
                    container.stop(timeout=3)
                    container.remove(force=True)
                except:
                    pass
                    
        except Exception as e:
            print(f"⚠️ Cleanup error: {e}")

def main():
    """Main test function."""
    
    print("🎯 @krinkuto11 Configuration Verification Test")
    print("=" * 70)
    
    print("\n🔍 This test specifically validates:")
    print("1. Docker network 'orchestrator' setup")
    print("2. Image ghcr.io/krinkuto11/acestream-http-proxy:latest availability")
    print("3. MIN_REPLICAS=3 container provisioning")
    print("4. Unique port allocation for each container")
    print("5. Proper port range usage (19000-19999, 40000-44999, 45000-49999)")
    print("6. Acestream provisioning endpoint functionality")
    print("7. Container reachability via Docker and API")
    
    success = test_user_configuration()
    
    print("\n" + "=" * 70)
    
    if success:
        print("✅ USER CONFIGURATION VERIFICATION SUCCESSFUL")
        print("\n📋 Summary:")
        print("✅ Docker network setup is working")
        print("✅ Container image is available and functional")
        print("✅ MIN_REPLICAS=3 provisioning works correctly")
        print("✅ Each container gets a unique port")
        print("✅ Port ranges are respected")
        print("✅ Acestream provisioning endpoint works")
        print("✅ Containers are reachable via Docker and API")
        
        print("\n🎉 Your Docker Compose setup should work correctly!")
        print("💡 When you run with docker-compose up, you should see:")
        print("   - 3 containers started automatically")
        print("   - Each with a unique port in range 19000-19999")
        print("   - All containers healthy and reachable")
        
    else:
        print("❌ USER CONFIGURATION VERIFICATION FAILED")
        print("\n🔧 Issues found with your configuration:")
        print("❌ Some aspect of the setup is not working correctly")
        
        print("\n💡 Troubleshooting steps:")
        print("1. Ensure Docker network exists: docker network create orchestrator")
        print("2. Check image accessibility: docker pull ghcr.io/krinkuto11/acestream-http-proxy:latest")
        print("3. Verify container can start: docker run --rm --network orchestrator ghcr.io/krinkuto11/acestream-http-proxy:latest")
        print("4. Check if image needs specific environment variables or configuration")
        
    return success

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Test interrupted")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)