# Manual Integration Tests

This directory contains manual integration tests that require a live AceStream engine or orchestrator deployment.

## test_proxy_playback.py

Tests the proxy's ability to fetch and stream data from an AceStream engine.

### Prerequisites

1. A running AceStream engine (can be a standalone engine or one managed by the orchestrator)
2. A valid AceStream content ID (infohash or content_id)
3. Python dependencies installed: `pip install httpx`

### Usage

```bash
# Test with local engine
python tests/manual/test_proxy_playback.py --content-id <acestream_id>

# Test with remote engine
python tests/manual/test_proxy_playback.py \
  --engine-host 192.168.1.100 \
  --engine-port 6878 \
  --content-id <acestream_id>
```

### What It Tests

1. ✓ HTTP client configuration (compression disabled, connection limits)
2. ✓ Stream metadata fetch from `/ace/getstream?format=json`
3. ✓ Playback URL data fetch with `Accept-Encoding: identity` header
4. ✓ Stream data reception (reads 10 chunks)
5. ✓ Stream cleanup via command URL

### Expected Output

```
======================================================================
AceStream Proxy Playback Test
======================================================================
Engine: localhost:6878
Content ID: <your_content_id>

Step 1: Creating HTTP client with compression disabled...
✓ HTTP client created
  - max_connections: 10
  - max_keepalive_connections: 10
  - keepalive_expiry: 30s

Step 2: Fetching stream metadata from engine...
  URL: http://localhost:6878/ace/getstream?id=<id>&format=json&pid=<uuid>
✓ Stream metadata received
  - playback_url: http://localhost:6878/...
  - stat_url: http://localhost:6878/...
  - command_url: http://localhost:6878/...
  - playback_session_id: <session_id>

Step 3: Fetching stream data from playback URL...
  This is the critical test - compression must be disabled!
  Headers:
    User-Agent: VLC/3.0.21 LibVLC/3.0.21
    Accept: */*
    Accept-Encoding: identity

✓ Stream response received (status: 200)
  Response headers:
    ...

  Reading stream chunks (max 10)...
  ✓ Chunk 1: 65536 bytes
  ✓ Chunk 2: 65536 bytes
  ...
  ✓ Chunk 10: 65536 bytes

✓ Stream data received successfully!
  - Total chunks: 10
  - Total bytes: 655360

Step 4: Stopping stream...
✓ Stream stopped

======================================================================
✓ All tests passed!
======================================================================

The proxy should now work correctly with AceStream engines.
Key success factors:
  1. ✓ Compression disabled via 'Accept-Encoding: identity'
  2. ✓ Connection limits applied
  3. ✓ Playback URL returned stream data
  4. ✓ Stream stopped cleanly
```

### Troubleshooting

**Error: Connection refused**
- Ensure the AceStream engine is running
- Check the host and port are correct
- Try `curl http://localhost:6878/webui/api/service` to verify engine is accessible

**Error: Invalid content ID**
- Verify the content ID is valid (40-character hex string for infohash)
- Try a known working content ID
- Check AceStream engine logs for errors

**Error: No stream data received**
- This was the original issue that the compression fix resolves
- Verify the fix is applied (check for `Accept-Encoding: identity` in headers)
- Check engine logs for stream start errors

**Error: Timeout waiting for stream**
- Content may not be available on the network
- Try a different content ID
- Increase timeout values in the test

## Running Tests

```bash
# Install dependencies
pip install -r requirements.txt

# Run the test
python tests/manual/test_proxy_playback.py --content-id <your_id>
```

## Notes

- These tests require external dependencies (running AceStream engine)
- They are not part of the automated test suite
- Use them for integration testing and troubleshooting
- They validate the compression fix is working correctly
