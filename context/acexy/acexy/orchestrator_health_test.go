package main

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func TestCanProvision(t *testing.T) {
	// Test nil client
	var nilClient *orchClient
	canProvision, reason := nilClient.CanProvision()
	if canProvision {
		t.Error("Expected false for nil client")
	}
	if reason != "orchestrator not configured" {
		t.Errorf("Expected 'orchestrator not configured', got '%s'", reason)
	}

	// Test with mocked health status
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:                "http://test",
		maxStreamsPerEngine: 1,
		hc:                  &http.Client{Timeout: 3 * time.Second},
		ctx:                 ctx,
		cancel:              cancel,
	}

	// Set health to allow provisioning
	client.health.canProvision = true
	client.health.blockedReason = ""

	canProvision, reason = client.CanProvision()
	if !canProvision {
		t.Error("Expected true when health allows provisioning")
	}
	if reason != "" {
		t.Errorf("Expected empty reason, got '%s'", reason)
	}

	// Set health to block provisioning
	client.health.canProvision = false
	client.health.blockedReason = "VPN disconnected"

	canProvision, reason = client.CanProvision()
	if canProvision {
		t.Error("Expected false when health blocks provisioning")
	}
	if reason != "VPN disconnected" {
		t.Errorf("Expected 'VPN disconnected', got '%s'", reason)
	}
}

func TestUpdateHealth(t *testing.T) {
	// Create a test server that returns orchestrator status
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/orchestrator/status" {
			t.Errorf("Expected /orchestrator/status path, got %s", r.URL.Path)
		}

		status := orchestratorStatus{
			Status: "healthy",
		}
		status.VPN.Connected = true
		status.Provisioning.CanProvision = true
		status.Provisioning.BlockedReason = ""

		w.Header().Set("Content-Type", "application/json")
		json.NewEncoder(w).Encode(status)
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:                server.URL,
		maxStreamsPerEngine: 1,
		hc:                  &http.Client{Timeout: 3 * time.Second},
		ctx:                 ctx,
		cancel:              cancel,
	}

	// Update health
	client.updateHealth()

	// Check that health was updated
	client.health.mu.RLock()
	defer client.health.mu.RUnlock()

	if client.health.status != "healthy" {
		t.Errorf("Expected status 'healthy', got '%s'", client.health.status)
	}
	if !client.health.vpnConnected {
		t.Error("Expected VPN to be connected")
	}
	if !client.health.canProvision {
		t.Error("Expected provisioning to be allowed")
	}
	if client.health.blockedReason != "" {
		t.Errorf("Expected empty blocked reason, got '%s'", client.health.blockedReason)
	}
}

func TestProvisionWithRetry(t *testing.T) {
	// Test temporary failures that should be retried
	attemptCount := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/provision/acestream" {
			t.Errorf("Expected /provision/acestream path, got %s", r.URL.Path)
		}

		attemptCount++
		if attemptCount < 2 {
			// First attempt fails with 503 (temporary)
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]string{
				"detail": "VPN disconnected",
			})
			return
		}

		// Second attempt succeeds
		w.WriteHeader(http.StatusOK)
		json.NewEncoder(w).Encode(aceProvisionResponse{
			ContainerID:       "test-container",
			ContainerName:     "test-acestream",
			HostHTTPPort:      19000,
			ContainerHTTPPort: 40000,
		})
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:                server.URL,
		maxStreamsPerEngine: 1,
		hc:                  &http.Client{Timeout: 3 * time.Second},
		ctx:                 ctx,
		cancel:              cancel,
	}

	// Should succeed after retry
	resp, err := client.ProvisionWithRetry(3)
	if err != nil {
		t.Errorf("Expected success after retry, got error: %v", err)
	}
	if resp == nil {
		t.Error("Expected non-nil response")
	}
	if attemptCount != 2 {
		t.Errorf("Expected 2 attempts, got %d", attemptCount)
	}
}

func TestProvisionWithRetryPermanentFailure(t *testing.T) {
	// Test permanent failures that should NOT be retried
	attemptCount := 0
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		attemptCount++
		// Always fail with 500 (permanent)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{
			"detail": "Configuration error",
		})
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:                server.URL,
		maxStreamsPerEngine: 1,
		hc:                  &http.Client{Timeout: 3 * time.Second},
		ctx:                 ctx,
		cancel:              cancel,
	}

	// Should fail immediately without retries
	_, err := client.ProvisionWithRetry(3)
	if err == nil {
		t.Error("Expected error for permanent failure")
	}
	if attemptCount != 1 {
		t.Errorf("Expected 1 attempt (no retries for permanent failure), got %d", attemptCount)
	}
}

func TestSelectBestEngineProvisioningBlocked(t *testing.T) {
	// Test that SelectBestEngine checks health before provisioning
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/engines" {
			// Return empty list - no engines available
			w.Header().Set("Content-Type", "application/json")
			json.NewEncoder(w).Encode([]engineState{})
			return
		}
		t.Errorf("Unexpected request to %s", r.URL.Path)
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:                server.URL,
		maxStreamsPerEngine: 1,
		hc:                  &http.Client{Timeout: 3 * time.Second},
		ctx:                 ctx,
		cancel:              cancel,
	}

	// Block provisioning
	client.health.canProvision = false
	client.health.blockedReason = "VPN disconnected"

	// Should fail with provisioning blocked error
	_, _, _, err := client.SelectBestEngine()
	if err == nil {
		t.Error("Expected error when provisioning is blocked")
	}
	if !strings.Contains(err.Error(), "cannot provision") {
		t.Errorf("Expected 'cannot provision' error, got: %v", err)
	}
	if !strings.Contains(err.Error(), "VPN disconnected") {
		t.Errorf("Expected 'VPN disconnected' in error, got: %v", err)
	}
}
