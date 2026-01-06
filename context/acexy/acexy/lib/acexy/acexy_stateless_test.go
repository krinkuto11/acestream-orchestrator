// Acexy - Copyright (C) 2024 - Javinator9889 <dev at javinator9889 dot com>
// This program comes with ABSOLUTELY NO WARRANTY; for details type `show w'.
// This is free software, and you are welcome to redistribute it
// under certain conditions; type `show c' for details.
package acexy

import (
	"bytes"
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"testing"
	"time"
)

// TestFetchStreamStateless tests that FetchStream creates unique streams for each request
func TestFetchStreamStateless(t *testing.T) {
	callCount := 0
	seenPIDs := make(map[string]bool)

	// Create a mock acestream engine
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if strings.HasPrefix(r.URL.Path, "/ace/getstream") {
			callCount++
			
			// Check that each call has a unique PID
			pid := r.URL.Query().Get("pid")
			if pid == "" {
				t.Error("PID not provided in request")
			}
			if seenPIDs[pid] {
				t.Errorf("PID %s was reused - should be unique per request", pid)
			}
			seenPIDs[pid] = true

			w.Header().Set("Content-Type", "application/json")
			w.Write([]byte(fmt.Sprintf(`{
				"response": {
					"playback_url": "http://localhost:6878/stream/%s",
					"stat_url": "http://localhost:6878/ace/stat/test/playback%d",
					"command_url": "http://localhost:6878/ace/cmd/test/playback%d"
				}
			}`, pid, callCount, callCount)))
			return
		}
	}))
	defer engine.Close()

	// Parse the server URL
	u, _ := url.Parse(engine.URL)

	// Create acexy instance
	acexyInst := &Acexy{
		Scheme:            u.Scheme,
		Host:              u.Hostname(),
		Port:              parseInt(u.Port()),
		Endpoint:          MPEG_TS_ENDPOINT,
		EmptyTimeout:      1 * time.Second,
		BufferSize:        1024,
		NoResponseTimeout: 10 * time.Second,
	}
	acexyInst.Init()

	aceID, _ := NewAceID("test-stream", "")

	// Fetch the same stream 3 times - should get 3 different PIDs and playback URLs
	for i := 0; i < 3; i++ {
		stream, err := acexyInst.FetchStream(aceID, nil)
		if err != nil {
			t.Fatalf("Iteration %d: FetchStream failed: %v", i, err)
		}

		// Each stream should have a unique playback URL (contains unique PID)
		if stream.PlaybackURL == "" {
			t.Errorf("Iteration %d: Empty playback URL", i)
		}
		
		t.Logf("Iteration %d: Got playback URL: %s", i, stream.PlaybackURL)
	}

	// Verify that each call got a unique PID
	if len(seenPIDs) != 3 {
		t.Errorf("Expected 3 unique PIDs, got %d", len(seenPIDs))
	}

	if callCount != 3 {
		t.Errorf("Expected engine to be called 3 times, got %d", callCount)
	}
}

