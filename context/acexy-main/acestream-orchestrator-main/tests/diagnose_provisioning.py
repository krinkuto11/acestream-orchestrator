#!/usr/bin/env python3
"""
Diagnostic script to help troubleshoot container provisioning issues
when MIN_REPLICAS > 0 but containers aren't being started.
"""

import os
import sys
import time
import subprocess
import docker
import json
from datetime import datetime

# Add app to path for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'app'))

def check_docker_connectivity():
    """Check if Docker daemon is accessible."""
    print("🐳 Checking Docker connectivity...")
    try:
        client = docker.from_env()
        client.ping()
        info = client.info()
        print(f"✅ Docker daemon accessible")
        print(f"   Version: {info.get('ServerVersion', 'unknown')}")
        print(f"   Containers: {info.get('Containers', 0)} total, {info.get('ContainersRunning', 0)} running")
        return True, client
    except Exception as e:
        print(f"❌ Docker daemon not accessible: {e}")
        print("   Make sure Docker is installed and running")
        print("   Try: sudo systemctl start docker")
        return False, None

def check_image_availability(client, image_name):
    """Check if the target image is available."""
    print(f"🖼️ Checking image availability: {image_name}")
    try:
        client.images.get(image_name)
        print(f"✅ Image {image_name} is available locally")
        return True
    except docker.errors.ImageNotFound:
        print(f"⚠️ Image {image_name} not found locally, will need to pull")
        try:
            print(f"📥 Attempting to pull {image_name}...")
            client.images.pull(image_name)
            print(f"✅ Successfully pulled {image_name}")
            return True
        except Exception as e:
            print(f"❌ Failed to pull image {image_name}: {e}")
            return False

def check_configuration():
    """Check orchestrator configuration."""
    print("⚙️ Checking orchestrator configuration...")
    
    # Import configuration
    try:
        from app.core.config import cfg
        print("✅ Configuration loaded successfully")
        
        # Check key settings
        print(f"   MIN_REPLICAS: {cfg.MIN_REPLICAS}")
        print(f"   MAX_REPLICAS: {cfg.MAX_REPLICAS}")
        print(f"   TARGET_IMAGE: {cfg.TARGET_IMAGE}")
        print(f"   CONTAINER_LABEL: {cfg.CONTAINER_LABEL}")
        print(f"   STARTUP_TIMEOUT_S: {cfg.STARTUP_TIMEOUT_S}")
        print(f"   DOCKER_NETWORK: {cfg.DOCKER_NETWORK}")
        
        # Validate configuration
        issues = []
        if cfg.MIN_REPLICAS < 0:
            issues.append("MIN_REPLICAS cannot be negative")
        if cfg.MAX_REPLICAS <= 0:
            issues.append("MAX_REPLICAS must be positive")
        if cfg.MIN_REPLICAS > cfg.MAX_REPLICAS:
            issues.append("MIN_REPLICAS cannot be greater than MAX_REPLICAS")
        if '=' not in cfg.CONTAINER_LABEL:
            issues.append("CONTAINER_LABEL must be in key=value format")
            
        if issues:
            print("❌ Configuration issues found:")
            for issue in issues:
                print(f"   - {issue}")
            return False
        else:
            print("✅ Configuration appears valid")
            return True
            
    except Exception as e:
        print(f"❌ Failed to load configuration: {e}")
        return False

def check_existing_containers(client, container_label):
    """Check for existing managed containers."""
    print("📦 Checking existing managed containers...")
    
    try:
        key, val = container_label.split("=")
        containers = client.containers.list(all=True, filters={'label': f'{key}={val}'})
        
        print(f"📊 Found {len(containers)} containers with label {container_label}")
        
        if containers:
            running = [c for c in containers if c.status == 'running']
            stopped = [c for c in containers if c.status != 'running']
            
            print(f"   Running: {len(running)}")
            print(f"   Stopped/Failed: {len(stopped)}")
            
            for i, container in enumerate(containers[:5]):  # Show first 5
                print(f"   Container {i+1}: {container.id[:12]} - {container.status} - {container.image.tags}")
                
            if stopped:
                print("⚠️ Some containers are not running. Checking logs for first stopped container...")
                try:
                    logs = stopped[0].logs(tail=20).decode('utf-8')
                    print(f"   Last 20 lines of logs for {stopped[0].id[:12]}:")
                    for line in logs.split('\n')[-10:]:  # Show last 10 lines
                        if line.strip():
                            print(f"      {line}")
                except Exception as e:
                    print(f"   Could not retrieve logs: {e}")
        
        return containers
        
    except Exception as e:
        print(f"❌ Error checking containers: {e}")
        return []

