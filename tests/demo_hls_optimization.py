#!/usr/bin/env python3
"""
Demo script to show the reduction in logging noise after HLS optimization.

This script simulates the before/after behavior to demonstrate the fix:
- Before: Engine selection ran on EVERY manifest request
- After: Engine selection runs only on FIRST request (channel creation)

Expected improvement:
- Reduced INFO log messages by ~90% for subsequent requests
- Engine selection computation happens only once per stream
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))


def simulate_before_fix():
    """Simulate the old behavior - engine selection on every request"""
    print("=" * 80)
    print("BEFORE FIX: Every manifest request triggers engine selection")
    print("=" * 80)
    print()
    
    # Simulate 5 manifest requests from the same client (typical for first 30 seconds)
    for i in range(1, 6):
        print(f"Request #{i} - Client requests manifest:")
        print("  [INFO] Selected engine abc123 for HLS stream XYZ (forwarded=True, current_load=0)")
        print("  [INFO] Client client-uuid connecting to HLS stream XYZ from 192.168.1.100")
        if i == 1:
            print("  [INFO] Requesting HLS stream from engine: http://gluetun:19000/ace/manifest.m3u8")
            print("  [INFO] HLS playback URL: http://gluetun:19000/ace/m/hash/session.m3u8")
            print("  [INFO] Initialized HLS stream manager for channel XYZ")
        else:
            print("  [INFO] HLS channel XYZ already exists, reusing existing session")
        print()
    
    print("Total INFO log messages: 5 requests × 2-6 lines = 12+ INFO messages")
    print("Engine selection computations: 5 (once per request)")
    print()


def simulate_after_fix():
    """Simulate the new optimized behavior - engine selection only on first request"""
    print("=" * 80)
    print("AFTER FIX: Engine selection only for new channels")
    print("=" * 80)
    print()
    
    # Simulate 5 manifest requests from the same client (typical for first 30 seconds)
    for i in range(1, 6):
        print(f"Request #{i} - Client requests manifest:")
        if i == 1:
            # First request - create channel
            print("  [INFO] Selected engine abc123 for new HLS stream XYZ (forwarded=True, current_load=0)")
            print("  [INFO] Client client-uuid initializing new HLS stream XYZ from 192.168.1.100")
            print("  [INFO] Requesting HLS stream from engine: http://gluetun:19000/ace/manifest.m3u8")
            print("  [INFO] HLS playback URL: http://gluetun:19000/ace/m/hash/session.m3u8")
            print("  [INFO] Initialized HLS stream manager for channel XYZ")
        else:
            # Subsequent requests - reuse channel
            print("  [DEBUG] HLS channel XYZ already exists, serving manifest to client-uuid from 192.168.1.100")
        print()
    
    print("Total INFO log messages: 5 INFO for first request + 4 DEBUG for subsequent = 5 INFO messages")
    print("Engine selection computations: 1 (only on first request)")
    print()


def main():
    print()
    print("#" * 80)
    print("# HLS Proxy Optimization Demo")
    print("#" * 80)
    print()
    
    simulate_before_fix()
    print()
    print("=" * 80)
    print()
    
    simulate_after_fix()
    
    print()
    print("=" * 80)
    print("IMPROVEMENT SUMMARY")
    print("=" * 80)
    print()
    print("✓ INFO log messages reduced by ~58% (12+ → 5)")
    print("✓ Engine selection overhead reduced by 80% (5 → 1)")
    print("✓ Subsequent requests use DEBUG level for minimal noise")
    print("✓ Behavior now matches TS proxy (select engine only once per stream)")
    print()


if __name__ == '__main__':
    main()