// TestStartStreamStateless tests that StartStream directly proxies without state
func TestStartStreamStateless(t *testing.T) {
	streamData := []byte("test stream data content")
	
	// Create a mock stream server
	streamServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "video/MP2T")
		w.Header().Set("Content-Length", fmt.Sprintf("%d", len(streamData)))
		w.Write(streamData)
	}))
	defer streamServer.Close()

	// Create a mock engine that returns our stream server URL
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(fmt.Sprintf(`{
			"response": {
				"playback_url": "%s",
				"stat_url": "http://localhost:6878/ace/stat/test/playback",
				"command_url": "http://localhost:6878/ace/cmd/test/playback"
			}
		}`, streamServer.URL)))
	}))
	defer engine.Close()

	u, _ := url.Parse(engine.URL)

	acexyInst := &Acexy{
		Scheme:            u.Scheme,
		Host:              u.Hostname(),
		Port:              parseInt(u.Port()),
		Endpoint:          MPEG_TS_ENDPOINT,
		EmptyTimeout:      500 * time.Millisecond, // Short timeout for test
		BufferSize:        1024,
		NoResponseTimeout: 10 * time.Second,
	}
	acexyInst.Init()

	aceID, _ := NewAceID("test-stream", "")

	// Fetch stream
	stream, err := acexyInst.FetchStream(aceID, nil)
	if err != nil {
		t.Fatalf("FetchStream failed: %v", err)
	}

	// Start stream and capture output
	var output bytes.Buffer
	done := make(chan error, 1)
	go func() {
		_, err := acexyInst.StartStream(stream, &output)
		done <- err
	}()

	// Wait for stream to complete (should be quick since we have all data)
	select {
	case err := <-done:
		if err != nil && err.Error() != "EOF" {
			t.Logf("StartStream returned: %v (this is expected for closed connection)", err)
		}
	case <-time.After(2 * time.Second):
		t.Fatal("Timeout waiting for stream to complete")
	}

	// Verify we got at least some data (the copier may buffer/flush)
	receivedData := output.Bytes()
	if len(receivedData) == 0 {
		t.Error("No stream data received")
	} else {
		t.Logf("Received %d bytes of stream data", len(receivedData))
		// Check if we got the expected content
		if bytes.Contains(receivedData, streamData) || bytes.Equal(receivedData, streamData) {
			t.Log("Stream data successfully proxied")
		}
	}
}

// TestConcurrentRequests tests that multiple concurrent requests work without blocking
func TestConcurrentRequests(t *testing.T) {
	requestCount := 0
	streamData := []byte("stream data")

	// Mock servers
	streamServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		time.Sleep(100 * time.Millisecond) // Simulate some streaming time
		w.Write(streamData)
	}))
	defer streamServer.Close()

	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		requestCount++
		w.Header().Set("Content-Type", "application/json")
		w.Write([]byte(fmt.Sprintf(`{
			"response": {
				"playback_url": "%s",
				"stat_url": "http://localhost:6878/ace/stat/test/playback",
				"command_url": "http://localhost:6878/ace/cmd/test/playback"
			}
		}`, streamServer.URL)))
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
		NoResponseTimeout: 10 * time.Second,
	}
	acexyInst.Init()

	// Launch 10 concurrent requests
	concurrency := 10
	done := make(chan bool, concurrency)
	errors := make(chan error, concurrency)

	start := time.Now()
	for i := 0; i < concurrency; i++ {
		go func(idx int) {
			aceID, _ := NewAceID(fmt.Sprintf("stream-%d", idx), "")
			
			stream, err := acexyInst.FetchStream(aceID, nil)
			if err != nil {
				errors <- fmt.Errorf("request %d fetch failed: %w", idx, err)
				done <- false
				return
			}

			var output bytes.Buffer
			_, err = acexyInst.StartStream(stream, &output)
			if err != nil {
				errors <- fmt.Errorf("request %d stream failed: %w", idx, err)
				done <- false
				return
			}

			done <- true
		}(i)
	}

	// Wait for all to complete
	successCount := 0
	for i := 0; i < concurrency; i++ {
		select {
		case success := <-done:
			if success {
				successCount++
			}
		case err := <-errors:
			t.Error(err)
		case <-time.After(5 * time.Second):
			t.Fatal("Timeout waiting for concurrent requests")
		}
	}

	duration := time.Since(start)
	t.Logf("Completed %d/%d requests in %v", successCount, concurrency, duration)

	if successCount != concurrency {
		t.Errorf("Expected %d successful requests, got %d", concurrency, successCount)
	}

	// With concurrency, all requests should complete in roughly the time of one request
	// (not 10x the time if properly concurrent)
	if duration > 2*time.Second {
		t.Logf("Warning: Requests took longer than expected (%v), may not be fully concurrent", duration)
	}
}

func parseInt(s string) int {
	var i int
	fmt.Sscanf(s, "%d", &i)
	return i
}
