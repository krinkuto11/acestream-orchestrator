// Acexy - Copyright (C) 2024 - Javinator9889 <dev at javinator9889 dot com>
// This program comes with ABSOLUTELY NO WARRANTY; for details type `show w'.
// This is free software, and you are welcome to redistribute it
// under certain conditions; type `show c' for details.
package acexy

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sync"
	"testing"
	"time"
)

// TestConcurrentStreamStartWithFailures tests that concurrent access to the same stream
// during failures doesn't leave the system in a bad state
func TestConcurrentStreamStartWithFailures(t *testing.T) {
	callCount := 0
	var mu sync.Mutex

	// Create a mock acestream engine that sometimes times out
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ace/getstream" {
			mu.Lock()
			callCount++
			currentCall := callCount
			mu.Unlock()

			// Return a valid stream response
			w.Header().Set("Content-Type", "application/json")
			playbackURL := fmt.Sprintf("http://timeout-server-%d:9999/stream", currentCall)
			w.Write([]byte(fmt.Sprintf(`{
				"response": {
					"playback_url": "%s",
					"stat_url": "http://localhost:6878/ace/stat/test/playback%d",
					"command_url": "http://localhost:6878/ace/cmd/test/playback%d"
				}
			}`, playbackURL, currentCall, currentCall)))
			return
		}
	}))
	defer engine.Close()

	// Parse the server URL
	u, _ := url.Parse(engine.URL)

	// Create acexy instance with very short timeout
	acexyInst := &Acexy{
		Scheme:            u.Scheme,
		Host:              u.Hostname(),
		Port:              parseInt(u.Port()),
		Endpoint:          MPEG_TS_ENDPOINT,
		EmptyTimeout:      1 * time.Second,
		BufferSize:        1024,
		NoResponseTimeout: 1 * time.Second, // Short timeout for faster test
	}
	acexyInst.Init()

	aceID, _ := NewAceID("concurrent-test-stream", "")

	// Launch 5 concurrent attempts to start the same stream
	// All should fail with timeout, but the system should remain consistent
	var wg sync.WaitGroup
	errors := make([]error, 5)

	for i := 0; i < 5; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()

			// Each goroutine tries to fetch and start the stream
			stream, err := acexyInst.FetchStream(aceID, nil)
			if err != nil {
				errors[idx] = fmt.Errorf("FetchStream failed: %w", err)
				return
			}

			mockWriter := &mockWriter{}
			err = acexyInst.StartStream(stream, mockWriter)
			errors[idx] = err
		}(i)

		// Small delay between launches to create more realistic timing
		time.Sleep(10 * time.Millisecond)
	}

	wg.Wait()

	// Count how many succeeded (should be 0 or 1 due to timeouts)
	succeeded := 0
	for _, err := range errors {
		if err == nil {
			succeeded++
		} else {
			t.Logf("Got expected error: %v", err)
		}
	}

	t.Logf("Out of 5 concurrent attempts, %d succeeded", succeeded)

	// Verify the stream map is in a consistent state (should be empty or have a clean entry)
	acexyInst.mutex.Lock()
	streamEntry, exists := acexyInst.streams[aceID]
	acexyInst.mutex.Unlock()

	if exists {
		// If a stream entry exists, it should not be in a failed state
		if streamEntry.failed {
			t.Error("Stream entry exists but is marked as failed - should have been cleaned up")
		}
		t.Logf("Stream entry exists with %d clients, player=%v", streamEntry.clients, streamEntry.player != nil)
	} else {
		t.Log("Stream entry was properly cleaned up")
	}

	// Try one more time to ensure we can recover from the failed state
	t.Log("Attempting final stream start to verify recovery...")
	stream, err := acexyInst.FetchStream(aceID, nil)
	if err != nil {
		t.Fatalf("Should be able to fetch stream after failures: %v", err)
	}

	// Verify it's a new stream (engine should have been called again)
	mu.Lock()
	finalCallCount := callCount
	mu.Unlock()

	if finalCallCount <= 5 {
		t.Errorf("Expected engine to be called more than 5 times (initial concurrent + final), got %d", finalCallCount)
	}

	t.Logf("Successfully recovered - engine called %d times total", finalCallCount)
	t.Logf("Final stream playback URL: %s", stream.PlaybackURL)
}

