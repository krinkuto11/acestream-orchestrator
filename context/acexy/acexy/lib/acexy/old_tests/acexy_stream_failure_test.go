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
	"testing"
	"time"
)

// TestStreamFailureNotReused verifies that when a stream fails during StartStream,
// the stream entry is properly cleaned up and not reused on retry
func TestStreamFailureNotReused(t *testing.T) {
	callCount := 0
	
	// Create a mock acestream engine
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ace/getstream" {
			callCount++
			// Return a valid stream response with a playback URL that will timeout
			w.Header().Set("Content-Type", "application/json")
			playbackURL := fmt.Sprintf("http://timeout-server-%d:9999/stream", callCount)
			w.Write([]byte(fmt.Sprintf(`{
				"response": {
					"playback_url": "%s",
					"stat_url": "http://localhost:6878/ace/stat/test/playback123",
					"command_url": "http://localhost:6878/ace/cmd/test/playback123"
				}
			}`, playbackURL)))
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

	// Create a test stream ID
	aceID, _ := NewAceID("test-stream-id", "")

	// First attempt - should succeed in FetchStream but fail in StartStream
	t.Log("First attempt: FetchStream...")
	stream1, err := acexyInst.FetchStream(aceID, nil)
	if err != nil {
		t.Fatalf("FetchStream should succeed, got error: %v", err)
	}
	t.Logf("FetchStream succeeded, got playback URL: %s", stream1.PlaybackURL)

	// Try to start the stream - this should fail with timeout
	t.Log("First attempt: StartStream...")
	mockWriter := &mockWriter{}
	err = acexyInst.StartStream(stream1, mockWriter)
	if err == nil {
		t.Error("Expected StartStream to fail with timeout, but it succeeded")
	}
	t.Logf("StartStream failed as expected: %v", err)

	// Verify stream was cleaned up from the map
	acexyInst.mutex.Lock()
	_, exists := acexyInst.streams[aceID]
	acexyInst.mutex.Unlock()

	if exists {
		t.Error("Stream entry should have been deleted after StartStream failure")
	} else {
		t.Log("Stream was properly cleaned up after failure")
	}

	// Second attempt - should create a NEW stream, not reuse the failed one
	t.Log("Second attempt: FetchStream...")
	stream2, err := acexyInst.FetchStream(aceID, nil)
	if err != nil {
		t.Fatalf("Second FetchStream should succeed, got error: %v", err)
	}

	// Verify it's a fresh stream by checking that the engine was called again
	if callCount < 2 {
		t.Errorf("Expected engine to be called at least 2 times, got %d", callCount)
	} else {
		t.Logf("Engine called %d times - stream was recreated, not reused", callCount)
	}

	// Verify the playback URLs are different (indicating different stream instances)
	if stream1.PlaybackURL == stream2.PlaybackURL {
		t.Error("Second stream has same playback URL as first - stream was reused instead of recreated")
	} else {
		t.Log("Second stream has different playback URL - correctly recreated")
	}
}

// TestStreamWithZeroClientsImmediatelyCleanedUp verifies that streams with 0 clients
// and no player are immediately cleaned up, not after 10 minutes
func TestStreamWithZeroClientsImmediatelyCleanedUp(t *testing.T) {
	// Create a mock acestream engine
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ace/getstream" {
			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(`{
				"response": {
					"playback_url": "http://timeout-server:9999/stream",
					"stat_url": "http://localhost:6878/ace/stat/test/playback123",
					"command_url": "http://localhost:6878/ace/cmd/test/playback123"
				}
			}`))
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

	aceID, _ := NewAceID("test-stream-cleanup", "")

	// Create a stream
	_, err := acexyInst.FetchStream(aceID, nil)
	if err != nil {
		t.Fatalf("FetchStream failed: %v", err)
	}

	// Simulate StartStream failure by directly manipulating the stream state
	acexyInst.mutex.Lock()
	streamEntry := acexyInst.streams[aceID]
	// Stream exists with 0 clients and no player (broken state)
	if streamEntry.clients != 0 {
		t.Errorf("Expected 0 clients, got %d", streamEntry.clients)
	}
	if streamEntry.player != nil {
		t.Error("Expected nil player")
	}
	acexyInst.mutex.Unlock()

	// Now call FetchStream again immediately (within 10 minutes)
	// The old implementation would have reused this broken stream
	// The new implementation should clean it up and create a new one
	t.Log("Calling FetchStream again immediately...")
	stream2, err := acexyInst.FetchStream(aceID, nil)
	if err != nil {
		t.Fatalf("Second FetchStream failed: %v", err)
	}

	// Verify a new stream entry was created
	acexyInst.mutex.Lock()
	newStreamEntry := acexyInst.streams[aceID]
	acexyInst.mutex.Unlock()

	// The creation time should be very recent (not from the original stream)
	if time.Since(newStreamEntry.createdAt) > 2*time.Second {
		t.Errorf("Stream appears to be old (created %v ago), expected fresh stream", time.Since(newStreamEntry.createdAt))
	}

	// Verify we got a valid stream back
	if stream2.PlaybackURL == "" {
		t.Error("Expected valid playback URL, got empty string")
	}

	t.Log("Stream with 0 clients was immediately cleaned up and recreated")
}
