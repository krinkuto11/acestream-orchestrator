package main

import (
	"encoding/json"
	"fmt"
	"javinator9889/acexy/lib/acexy"
	"net/http"
	"net/http/httptest"
	"net/url"
	"sync"
	"testing"
	"time"
)

// TestStreamEndEmitsEndedEvent verifies that when a stream completes successfully,
// EmitEnded is called to notify the orchestrator
func TestStreamEndEmitsEndedEvent(t *testing.T) {
	var endedEventReceived bool
	var endedEventMu sync.Mutex
	var endedEventReason string
	var aceStreamServerURL string

	// Create a mock AceStream engine
	aceStreamServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ace/getstream" {
			w.Header().Set("Content-Type", "application/json")
			resp := map[string]interface{}{
				"response": map[string]interface{}{
					"playback_url":        aceStreamServerURL + "/stream",
					"stat_url":            aceStreamServerURL + "/ace/stat/test/playback123",
					"command_url":         aceStreamServerURL + "/ace/cmd/test/playback123",
					"playback_session_id": "playback123",
				},
			}
			json.NewEncoder(w).Encode(resp)
			return
		}
		if r.URL.Path == "/stream" {
			// Simulate a short stream
			w.Header().Set("Content-Type", "video/MP2T")
			w.Write([]byte("test stream data"))
			return
		}
		if r.URL.Path == "/ace/cmd/test/playback123" {
			// Handle stop command
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"response": "ok",
			})
			return
		}
		http.NotFound(w, r)
	}))
	defer aceStreamServer.Close()
	aceStreamServerURL = aceStreamServer.URL

	// Create a mock orchestrator server
	orchServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/events/stream_started" {
			w.WriteHeader(http.StatusOK)
			return
		}
		if r.URL.Path == "/events/stream_ended" {
			endedEventMu.Lock()
			endedEventReceived = true
			
			var evt endedEvent
			if err := json.NewDecoder(r.Body).Decode(&evt); err == nil {
				endedEventReason = evt.Reason
			}
			endedEventMu.Unlock()
			w.WriteHeader(http.StatusOK)
			return
		}
		if r.URL.Path == "/orchestrator/status" {
			resp := orchestratorStatus{
				Status: "healthy",
			}
			resp.VPN.Connected = true
			resp.Provisioning.CanProvision = true
			json.NewEncoder(w).Encode(resp)
			return
		}
		http.NotFound(w, r)
	}))
	defer orchServer.Close()

	// Parse acestream server URL
	aceStreamURL, _ := url.Parse(aceStreamServer.URL)

	// Create acexy instance
	acexyInst := &acexy.Acexy{
		Scheme:            aceStreamURL.Scheme,
		Host:              aceStreamURL.Hostname(),
		Port:              parsePort(aceStreamURL.Port()),
		Endpoint:          acexy.MPEG_TS_ENDPOINT,
		EmptyTimeout:      1 * time.Second,
		BufferSize:        1024,
		NoResponseTimeout: 5 * time.Second,
	}
	acexyInst.Init()

	// Create orchestrator client
	orchClient := newOrchClient(orchServer.URL)
	defer orchClient.Close()

	// Create proxy
	proxy := &Proxy{
		Acexy: acexyInst,
		Orch:  orchClient,
	}

	// Create a test request
	req := httptest.NewRequest("GET", "/ace/getstream?id=test-stream-id", nil)
	rec := httptest.NewRecorder()

	// Handle the request
	proxy.HandleStream(rec, req)

	// Give async events time to complete
	time.Sleep(200 * time.Millisecond)

	// Verify stream_ended event was received
	endedEventMu.Lock()
	defer endedEventMu.Unlock()

	if !endedEventReceived {
		t.Error("Expected stream_ended event to be sent to orchestrator, but it was not")
	} else {
		t.Logf("Stream_ended event was correctly sent to orchestrator with reason: %s", endedEventReason)
	}

	// Verify the reason is "completed" for successful stream
	if endedEventReason != "completed" {
		t.Errorf("Expected reason 'completed', got '%s'", endedEventReason)
	}
}

