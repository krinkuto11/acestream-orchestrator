#!/usr/bin/env python3
"""
Demo script to show cache working with API endpoints.
This script makes requests to cached endpoints and shows cache statistics.
"""

import time
import requests
import json
from typing import Dict, Any


def print_section(title: str):
    """Print a section header."""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print('='*60)


def get_cache_stats(base_url: str) -> Dict[str, Any]:
    """Get current cache statistics."""
    response = requests.get(f"{base_url}/cache/stats")
    return response.json()


def print_cache_stats(stats: Dict[str, Any]):
    """Pretty print cache statistics."""
    print(f"\nCache Stats:")
    print(f"  Size: {stats['size']} entries")
    print(f"  Hits: {stats['hits']}")
    print(f"  Misses: {stats['misses']}")
    print(f"  Hit Rate: {stats['hit_rate']:.1f}%")
    
    if stats['entries']:
        print(f"\n  Cached Entries:")
        for entry in stats['entries']:
            print(f"    - {entry['key']}: age={entry['age']:.2f}s, ttl_remaining={entry['ttl_remaining']:.2f}s")


def demo_cache_behavior(base_url: str = "http://localhost:8000"):
    """Demonstrate cache behavior with multiple requests."""
    
    print_section("Cache Demo - Stats Caching Feature")
    
    # Clear cache at start
    print("\n1. Starting with clean cache...")
    initial_stats = get_cache_stats(base_url)
    print_cache_stats(initial_stats)
    
    # First request - should be cache miss
    print_section("First Request (Cache Miss)")
    print("\nMaking request to /engines/stats/total...")
    start = time.time()
    response1 = requests.get(f"{base_url}/engines/stats/total")
    time1 = time.time() - start
    print(f"Response time: {time1*1000:.2f}ms")
    print(f"Status: {response1.status_code}")
    
    stats_after_first = get_cache_stats(base_url)
    print_cache_stats(stats_after_first)
    
    # Second request - should be cache hit
    print_section("Second Request (Cache Hit)")
    print("\nMaking second request to /engines/stats/total...")
    time.sleep(0.5)  # Small delay
    start = time.time()
    response2 = requests.get(f"{base_url}/engines/stats/total")
    time2 = time.time() - start
    print(f"Response time: {time2*1000:.2f}ms")
    print(f"Status: {response2.status_code}")
    print(f"Data identical: {response1.json() == response2.json()}")
    
    stats_after_second = get_cache_stats(base_url)
    print_cache_stats(stats_after_second)
    
    # Multiple endpoints
    print_section("Testing Multiple Endpoints")
    endpoints = [
        "/engines/stats/total",
        "/orchestrator/status",
        "/vpn/status"
    ]
    
    print("\nMaking requests to multiple endpoints...")
    for endpoint in endpoints:
        try:
            response = requests.get(f"{base_url}{endpoint}")
            print(f"  ✓ {endpoint}: {response.status_code}")
        except Exception as e:
            print(f"  ✗ {endpoint}: {e}")
    
    stats_after_multiple = get_cache_stats(base_url)
    print_cache_stats(stats_after_multiple)
    
    # Demonstrate cache expiration
    print_section("Cache Expiration Demo")
    print("\nWaiting for cache to expire (TTL = 3 seconds)...")
    time.sleep(3.5)
    
    print("\nMaking request after TTL expiration...")
    start = time.time()
    response3 = requests.get(f"{base_url}/engines/stats/total")
    time3 = time.time() - start
    print(f"Response time: {time3*1000:.2f}ms")
    print(f"Status: {response3.status_code}")
    
    stats_after_expiry = get_cache_stats(base_url)
    print_cache_stats(stats_after_expiry)
    
    # Performance comparison
    print_section("Performance Summary")
    print(f"\nFirst request (cache miss): {time1*1000:.2f}ms")
    print(f"Second request (cache hit): {time2*1000:.2f}ms")
    if time1 > time2:
        speedup = time1 / time2
        print(f"Speedup: {speedup:.2f}x faster with cache")
    
    # Final stats
    print_section("Final Cache Statistics")
    final_stats = get_cache_stats(base_url)
    print_cache_stats(final_stats)
    
    print("\n" + "="*60)
    print("Demo completed!")
    print("="*60 + "\n")


if __name__ == "__main__":
    import sys
    
    base_url = "http://localhost:8000"
    if len(sys.argv) > 1:
        base_url = sys.argv[1]
    
    try:
        demo_cache_behavior(base_url)
    except requests.exceptions.ConnectionError:
        print(f"❌ Error: Could not connect to {base_url}")
        print("   Make sure the orchestrator is running:")
        print("   uvicorn app.main:app --host 0.0.0.0 --port 8000")
        sys.exit(1)
    except Exception as e:
        print(f"❌ Error: {e}")
        sys.exit(1)
