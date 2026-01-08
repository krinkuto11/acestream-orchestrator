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

// TestConcurrentEngineSelection verifies that concurrent requests
// can select engines without blocking. Without the pending stream tracking,
// multiple concurrent requests may select the same engine until the
// orchestrator's stream state is updated.
func TestConcurrentEngineSelection(t *testing.T) {
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
	}

	// Simulate concurrent requests trying to select an engine
	// Without pending stream tracking, requests won't block and
	// multiple requests can select the same engine based on
	// orchestrator's reported stream state
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

	// Verify that requests completed without deadlock
	selectionMu.Lock()
	defer selectionMu.Unlock()

	totalSelections := 0
	for engineID, count := range selectionCount {
		totalSelections += count
		t.Logf("Engine %s was selected %d times", engineID, count)
	}

	// Without blocking, the orchestrator's stream state controls capacity
	// Since mock returns empty streams, all requests should select the engine
	// up to max capacity, then provisioning kicks in
	if totalSelections == 0 {
		t.Error("Expected at least one successful selection")
	}
	t.Logf("Total selections: %d", totalSelections)
}

// TestEngineSelectionWithoutBlocking verifies that engine selection
// does not block and relies on orchestrator's stream state for capacity
func TestEngineSelectionWithoutBlocking(t *testing.T) {
	// Track timing to ensure no blocking
	startTime := time.Now()

	// Create a mock orchestrator server with slight delay
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/engines" {
			engines := []engineState{
				{
					ContainerID:     "test-engine-1",
					ContainerName:   "test-1",
					Host:            "localhost",
					Port:            19000,
					HealthStatus:    "healthy",
					LastHealthCheck: time.Now(),
					LastStreamUsage: time.Now().Add(-10 * time.Minute),
				},
			}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(engines)
			return
		}
		if r.URL.Path == "/streams" {
			// Return empty streams - engine has capacity
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
		maxStreamsPerEngine: 2,
		hc:                  &http.Client{Timeout: 3 * time.Second},
		ctx:                 ctx,
		cancel:              cancel,
	}

	// Make multiple sequential selections
	for i := 0; i < 3; i++ {
		host, port, containerID, err := client.SelectBestEngine()
		if err != nil {
			t.Logf("Selection %d failed: %v", i, err)
			continue
		}
		t.Logf("Selection %d: host=%s port=%d containerID=%s", i, host, port, containerID)
	}

	duration := time.Since(startTime)
	t.Logf("Total time for 3 selections: %v", duration)

	// Without blocking, selections should be fast (under 1 second for mock server)
	if duration > 5*time.Second {
		t.Errorf("Expected selections to complete quickly, but took %v", duration)
	}
}

