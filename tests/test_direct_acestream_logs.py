#!/usr/bin/env python3
"""
Direct verification test to check acestream-http-proxy container logs.
This test directly starts acestream containers with specific CONF and examines
their logs to verify they bind to the correct ports.
"""

import os
import sys
import time
import docker
import re
from datetime import datetime

def test_direct_acestream_logs():
    """Test acestream logs directly by creating containers with specific CONF."""
    print("🧪 Direct AceStream Container Log Verification")
    print("=" * 70)
    
    docker_client = None
    created_containers = []
    
    try:
        # Initialize Docker client
        print(f"\n📋 Step 1: Setting up Docker client...")
        docker_client = docker.from_env()
        print("✅ Docker client initialized")
        
        # Test 1: Docker Compose scenario (HTTP=6879, HTTPS=6880)
        print(f"\n📋 Step 2: Testing Docker Compose scenario...")
        docker_compose_conf = "--http-port=6879\n--https-port=6880\n--bind-all"
        
        print(f"   Creating container with CONF:")
        print(f"   {repr(docker_compose_conf)}")
        
        try:
            # Create acestream container with the specific CONF
            container = docker_client.containers.run(
                'ghcr.io/krinkuto11/acestream-http-proxy:latest',
                detach=True,
                environment={'CONF': docker_compose_conf},
                ports={'6879/tcp': 6879, '6880/tcp': 6880},  # Port mapping that should work
                labels={'test': 'acestream-logs-verification'},
                remove=False
            )
            
            created_containers.append(container.id)
            print(f"✅ Container created: {container.id[:12]}")
            
            # Wait for acestream to start and initialize
            print(f"\n📋 Step 3: Waiting for acestream to initialize...")
            time.sleep(20)  # Give acestream time to start and log
            
            # Reload container to get latest status
            container.reload()
            print(f"   Container status: {container.status}")
            
            # Get container logs
            print(f"\n📋 Step 4: Examining container logs...")
            logs = container.logs(stdout=True, stderr=True).decode('utf-8', errors='ignore')
            
            print(f"   Log length: {len(logs)} characters")
            
            # Look for the specific log line that shows port binding
            # Expected format: "bound on ('0.0.0.0', 6879)"
            bound_pattern = r"bound on \('0\.0\.0\.0', (\d+)\)"
            bound_matches = re.findall(bound_pattern, logs)
            
            if bound_matches:
                actual_port = int(bound_matches[-1])  # Get the last (most recent) binding
                print(f"✅ Found port binding in logs:")
                print(f"   Acestream HTTP server bound to port: {actual_port}")
                
                if actual_port == 6879:
                    print(f"✅ SUCCESS: Port matches expected CONF value!")
                    
                    # Show the relevant log lines
                    print(f"\n📋 Relevant log lines showing port binding:")
                    log_lines = logs.split('\n')
                    for line in log_lines:
                        if 'bound on' in line:
                            print(f"   {line}")
                    
                    # Also show CONF-related logs
                    print(f"\n📋 CONF-related log lines:")
                    for line in log_lines:
                        if 'http-port' in line.lower() or 'conf' in line.lower():
                            print(f"   {line}")
                    
                    return True
                    
                else:
                    print(f"❌ FAIL: Port mismatch!")
                    print(f"   Expected: 6879 (from CONF)")
                    print(f"   Actual: {actual_port}")
                    print(f"   This indicates the fix needs further work")
                    
                    # Show debugging info
                    print(f"\n📋 All port binding log lines:")
                    log_lines = logs.split('\n')
                    for line in log_lines:
                        if 'bound on' in line:
                            print(f"   {line}")
                    
                    return False
            else:
                print(f"❌ No 'bound on' messages found in logs")
                print(f"   This might indicate acestream failed to start properly")
                
                # Show logs for debugging
                print(f"\n📋 Container logs (last 2000 chars):")
                print(logs[-2000:])
                
                return False
                
        except docker.errors.DockerException as e:
            print(f"❌ Docker error: {e}")
            return False
        except Exception as e:
            print(f"❌ Error: {e}")
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


def test_different_port_scenario():
    """Test with a different port to verify the fix works for various ports."""
    print("\n🧪 Testing Different Port Scenario (HTTP=8080)")
    print("=" * 50)
    
    docker_client = None
    created_containers = []
    
    try:
        docker_client = docker.from_env()
        
        # Test with HTTP port 8080
        custom_conf = "--http-port=8080\n--bind-all"
        
        print(f"   Creating container with CONF:")
        print(f"   {repr(custom_conf)}")
        
        container = docker_client.containers.run(
            'ghcr.io/krinkuto11/acestream-http-proxy:latest',
            detach=True,
            environment={'CONF': custom_conf},
            ports={'8080/tcp': 8080},
            labels={'test': 'acestream-logs-verification-8080'},
            remove=False
        )
        
        created_containers.append(container.id)
        print(f"✅ Container created: {container.id[:12]}")
        
        # Wait for acestream to start
        time.sleep(20)
        
        # Get logs and check binding
        logs = container.logs(stdout=True, stderr=True).decode('utf-8', errors='ignore')
        
        bound_pattern = r"bound on \('0\.0\.0\.0', (\d+)\)"
        bound_matches = re.findall(bound_pattern, logs)
        
        if bound_matches:
            actual_port = int(bound_matches[-1])
            if actual_port == 8080:
                print(f"✅ SUCCESS: Port 8080 scenario works correctly!")
                return True
            else:
                print(f"❌ FAIL: Expected 8080, got {actual_port}")
                return False
        else:
            print(f"❌ No port binding found in logs")
            return False
            
    except Exception as e:
        print(f"❌ Error: {e}")
        return False
        
    finally:
        for container_id in created_containers:
            try:
                if docker_client:
                    container = docker_client.containers.get(container_id)
                    container.stop(timeout=10)
                    container.remove()
            except:
                pass


def main():
    """Main test function."""
    print("🎯 AceStream HTTP Proxy Log Verification")
    print("=" * 70)
    
    print("\n🔍 This test verifies the port mapping fix by:")
    print("1. Creating acestream-http-proxy containers with specific CONF")
    print("2. Examining container logs for 'bound on' messages")
    print("3. Confirming acestream binds to the ports specified in CONF")
    print("4. Testing the exact Docker Compose scenario from the issue")
    
    # Test the main Docker Compose scenario
    success1 = test_direct_acestream_logs()
    
    # Test with a different port for additional verification
    success2 = test_different_port_scenario()
    
    print("\n" + "=" * 70)
    
    if success1 and success2:
        print("✅ LOG VERIFICATION SUCCESSFUL")
        print("\n📋 Summary:")
        print("✅ AceStream containers bind to correct ports from CONF")
        print("✅ Docker Compose scenario (6879/6880) works correctly")
        print("✅ Alternative port scenario (8080) also works")
        print("✅ The port mapping fix is working as expected")
        print("\n🎉 The acestream-http-proxy logs confirm the fix resolves the issue!")
    else:
        print("❌ LOG VERIFICATION FAILED")
        print("\n🔧 Issues found:")
        if not success1:
            print("❌ Docker Compose scenario (6879/6880) failed")
        if not success2:
            print("❌ Alternative port scenario (8080) failed")
        print("\n💡 Check the logs above for debugging information")
    
    return success1 and success2


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)