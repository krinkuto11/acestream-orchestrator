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

// TestEmitEndedIdempotency verifies that multiple calls to EmitEnded
// for the same stream only result in one event being sent
func TestEmitEndedIdempotency(t *testing.T) {
	eventCount := 0
	var eventMu sync.Mutex

	// Create a mock orchestrator server that counts events
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/events/stream_ended" {
			eventMu.Lock()
			eventCount++
			eventMu.Unlock()
			w.WriteHeader(http.StatusOK)
			return
		}
		t.Errorf("Unexpected request to %s", r.URL.Path)
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:           server.URL,
		hc:             &http.Client{Timeout: 3 * time.Second},
		ctx:            ctx,
		cancel:         cancel,
		endedStreams:   make(map[string]bool),
	}

	streamID := "test-stream-123"

	// Call EmitEnded multiple times concurrently
	var wg sync.WaitGroup
	numCalls := 10
	wg.Add(numCalls)

	for i := 0; i < numCalls; i++ {
		go func(reason string) {
			defer wg.Done()
			client.EmitEnded(streamID, reason)
		}("test_reason")
	}

	wg.Wait()

	// Give async events time to complete
	time.Sleep(100 * time.Millisecond)

	// Verify only one event was sent
	eventMu.Lock()
	defer eventMu.Unlock()

	if eventCount != 1 {
		t.Errorf("Expected exactly 1 stream_ended event, got %d", eventCount)
	} else {
		t.Logf("Idempotency working correctly: %d calls resulted in %d event", numCalls, eventCount)
	}

	// Verify the stream is marked as ended
	client.endedStreamsMu.Lock()
	isEnded := client.endedStreams[streamID]
	client.endedStreamsMu.Unlock()

	if !isEnded {
		t.Error("Stream should be marked as ended in tracking map")
	}
}

// TestEngineListCaching verifies that engine list is cached to reduce
// concurrent queries to the orchestrator
func TestEngineListCaching(t *testing.T) {
	queryCount := 0
	var queryMu sync.Mutex

	// Create a mock orchestrator server that counts queries
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/engines" {
			queryMu.Lock()
			queryCount++
			queryMu.Unlock()

			engines := []engineState{
				{
					ContainerID:     "test-engine-1",
					Host:            "localhost",
					Port:            19000,
					HealthStatus:    "healthy",
					LastHealthCheck: time.Now(),
				},
			}
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(engines)
			return
		}
		t.Errorf("Unexpected request to %s", r.URL.Path)
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:                server.URL,
		hc:                  &http.Client{Timeout: 3 * time.Second},
		ctx:                 ctx,
		cancel:              cancel,
		engineCacheDuration: 2 * time.Second,
		endedStreams:        make(map[string]bool),
	}

	// Make multiple concurrent calls to GetEngines
	// First call to populate cache
	firstEngines, firstErr := client.GetEngines()
	if firstErr != nil {
		t.Fatalf("Initial GetEngines failed: %v", firstErr)
	}
	if len(firstEngines) == 0 {
		t.Fatal("Expected at least one engine")
	}

	// Reset counter after initial call
	queryMu.Lock()
	queryCount = 0
	queryMu.Unlock()

	// Now make concurrent calls that should hit the cache
	var wg sync.WaitGroup
	numCalls := 20
	wg.Add(numCalls)

	for i := 0; i < numCalls; i++ {
		go func() {
			defer wg.Done()
			cEngines, cErr := client.GetEngines()
			if cErr != nil {
				t.Errorf("GetEngines failed: %v", cErr)
			}
			if len(cEngines) == 0 {
				t.Error("Expected engines from cache")
			}
		}()
	}

	wg.Wait()

	// Verify that fewer queries were made due to caching
	queryMu.Lock()
	finalQueryCount := queryCount
	queryMu.Unlock()

	if finalQueryCount >= numCalls {
		t.Errorf("Expected caching to reduce queries, but got %d queries for %d calls", finalQueryCount, numCalls)
	} else {
		t.Logf("Caching working: %d calls resulted in only %d queries", numCalls, finalQueryCount)
	}
}

// TestEventOrdering verifies that stream_started is sent before stream_ended
func TestEventOrdering(t *testing.T) {
	events := []string{}
	var eventMu sync.Mutex

	// Create a mock orchestrator server that records event order
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/events/stream_started" {
			eventMu.Lock()
			events = append(events, "started")
			eventMu.Unlock()
			w.WriteHeader(http.StatusOK)
			return
		}
		if r.URL.Path == "/events/stream_ended" {
			eventMu.Lock()
			events = append(events, "ended")
			eventMu.Unlock()
			w.WriteHeader(http.StatusOK)
			return
		}
		t.Errorf("Unexpected request to %s", r.URL.Path)
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:           server.URL,
		hc:             &http.Client{Timeout: 3 * time.Second},
		ctx:            ctx,
		cancel:         cancel,
		endedStreams:   make(map[string]bool),
	}

	streamID := "test-stream-123"

	// Emit started (synchronous)
	client.EmitStarted("localhost", 19000, "infohash", "testkey", "playback123",
		"http://stat", "http://cmd", streamID, "engine-1")

	// Emit ended immediately after (async)
	client.EmitEnded(streamID, "test")

	// Wait for async events to complete
	time.Sleep(100 * time.Millisecond)

	// Verify event order
	eventMu.Lock()
	defer eventMu.Unlock()

	if len(events) < 2 {
		t.Fatalf("Expected at least 2 events, got %d", len(events))
	}

	if events[0] != "started" {
		t.Errorf("First event should be 'started', got '%s'", events[0])
	}

	if events[1] != "ended" {
		t.Errorf("Second event should be 'ended', got '%s'", events[1])
	}

	t.Logf("Event ordering correct: %v", events)
}

// TestCleanupMonitor verifies that the cleanup monitor properly manages tracking maps
func TestCleanupMonitor(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:                "http://test",
		hc:                  &http.Client{Timeout: 3 * time.Second},
		ctx:                 ctx,
		cancel:              cancel,
		endedStreams:        make(map[string]bool),
		engineCacheDuration: 2 * time.Second,
	}

	// Add many ended streams to trigger cleanup
	for i := 0; i < 1500; i++ {
		streamID := "stream-" + string(rune(i))
		client.endedStreams[streamID] = true
	}

	initialSize := len(client.endedStreams)
	t.Logf("Initial ended streams map size: %d", initialSize)

	// Run cleanup
	client.cleanupStaleData()

	finalSize := len(client.endedStreams)
	t.Logf("Final ended streams map size: %d", finalSize)

	if finalSize >= initialSize {
		t.Errorf("Cleanup should have reduced map size from %d, but got %d", initialSize, finalSize)
	}
}

// TestEmitEndedWithEmptyStreamID verifies that EmitEnded handles empty streamID gracefully
func TestEmitEndedWithEmptyStreamID(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:           "http://test",
		hc:             &http.Client{Timeout: 3 * time.Second},
		ctx:            ctx,
		cancel:         cancel,
		endedStreams:   make(map[string]bool),
	}

	// Should not panic or cause issues
	client.EmitEnded("", "test_reason")

	// Verify no entry was added
	client.endedStreamsMu.Lock()
	size := len(client.endedStreams)
	client.endedStreamsMu.Unlock()

	if size != 0 {
		t.Errorf("Empty streamID should not add entry to map, but got size %d", size)
	}

	t.Log("Empty streamID handled gracefully")
}