// TestRapidStreamStartStop simulates the rapid start/stop scenario from the logs
func TestRapidStreamStartStop(t *testing.T) {
	callCount := 0
	var mu sync.Mutex

	// Create a mock acestream engine
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ace/getstream" {
			mu.Lock()
			callCount++
			currentCall := callCount
			mu.Unlock()

			w.Header().Set("Content-Type", "application/json")
			playbackURL := fmt.Sprintf("http://timeout-server-%d:9999/stream", currentCall)
			w.Write([]byte(fmt.Sprintf(`{
				"response": {
					"playback_url": "%s",
					"stat_url": "http://localhost:6878/ace/stat/test/playback%d",
					"command_url": "http://localhost:6878/ace/cmd/test/playback%d"
				}
			}`, playbackURL, currentCall, currentCall)))
			return
		}
	}))
	defer engine.Close()

	u, _ := url.Parse(engine.URL)

	acexyInst := &Acexy{
		Scheme:            u.Scheme,
		Host:              u.Hostname(),
		Port:              parseInt(u.Port()),
		Endpoint:          MPEG_TS_ENDPOINT,
		EmptyTimeout:      1 * time.Second,
		BufferSize:        1024,
		NoResponseTimeout: 1 * time.Second,
	}
	acexyInst.Init()

	// Simulate rapid switching between streams (like in the logs)
	streamIDs := []string{"stream1", "stream2", "stream3"}

	for iteration := 0; iteration < 3; iteration++ {
		t.Logf("Iteration %d", iteration+1)

		for _, streamIDStr := range streamIDs {
			aceID, _ := NewAceID(streamIDStr, "")

			// Try to start stream
			stream, err := acexyInst.FetchStream(aceID, nil)
			if err != nil {
				t.Logf("FetchStream failed for %s: %v", streamIDStr, err)
				continue
			}

			mockWriter := &mockWriter{}
			err = acexyInst.StartStream(stream, mockWriter)
			if err != nil {
				t.Logf("StartStream failed for %s: %v", streamIDStr, err)
			}

			// Quick stop (simulating user switching channels)
			time.Sleep(50 * time.Millisecond)
		}
	}

	// After rapid switching, verify the system is still functional
	acexyInst.mutex.Lock()
	streamCount := len(acexyInst.streams)
	hasFailedStreams := false
	for _, stream := range acexyInst.streams {
		if stream.failed {
			hasFailedStreams = true
			break
		}
	}
	acexyInst.mutex.Unlock()

	t.Logf("After rapid switching: %d streams in map, has failed streams: %v", streamCount, hasFailedStreams)

	if hasFailedStreams {
		t.Error("System has failed streams that should have been cleaned up")
	}

	// Verify we can still start a new stream
	aceID, _ := NewAceID("final-stream", "")
	stream, err := acexyInst.FetchStream(aceID, nil)
	if err != nil {
		t.Fatalf("Should be able to fetch stream after rapid switching: %v", err)
	}

	t.Logf("Successfully started final stream after rapid switching: %s", stream.PlaybackURL)
}

// TestStreamFailureDoesNotBlockSubsequentRequests ensures that when one stream fails,
// it doesn't prevent other streams from being started
func TestStreamFailureDoesNotBlockSubsequentRequests(t *testing.T) {
	callCount := 0
	var mu sync.Mutex

	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ace/getstream" {
			mu.Lock()
			callCount++
			currentCall := callCount
			mu.Unlock()

			w.Header().Set("Content-Type", "application/json")
			playbackURL := fmt.Sprintf("http://timeout-server-%d:9999/stream", currentCall)
			w.Write([]byte(fmt.Sprintf(`{
				"response": {
					"playback_url": "%s",
					"stat_url": "http://localhost:6878/ace/stat/test/playback%d",
					"command_url": "http://localhost:6878/ace/cmd/test/playback%d"
				}
			}`, playbackURL, currentCall, currentCall)))
		}
	}))
	defer engine.Close()

	u, _ := url.Parse(engine.URL)

	acexyInst := &Acexy{
		Scheme:            u.Scheme,
		Host:              u.Hostname(),
		Port:              parseInt(u.Port()),
		Endpoint:          MPEG_TS_ENDPOINT,
		EmptyTimeout:      1 * time.Second,
		BufferSize:        1024,
		NoResponseTimeout: 1 * time.Second,
	}
	acexyInst.Init()

	// Try to start 10 different streams in sequence
	// Even though they all fail, the system should remain functional
	for i := 0; i < 10; i++ {
		aceID, _ := NewAceID(fmt.Sprintf("stream-%d", i), "")

		stream, err := acexyInst.FetchStream(aceID, nil)
		if err != nil {
			t.Errorf("FetchStream %d failed: %v", i, err)
			continue
		}

		mockWriter := &mockWriter{}
		err = acexyInst.StartStream(stream, mockWriter)
		// We expect errors due to timeout
		if err != nil {
			t.Logf("Stream %d failed as expected: %v", i, err)
		}

		// Brief pause between attempts
		time.Sleep(10 * time.Millisecond)
	}

	// Verify no streams are stuck in the map in a failed state
	acexyInst.mutex.Lock()
	failedCount := 0
	for _, stream := range acexyInst.streams {
		if stream.failed {
			failedCount++
		}
	}
	acexyInst.mutex.Unlock()

	if failedCount > 0 {
		t.Errorf("Found %d failed streams still in map (should have been cleaned up)", failedCount)
	} else {
		t.Log("All failed streams were properly cleaned up")
	}

	// Engine should have been called at least 10 times (once per stream)
	mu.Lock()
	finalCallCount := callCount
	mu.Unlock()

	if finalCallCount < 10 {
		t.Errorf("Expected engine to be called at least 10 times, got %d", finalCallCount)
	}
}