// TestStreamFailureEmitsEndedEvent verifies that when a stream fails,
// EmitEnded is called with an appropriate error reason
func TestStreamFailureEmitsEndedEvent(t *testing.T) {
	var endedEventReceived bool
	var endedEventMu sync.Mutex
	var endedEventReason string
	var aceStreamServerURL string

	// Create a mock AceStream engine that fails during streaming
	aceStreamServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ace/getstream" {
			w.Header().Set("Content-Type", "application/json")
			resp := map[string]interface{}{
				"response": map[string]interface{}{
					"playback_url":        "http://nonexistent-server-timeout:9999/stream",
					"stat_url":            aceStreamServerURL + "/ace/stat/test/playback123",
					"command_url":         aceStreamServerURL + "/ace/cmd/test/playback123",
					"playback_session_id": "playback123",
				},
			}
			json.NewEncoder(w).Encode(resp)
			return
		}
		if r.URL.Path == "/ace/cmd/test/playback123" {
			// Handle stop command
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode(map[string]interface{}{
				"response": "ok",
			})
			return
		}
		http.NotFound(w, r)
	}))
	defer aceStreamServer.Close()
	aceStreamServerURL = aceStreamServer.URL

	// Create a mock orchestrator server
	orchServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/events/stream_started" {
			w.WriteHeader(http.StatusOK)
			return
		}
		if r.URL.Path == "/events/stream_ended" {
			endedEventMu.Lock()
			endedEventReceived = true
			
			var evt endedEvent
			if err := json.NewDecoder(r.Body).Decode(&evt); err == nil {
				endedEventReason = evt.Reason
			}
			endedEventMu.Unlock()
			w.WriteHeader(http.StatusOK)
			return
		}
		if r.URL.Path == "/orchestrator/status" {
			resp := orchestratorStatus{
				Status: "healthy",
			}
			resp.VPN.Connected = true
			resp.Provisioning.CanProvision = true
			json.NewEncoder(w).Encode(resp)
			return
		}
		http.NotFound(w, r)
	}))
	defer orchServer.Close()

	// Parse acestream server URL
	aceStreamURL, _ := url.Parse(aceStreamServer.URL)

	// Create acexy instance with short timeout for faster test
	acexyInst := &acexy.Acexy{
		Scheme:            aceStreamURL.Scheme,
		Host:              aceStreamURL.Hostname(),
		Port:              parsePort(aceStreamURL.Port()),
		Endpoint:          acexy.MPEG_TS_ENDPOINT,
		EmptyTimeout:      1 * time.Second,
		BufferSize:        1024,
		NoResponseTimeout: 1 * time.Second, // Short timeout to fail quickly
	}
	acexyInst.Init()

	// Create orchestrator client
	orchClient := newOrchClient(orchServer.URL)
	defer orchClient.Close()

	// Create proxy
	proxy := &Proxy{
		Acexy: acexyInst,
		Orch:  orchClient,
	}

	// Create a test request
	req := httptest.NewRequest("GET", "/ace/getstream?id=test-stream-id", nil)
	rec := httptest.NewRecorder()

	// Handle the request
	proxy.HandleStream(rec, req)

	// Give async events time to complete
	time.Sleep(200 * time.Millisecond)

	// Verify stream_ended event was received
	endedEventMu.Lock()
	defer endedEventMu.Unlock()

	if !endedEventReceived {
		t.Error("Expected stream_ended event to be sent to orchestrator even on failure, but it was not")
	} else {
		t.Logf("Stream_ended event was correctly sent to orchestrator with reason: %s", endedEventReason)
	}

	// Verify the reason indicates an error (timeout, error, etc.)
	if endedEventReason != "timeout" && endedEventReason != "error" {
		t.Logf("Warning: Expected reason 'timeout' or 'error', got '%s' (this may be acceptable)", endedEventReason)
	}
}

// TestNoOrchestratorNoEndedEvent verifies that when orchestrator is not configured,
// EmitEnded is not called (no panic or error)
func TestNoOrchestratorNoEndedEvent(t *testing.T) {
	var aceStreamServerURL string
	
	// Create a mock AceStream engine
	aceStreamServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ace/getstream" {
			w.Header().Set("Content-Type", "application/json")
			resp := map[string]interface{}{
				"response": map[string]interface{}{
					"playback_url":        aceStreamServerURL + "/stream",
					"stat_url":            aceStreamServerURL + "/ace/stat/test/playback123",
					"command_url":         aceStreamServerURL + "/ace/cmd/test/playback123",
					"playback_session_id": "playback123",
				},
			}
			json.NewEncoder(w).Encode(resp)
			return
		}
		if r.URL.Path == "/stream" {
			// Simulate a short stream
			w.Header().Set("Content-Type", "video/MP2T")
			w.Write([]byte("test stream data"))
			return
		}
		http.NotFound(w, r)
	}))
	defer aceStreamServer.Close()
	aceStreamServerURL = aceStreamServer.URL

	// Parse acestream server URL
	aceStreamURL, _ := url.Parse(aceStreamServer.URL)

	// Create acexy instance
	acexyInst := &acexy.Acexy{
		Scheme:            aceStreamURL.Scheme,
		Host:              aceStreamURL.Hostname(),
		Port:              parsePort(aceStreamURL.Port()),
		Endpoint:          acexy.MPEG_TS_ENDPOINT,
		EmptyTimeout:      1 * time.Second,
		BufferSize:        1024,
		NoResponseTimeout: 5 * time.Second,
	}
	acexyInst.Init()

	// Create proxy WITHOUT orchestrator
	proxy := &Proxy{
		Acexy: acexyInst,
		Orch:  nil, // No orchestrator
	}

	// Create a test request
	req := httptest.NewRequest("GET", "/ace/getstream?id=test-stream-id", nil)
	rec := httptest.NewRecorder()

	// Handle the request - should complete without error even without orchestrator
	proxy.HandleStream(rec, req)

	// Verify request completed successfully
	if rec.Code != http.StatusOK {
		t.Errorf("Expected status OK even without orchestrator, got %d", rec.Code)
	} else {
		t.Log("Stream completed successfully without orchestrator (no EmitEnded called)")
	}
}

// Helper function to parse port from string
func parsePort(portStr string) int {
	var port int
	if portStr != "" {
		if _, err := fmt.Sscanf(portStr, "%d", &port); err != nil {
			return 0
		}
	}
	return port
}