def test_container_startup(client, image, container_label, timeout=30):
    """Test if we can start a container successfully."""
    print(f"🧪 Testing container startup with image: {image}")
    
    try:
        key, val = container_label.split("=")
        labels = {key: val, 'diagnostic.test': 'true'}
        
        print(f"   Starting test container...")
        container = client.containers.run(
            image,
            detach=True,
            labels=labels,
            restart_policy={"Name": "no"}  # Don't restart for test
        )
        
        # Wait for startup
        start_time = time.time()
        while time.time() - start_time < timeout:
            container.reload()
            if container.status == 'running':
                print(f"✅ Test container started successfully: {container.id[:12]}")
                
                # Clean up
                container.stop(timeout=5)
                container.remove()
                return True
            elif container.status in ['exited', 'dead']:
                break
            time.sleep(1)
        
        container.reload()
        print(f"❌ Test container failed to start properly: status = {container.status}")
        
        # Get logs
        try:
            logs = container.logs().decode('utf-8')
            print(f"   Container logs:")
            for line in logs.split('\n')[-10:]:
                if line.strip():
                    print(f"      {line}")
        except:
            pass
            
        # Clean up
        try:
            container.remove(force=True)
        except:
            pass
            
        return False
        
    except Exception as e:
        print(f"❌ Error testing container startup: {e}")
        return False

def test_autoscaler_functionality():
    """Test the autoscaler logic in isolation."""
    print("🔧 Testing autoscaler functionality...")
    
    try:
        from app.services.autoscaler import ensure_minimum
        from app.services.health import list_managed
        from app.core.config import cfg
        
        print(f"   MIN_REPLICAS setting: {cfg.MIN_REPLICAS}")
        
        if cfg.MIN_REPLICAS == 0:
            print("⚠️ MIN_REPLICAS is 0, no containers should be started")
            return True
            
        # Check current managed containers
        current = list_managed()
        running = [c for c in current if c.status == "running"]
        print(f"   Currently running managed containers: {len(running)}")
        
        deficit = cfg.MIN_REPLICAS - len(running)
        print(f"   Deficit (should start): {max(deficit, 0)} containers")
        
        if deficit > 0:
            print(f"   🚀 Calling ensure_minimum() to start {deficit} containers...")
            
            # Before count
            before_containers = list_managed()
            before_running = [c for c in before_containers if c.status == "running"]
            
            # Call autoscaler
            ensure_minimum()
            
            # Wait a bit for containers to start
            time.sleep(5)
            
            # After count
            after_containers = list_managed()
            after_running = [c for c in after_containers if c.status == "running"]
            
            print(f"   Before: {len(before_running)} running containers")
            print(f"   After: {len(after_running)} running containers")
            print(f"   Difference: +{len(after_running) - len(before_running)} containers")
            
            if len(after_running) >= cfg.MIN_REPLICAS:
                print("✅ Autoscaler appears to be working correctly")
                return True
            else:
                print("❌ Autoscaler did not start enough containers")
                return False
        else:
            print("✅ No deficit, autoscaler working as expected")
            return True
            
    except Exception as e:
        print(f"❌ Error testing autoscaler: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    """Run all diagnostic checks."""
    print("🔍 Orchestrator Container Provisioning Diagnostics")
    print("=" * 60)
    
    all_checks_passed = True
    
    # Check 1: Docker connectivity
    docker_ok, client = check_docker_connectivity()
    if not docker_ok:
        return False
    all_checks_passed &= docker_ok
    
    print("\n" + "-" * 60)
    
    # Check 2: Configuration
    config_ok = check_configuration()
    all_checks_passed &= config_ok
    
    if not config_ok:
        return False
        
    print("\n" + "-" * 60)
    
    # Load config for further checks
    from app.core.config import cfg
    
    # Check 3: Image availability
    image_ok = check_image_availability(client, cfg.TARGET_IMAGE)
    all_checks_passed &= image_ok
    
    print("\n" + "-" * 60)
    
    # Check 4: Existing containers
    containers = check_existing_containers(client, cfg.CONTAINER_LABEL)
    
    print("\n" + "-" * 60)
    
    # Check 5: Container startup test
    startup_ok = test_container_startup(client, cfg.TARGET_IMAGE, cfg.CONTAINER_LABEL)
    all_checks_passed &= startup_ok
    
    print("\n" + "-" * 60)
    
    # Check 6: Autoscaler functionality
    autoscaler_ok = test_autoscaler_functionality()
    all_checks_passed &= autoscaler_ok
    
    print("\n" + "=" * 60)
    
    if all_checks_passed:
        print("✅ ALL CHECKS PASSED - Container provisioning should work correctly")
        print("\nIf you're still having issues:")
        print("1. Check if the orchestrator startup logs show any errors")
        print("2. Verify that ensure_minimum() is being called during startup")
        print("3. Check if there are any firewall or security restrictions")
    else:
        print("❌ SOME CHECKS FAILED - Please address the issues above")
        print("\nCommon solutions:")
        print("1. Ensure Docker daemon is running: sudo systemctl start docker")
        print("2. Pull the target image manually: docker pull <TARGET_IMAGE>")
        print("3. Check .env file configuration")
        print("4. Verify user has Docker permissions: sudo usermod -aG docker $USER")
    
    return all_checks_passed

if __name__ == "__main__":
    try:
        success = main()
        sys.exit(0 if success else 1)
    except KeyboardInterrupt:
        print("\n⏹️ Diagnostics interrupted by user")
        sys.exit(1)
    except Exception as e:
        print(f"\n💥 Diagnostics failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)