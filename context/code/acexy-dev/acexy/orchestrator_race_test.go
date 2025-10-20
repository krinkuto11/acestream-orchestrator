package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"sync"
	"testing"
	"time"
)

// TestPendingStreamTracking verifies that the pending stream tracking
// prevents the race condition where multiple concurrent requests select
// the same engine before the orchestrator is updated.
func TestPendingStreamTracking(t *testing.T) {
	// Track how many times the same engine gets selected
	selectionCount := make(map[string]int)
	var selectionMu sync.Mutex

	// Create a mock orchestrator server
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/engines" {
			// Return one engine with capacity for 2 streams
			engines := []engineState{
				{
					ContainerID:     "test-engine-1",
					ContainerName:   "test-1",
					Host:            "localhost",
					Port:            19000,
					HealthStatus:    "healthy",
					LastHealthCheck: time.Now(),
					LastStreamUsage: time.Time{}, // Never used
				},
			}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(engines)
			return
		}
		if r.URL.Path == "/streams" {
			// Always return empty - simulating the race condition scenario
			// where orchestrator hasn't been updated yet
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode([]streamState{})
			return
		}
		t.Errorf("Unexpected request to %s", r.URL.Path)
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:                server.URL,
		maxStreamsPerEngine: 2, // Max 2 streams per engine
		hc:                  &http.Client{Timeout: 3 * time.Second},
		ctx:                 ctx,
		cancel:              cancel,
		pendingStreams:      make(map[string]int),
	}

	// Simulate 5 concurrent requests trying to select an engine
	// Without pending stream tracking, all 5 would select the same engine
	// With pending stream tracking, only 2 should select it (the max)
	numRequests := 5
	var wg sync.WaitGroup
	wg.Add(numRequests)

	for i := 0; i < numRequests; i++ {
		go func() {
			defer wg.Done()
			host, port, containerID, err := client.SelectBestEngine()
			if err == nil {
				selectionMu.Lock()
				selectionCount[containerID]++
				selectionMu.Unlock()
				t.Logf("Selected engine: host=%s port=%d containerID=%s", host, port, containerID)
			} else {
				t.Logf("Selection failed (expected when at capacity): %v", err)
			}
		}()
	}

	wg.Wait()

	// Verify that the engine was selected at most maxStreamsPerEngine times
	selectionMu.Lock()
	defer selectionMu.Unlock()

	for engineID, count := range selectionCount {
		if count > client.maxStreamsPerEngine {
			t.Errorf("Engine %s was selected %d times, but max is %d", 
				engineID, count, client.maxStreamsPerEngine)
		} else {
			t.Logf("Engine %s was correctly selected %d times (max=%d)", 
				engineID, count, client.maxStreamsPerEngine)
		}
	}

	// Verify pending streams were tracked
	client.pendingStreamsMu.Lock()
	pendingCount := client.pendingStreams["test-engine-1"]
	client.pendingStreamsMu.Unlock()

	t.Logf("Final pending stream count: %d", pendingCount)
	if pendingCount != client.maxStreamsPerEngine {
		t.Errorf("Expected pending count to be %d, got %d", client.maxStreamsPerEngine, pendingCount)
	}
}

// TestPendingStreamRelease verifies that pending streams are released
// after EmitStarted is called.
func TestPendingStreamRelease(t *testing.T) {
	client := &orchClient{
		base:                "http://test",
		maxStreamsPerEngine: 2,
		hc:                  &http.Client{Timeout: 3 * time.Second},
		pendingStreams:      make(map[string]int),
	}

	// Manually set a pending stream
	engineID := "test-engine-1"
	client.pendingStreamsMu.Lock()
	client.pendingStreams[engineID] = 2
	client.pendingStreamsMu.Unlock()

	// Verify it's set
	client.pendingStreamsMu.Lock()
	count := client.pendingStreams[engineID]
	client.pendingStreamsMu.Unlock()
	if count != 2 {
		t.Errorf("Expected pending count to be 2, got %d", count)
	}

	// Release one stream
	client.ReleasePendingStream(engineID)

	// Verify count decreased
	client.pendingStreamsMu.Lock()
	count = client.pendingStreams[engineID]
	client.pendingStreamsMu.Unlock()
	if count != 1 {
		t.Errorf("Expected pending count to be 1 after release, got %d", count)
	}

	// Release another
	client.ReleasePendingStream(engineID)

	// Verify map entry is cleaned up when count reaches 0
	client.pendingStreamsMu.Lock()
	_, exists := client.pendingStreams[engineID]
	client.pendingStreamsMu.Unlock()
	if exists {
		t.Error("Expected pending streams map entry to be cleaned up when count reaches 0")
	}

	// Verify releasing when already at 0 doesn't cause issues
	client.ReleasePendingStream(engineID)
	
	t.Log("Pending stream release working correctly")
}
