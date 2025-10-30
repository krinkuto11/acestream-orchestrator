"""
Manual test script to verify forwarded engine functionality.

This script demonstrates how the forwarded engine feature works.
Run this after starting the orchestrator with Gluetun enabled.
"""

import sys
import os
import requests
import json
from datetime import datetime

# Configuration
ORCHESTRATOR_URL = os.getenv("ORCHESTRATOR_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "holaholahola")

headers = {"X-API-Key": API_KEY}

def print_section(title):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def check_vpn_status():
    """Check VPN status."""
    print_section("VPN Status")
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/vpn/status", headers=headers)
        response.raise_for_status()
        vpn_status = response.json()
        
        print(f"VPN Enabled: {vpn_status.get('enabled')}")
        print(f"VPN Connected: {vpn_status.get('connected')}")
        print(f"Forwarded Port: {vpn_status.get('forwarded_port')}")
        
        return vpn_status
    except Exception as e:
        print(f"Error checking VPN status: {e}")
        return None

def list_engines():
    """List all engines and their forwarded status."""
    print_section("Engine List")
    try:
        response = requests.get(f"{ORCHESTRATOR_URL}/engines", headers=headers)
        response.raise_for_status()
        engines = response.json()
        
        print(f"Total engines: {len(engines)}")
        
        forwarded_count = 0
        for engine in engines:
            forwarded = engine.get('forwarded', False)
            if forwarded:
                forwarded_count += 1
            
            print(f"\nEngine: {engine['container_name']}")
            print(f"  ID: {engine['container_id'][:12]}")
            print(f"  Host: {engine['host']}")
            print(f"  Port: {engine['port']}")
            print(f"  Forwarded: {'✓ YES' if forwarded else '✗ No'}")
            print(f"  Health: {engine.get('health_status', 'unknown')}")
            print(f"  Active Streams: {len(engine.get('streams', []))}")
        
        print(f"\nSummary:")
        print(f"  Total engines: {len(engines)}")
        print(f"  Forwarded engines: {forwarded_count}")
        
        if forwarded_count > 1:
            print(f"  ⚠️  WARNING: More than one engine is forwarded!")
        elif forwarded_count == 0:
            print(f"  ℹ️  INFO: No engines are forwarded (expected if not using Gluetun)")
        else:
            print(f"  ✓ OK: Exactly one engine is forwarded")
        
        return engines
    except Exception as e:
        print(f"Error listing engines: {e}")
        return []

def provision_engine():
    """Provision a new engine."""
    print_section("Provisioning New Engine")
    try:
        response = requests.post(
            f"{ORCHESTRATOR_URL}/provision/acestream",
            headers=headers,
            json={}
        )
        response.raise_for_status()
        engine = response.json()
        
        print(f"✓ Successfully provisioned engine:")
        print(f"  Container ID: {engine['container_id'][:12]}")
        print(f"  Container Name: {engine['container_name']}")
        print(f"  HTTP Port: {engine['host_http_port']}")
        
        return engine
    except Exception as e:
        print(f"Error provisioning engine: {e}")
        if hasattr(e, 'response'):
            print(f"Response: {e.response.text}")
        return None

def delete_engine(container_id):
    """Delete an engine."""
    print_section(f"Deleting Engine {container_id[:12]}")
    try:
        response = requests.delete(
            f"{ORCHESTRATOR_URL}/containers/{container_id}",
            headers=headers
        )
        response.raise_for_status()
        print(f"✓ Successfully deleted engine {container_id[:12]}")
        return True
    except Exception as e:
        print(f"Error deleting engine: {e}")
        return False

def main():
    print(f"\n{'#'*60}")
    print(f"# Manual Test: Forwarded Engine Functionality")
    print(f"# Orchestrator: {ORCHESTRATOR_URL}")
    print(f"# Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'#'*60}")
    
    # Step 1: Check VPN status
    vpn_status = check_vpn_status()
    if not vpn_status or not vpn_status.get('enabled'):
        print("\n⚠️  VPN is not enabled. Forwarded engine feature requires Gluetun.")
        print("This test will still run but forwarded status won't be meaningful.")
    
    # Step 2: List current engines
    engines_before = list_engines()
    
    # Step 3: Provision a new engine
    input("\nPress Enter to provision a new engine...")
    new_engine = provision_engine()
    
    # Step 4: List engines again to see the new one
    input("\nPress Enter to list engines again...")
    engines_after = list_engines()
    
    # Step 5: Check forwarded status distribution
    print_section("Forwarded Status Analysis")
    forwarded_engines = [e for e in engines_after if e.get('forwarded', False)]
    non_forwarded_engines = [e for e in engines_after if not e.get('forwarded', False)]
    
    print(f"Forwarded engines: {len(forwarded_engines)}")
    for engine in forwarded_engines:
        print(f"  - {engine['container_name']} ({engine['container_id'][:12]})")
    
    print(f"\nNon-forwarded engines: {len(non_forwarded_engines)}")
    for engine in non_forwarded_engines:
        print(f"  - {engine['container_name']} ({engine['container_id'][:12]})")
    
    # Step 6: Optionally delete the forwarded engine
    if forwarded_engines:
        forwarded_engine = forwarded_engines[0]
        response = input(f"\nDelete forwarded engine {forwarded_engine['container_name']}? (y/N): ")
        if response.lower() == 'y':
            delete_engine(forwarded_engine['container_id'])
            
            input("\nPress Enter to list engines after deletion...")
            engines_final = list_engines()
            
            print_section("After Deleting Forwarded Engine")
            forwarded_final = [e for e in engines_final if e.get('forwarded', False)]
            print(f"Forwarded engines: {len(forwarded_final)}")
            if forwarded_final:
                print(f"  - {forwarded_final[0]['container_name']} ({forwarded_final[0]['container_id'][:12]})")
                print("\n✓ A new engine was promoted to forwarded (via autoscaler)")
            else:
                print("\n⚠️  No forwarded engines (autoscaler should create one)")
    
    print_section("Test Complete")
    print("✓ Manual test completed successfully")
    print("\nKey points to verify:")
    print("1. ✓ Only one engine should be forwarded at a time")
    print("2. ✓ Forwarded engine should show 'FORWARDED' badge in UI")
    print("3. ✓ When forwarded engine is deleted, autoscaler creates a new one")
    print("4. ✓ Forwarded status persists across restarts (stored in DB)")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nTest interrupted by user")
    except Exception as e:
        print(f"\n\nTest failed with error: {e}")
        import traceback
        traceback.print_exc()
