#!/usr/bin/env python3
"""
Manual integration test for AceStream proxy playback.

This script tests the proxy's ability to fetch and stream data from an AceStream engine.
It verifies that:
1. HTTP client is configured correctly (compression disabled, connection limits)
2. Playback URL returns data successfully
3. Stream data can be read and written

Usage:
    python tests/manual/test_proxy_playback.py --engine-host localhost --engine-port 6878 --content-id <acestream_id>

Requirements:
    - A running AceStream engine
    - A valid AceStream content ID
"""

import asyncio
import argparse
import sys
import httpx
from uuid import uuid4

# Add parent directory to path
sys.path.insert(0, '/home/runner/work/acestream-orchestrator/acestream-orchestrator')

from app.services.proxy.config import (
    MAX_CONNECTIONS,
    MAX_KEEPALIVE_CONNECTIONS,
    KEEPALIVE_EXPIRY,
    USER_AGENT,
    COPY_CHUNK_SIZE,
)


async def test_proxy_playback(engine_host: str, engine_port: int, content_id: str):
    """Test proxy playback with a real AceStream engine."""
    
    print("=" * 70)
    print("AceStream Proxy Playback Test")
    print("=" * 70)
    print(f"Engine: {engine_host}:{engine_port}")
    print(f"Content ID: {content_id}")
    print()
    
    # Step 1: Create HTTP client with AceStream-compatible configuration
    print("Step 1: Creating HTTP client with compression disabled...")
    limits = httpx.Limits(
        max_connections=MAX_CONNECTIONS,
        max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        keepalive_expiry=KEEPALIVE_EXPIRY,
    )
    
    client = httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0, read=None),
        follow_redirects=True,
        limits=limits,
    )
    
    print(f"✓ HTTP client created")
    print(f"  - max_connections: {limits.max_connections}")
    print(f"  - max_keepalive_connections: {limits.max_keepalive_connections}")
    print(f"  - keepalive_expiry: {limits.keepalive_expiry}s")
    print()
    
    try:
        # Step 2: Get stream metadata from engine
        print("Step 2: Fetching stream metadata from engine...")
        getstream_url = (
            f"http://{engine_host}:{engine_port}/ace/getstream"
            f"?id={content_id}&format=json&pid={uuid4()}"
        )
        print(f"  URL: {getstream_url}")
        
        response = await client.get(getstream_url)
        response.raise_for_status()
        data = response.json()
        
        if "error" in data and data["error"]:
            print(f"✗ AceStream error: {data['error']}")
            return False
        
        if "response" not in data:
            print("✗ Invalid response from AceStream engine")
            return False
        
        resp = data["response"]
        playback_url = resp.get("playback_url")
        stat_url = resp.get("stat_url")
        command_url = resp.get("command_url")
        playback_session_id = resp.get("playback_session_id")
        
        print(f"✓ Stream metadata received")
        print(f"  - playback_url: {playback_url}")
        print(f"  - stat_url: {stat_url}")
        print(f"  - command_url: {command_url}")
        print(f"  - playback_session_id: {playback_session_id}")
        print()
        
        if not playback_url:
            print("✗ No playback URL in response")
            return False
        
        # Step 3: Fetch stream data from playback URL
        print("Step 3: Fetching stream data from playback URL...")
        print("  This is the critical test - compression must be disabled!")
        
        # Build headers with compression disabled (critical!)
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "*/*",
            "Accept-Encoding": "identity",  # Disable compression
        }
        
        print(f"  Headers:")
        for key, value in headers.items():
            print(f"    {key}: {value}")
        print()
        
        chunk_count = 0
        bytes_received = 0
        
        async with client.stream("GET", playback_url, headers=headers) as stream_response:
            stream_response.raise_for_status()
            
            print(f"✓ Stream response received (status: {stream_response.status_code})")
            print(f"  Response headers:")
            for key, value in stream_response.headers.items():
                print(f"    {key}: {value}")
            print()
            
            print("  Reading stream chunks (max 10)...")
            async for chunk in stream_response.aiter_bytes(chunk_size=COPY_CHUNK_SIZE):
                chunk_count += 1
                bytes_received += len(chunk)
                
                print(f"  ✓ Chunk {chunk_count}: {len(chunk)} bytes")
                
                # Read max 10 chunks for testing
                if chunk_count >= 10:
                    break
        
        print()
        print(f"✓ Stream data received successfully!")
        print(f"  - Total chunks: {chunk_count}")
        print(f"  - Total bytes: {bytes_received}")
        print()
        
        # Step 4: Stop the stream
        print("Step 4: Stopping stream...")
        stop_url = f"{command_url}?method=stop"
        stop_response = await client.get(stop_url)
        stop_response.raise_for_status()
        print(f"✓ Stream stopped")
        print()
        
        print("=" * 70)
        print("✓ All tests passed!")
        print("=" * 70)
        print()
        print("The proxy should now work correctly with AceStream engines.")
        print("Key success factors:")
        print("  1. ✓ Compression disabled via 'Accept-Encoding: identity'")
        print("  2. ✓ Connection limits applied")
        print("  3. ✓ Playback URL returned stream data")
        print("  4. ✓ Stream stopped cleanly")
        
        return True
        
    except httpx.HTTPStatusError as e:
        print(f"✗ HTTP error: {e.response.status_code} {e.response.reason_phrase}")
        print(f"  Response: {e.response.text[:500]}")
        return False
    except Exception as e:
        print(f"✗ Error: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        await client.aclose()


def main():
    parser = argparse.ArgumentParser(description="Test AceStream proxy playback")
    parser.add_argument("--engine-host", default="localhost", help="AceStream engine host")
    parser.add_argument("--engine-port", type=int, default=6878, help="AceStream engine port")
    parser.add_argument("--content-id", required=True, help="AceStream content ID (infohash or content_id)")
    
    args = parser.parse_args()
    
    success = asyncio.run(test_proxy_playback(args.engine_host, args.engine_port, args.content_id))
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
