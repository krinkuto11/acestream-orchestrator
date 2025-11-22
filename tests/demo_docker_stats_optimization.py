#!/usr/bin/env python3
"""
Demo script showing the performance improvement of batch docker stats collection.

This script compares the old individual-query approach vs the new batch approach.
"""

import time
import sys
import os

# Add the app directory to the path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from app.services.docker_stats import (
    get_container_stats,
    get_all_container_stats_batch,
    get_multiple_container_stats
)


def get_running_containers():
    """Get list of running container IDs."""
    import subprocess
    try:
        result = subprocess.run(
            ['docker', 'ps', '-q'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode == 0:
            return [cid.strip() for cid in result.stdout.strip().split('\n') if cid.strip()]
        return []
    except Exception as e:
        print(f"Error getting containers: {e}")
        return []


def demo_individual_approach(container_ids):
    """Demo the old approach: individual queries."""
    print("\n🔍 OLD APPROACH: Individual queries")
    print(f"   Querying stats for {len(container_ids)} containers individually...")
    
    start_time = time.time()
    results = {}
    for cid in container_ids:
        stats = get_container_stats(cid)
        if stats:
            results[cid] = stats
    elapsed = time.time() - start_time
    
    print(f"   ✓ Collected stats for {len(results)} containers")
    print(f"   ⏱️  Time taken: {elapsed:.3f} seconds")
    print(f"   📊 Average per container: {elapsed/len(container_ids) if container_ids else 0:.3f} seconds")
    
    return results, elapsed


def demo_batch_approach(container_ids):
    """Demo the new approach: batch collection."""
    print("\n🚀 NEW APPROACH: Batch collection")
    print(f"   Querying stats for all containers in one command...")
    
    start_time = time.time()
    all_stats = get_all_container_stats_batch()
    
    # Filter to requested containers
    results = {cid: all_stats[cid] for cid in container_ids if cid in all_stats}
    elapsed = time.time() - start_time
    
    print(f"   ✓ Collected stats for {len(results)} containers")
    print(f"   ⏱️  Time taken: {elapsed:.3f} seconds")
    
    return results, elapsed


def demo_new_api(container_ids):
    """Demo the updated API that automatically uses batch collection."""
    print("\n✨ UPDATED API: Automatic batch optimization")
    print(f"   Using get_multiple_container_stats() with {len(container_ids)} containers...")
    
    start_time = time.time()
    results = get_multiple_container_stats(container_ids)
    elapsed = time.time() - start_time
    
    print(f"   ✓ Collected stats for {len(results)} containers")
    print(f"   ⏱️  Time taken: {elapsed:.3f} seconds")
    
    return results, elapsed


def main():
    print("=" * 60)
    print("Docker Stats Optimization Demo")
    print("=" * 60)
    
    # Get running containers
    print("\n🔎 Detecting running containers...")
    container_ids = get_running_containers()
    
    if not container_ids:
        print("   ⚠️  No running containers found!")
        print("   Start some containers to see the performance difference.")
        return
    
    print(f"   ✓ Found {len(container_ids)} running containers")
    
    if len(container_ids) == 1:
        print("\n   ℹ️  Note: With only 1 container, the improvement is minimal.")
        print("   Start more containers to see bigger performance gains!")
    
    # Demo old approach
    old_results, old_time = demo_individual_approach(container_ids)
    
    # Demo new batch approach
    new_results, new_time = demo_batch_approach(container_ids)
    
    # Demo updated API
    api_results, api_time = demo_new_api(container_ids)
    
    # Compare results
    print("\n" + "=" * 60)
    print("📊 RESULTS SUMMARY")
    print("=" * 60)
    print(f"Containers processed: {len(container_ids)}")
    print(f"Old approach time:    {old_time:.3f} seconds")
    print(f"New batch time:       {new_time:.3f} seconds")
    print(f"Updated API time:     {api_time:.3f} seconds")
    
    if new_time > 0 and old_time > 0:
        speedup = old_time / new_time
        improvement = ((old_time - new_time) / old_time) * 100
        print(f"\n🎉 Speedup: {speedup:.2f}x faster")
        print(f"🎉 Improvement: {improvement:.1f}% reduction in time")
    
    print("\n✅ The updated API automatically uses batch collection for efficiency")
    print("✅ Fallback to individual queries if batch collection fails")
    print("=" * 60)


if __name__ == '__main__':
    main()
