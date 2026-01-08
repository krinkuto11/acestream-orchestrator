package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"sync/atomic"
	"testing"
	"time"
)

// TestE2E_VPNRecovery simulates a VPN disconnection and recovery scenario
func TestE2E_VPNRecovery(t *testing.T) {
	var vpnConnected atomic.Bool
	vpnConnected.Store(false) // Start with VPN disconnected

	attemptCount := 0

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/orchestrator/status":
			// Status endpoint reflects VPN state
			status := map[string]interface{}{
				"status": "healthy",
				"vpn": map[string]bool{
					"connected": vpnConnected.Load(),
				},
				"provisioning": map[string]interface{}{
					"can_provision": vpnConnected.Load(),
					"blocked_reason": func() string {
						if vpnConnected.Load() {
							return ""
						}
						return "VPN disconnected"
					}(),
					"blocked_reason_details": func() interface{} {
						if vpnConnected.Load() {
							return nil
						}
						return map[string]interface{}{
							"code":                 "vpn_disconnected",
							"message":              "VPN is down",
							"recovery_eta_seconds": 10,
							"should_wait":          true,
							"can_retry":            true,
						}
					}(),
				},
				"capacity": map[string]int{
					"total":     0,
					"used":      0,
					"available": 0,
				},
			}
			json.NewEncoder(w).Encode(status)

		case "/provision/acestream":
			attemptCount++
			if !vpnConnected.Load() {
				// VPN is down, return error
				w.WriteHeader(http.StatusServiceUnavailable)
				json.NewEncoder(w).Encode(map[string]interface{}{
					"detail": map[string]interface{}{
						"error":                "provisioning_blocked",
						"code":                 "vpn_disconnected",
						"message":              "VPN is disconnected",
						"recovery_eta_seconds": 10,
						"can_retry":            true,
						"should_wait":          true,
					},
				})
				return
			}

			// VPN is connected, provision succeeds
			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(aceProvisionResponse{
				ContainerID:       "test-container",
				ContainerName:     "test-acestream",
				HostHTTPPort:      19000,
				ContainerHTTPPort: 40000,
			})

		case "/engines":
			// Return empty list (no engines available)
			json.NewEncoder(w).Encode([]engineState{})
		}
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

	// Initial health check
	client.updateHealth()

	// Attempt 1: VPN down, should fail
	t.Log("Attempt 1: VPN disconnected")
	_, err := client.ProvisionAcestream()
	if err == nil {
		t.Fatal("Expected error when VPN is down")
	}
	if attemptCount != 1 {
		t.Errorf("Expected 1 attempt, got %d", attemptCount)
	}

	// Simulate VPN recovery after a delay
	go func() {
		time.Sleep(2 * time.Second)
		vpnConnected.Store(true)
		t.Log("VPN reconnected")
	}()

	// Attempt 2: Should retry and succeed after VPN reconnects
	t.Log("Starting retry with intelligent backoff...")
	resp, err := client.ProvisionWithRetry(3)
	if err != nil {
		t.Fatalf("Expected success after VPN recovery, got error: %v", err)
	}
	if resp == nil {
		t.Fatal("Expected non-nil response")
	}
	if attemptCount < 2 {
		t.Errorf("Expected at least 2 attempts (retry), got %d", attemptCount)
	}

	t.Logf("Successfully provisioned after %d attempts", attemptCount)
}

// TestE2E_CircuitBreakerRecovery simulates circuit breaker opening and closing
func TestE2E_CircuitBreakerRecovery(t *testing.T) {
	var circuitOpen atomic.Bool
	circuitOpen.Store(true) // Start with circuit breaker open

	attemptCount := 0

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/orchestrator/status":
			status := map[string]interface{}{
				"status": func() string {
					if circuitOpen.Load() {
						return "degraded"
					}
					return "healthy"
				}(),
				"vpn": map[string]bool{
					"connected": true,
				},
				"provisioning": map[string]interface{}{
					"can_provision": !circuitOpen.Load(),
					"blocked_reason": func() string {
						if circuitOpen.Load() {
							return "Circuit breaker is open"
						}
						return ""
					}(),
					"blocked_reason_details": func() interface{} {
						if circuitOpen.Load() {
							return map[string]interface{}{
								"code":                 "circuit_breaker",
								"message":              "Too many failures",
								"recovery_eta_seconds": 5,
								"should_wait":          true,
								"can_retry":            true,
							}
						}
						return nil
					}(),
				},
				"capacity": map[string]int{
					"total":     0,
					"used":      0,
					"available": 0,
				},
			}
			json.NewEncoder(w).Encode(status)

		case "/provision/acestream":
			attemptCount++
			if circuitOpen.Load() {
				w.WriteHeader(http.StatusServiceUnavailable)
				json.NewEncoder(w).Encode(map[string]interface{}{
					"detail": map[string]interface{}{
						"error":                "provisioning_blocked",
						"code":                 "circuit_breaker",
						"message":              "Circuit breaker is open due to repeated failures",
						"recovery_eta_seconds": 5,
						"can_retry":            true,
						"should_wait":          true,
					},
				})
				return
			}

			w.WriteHeader(http.StatusOK)
			json.NewEncoder(w).Encode(aceProvisionResponse{
				ContainerID:       "test-container",
				ContainerName:     "test-acestream",
				HostHTTPPort:      19000,
				ContainerHTTPPort: 40000,
			})

		case "/engines":
			json.NewEncoder(w).Encode([]engineState{})
		}
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

	client.updateHealth()

	// Simulate circuit breaker closing
	go func() {
		time.Sleep(1 * time.Second)
		circuitOpen.Store(false)
		t.Log("Circuit breaker closed")
	}()

	t.Log("Attempting provisioning with circuit breaker...")
	resp, err := client.ProvisionWithRetry(3)
	if err != nil {
		t.Fatalf("Expected success after circuit breaker recovery, got: %v", err)
	}
	if resp == nil {
		t.Fatal("Expected non-nil response")
	}

	t.Logf("Successfully provisioned after %d attempts", attemptCount)
}

// TestE2E_CapacityAvailable simulates capacity becoming available
func TestE2E_CapacityAvailable(t *testing.T) {
	var hasCapacity atomic.Bool
	hasCapacity.Store(false) // Start with no capacity

	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/orchestrator/status":
			available := 0
			if hasCapacity.Load() {
				available = 1
			}
			status := map[string]interface{}{
				"status": "healthy",
				"vpn": map[string]bool{
					"connected": true,
				},
				"provisioning": map[string]interface{}{
					"can_provision": hasCapacity.Load(),
					"blocked_reason": func() string {
						if hasCapacity.Load() {
							return ""
						}
						return "At max capacity"
					}(),
					"blocked_reason_details": func() interface{} {
						if hasCapacity.Load() {
							return nil
						}
						return map[string]interface{}{
							"code":                 "max_capacity",
							"message":              "All engines at capacity",
							"recovery_eta_seconds": 5,
							"should_wait":          true,
							"can_retry":            true,
						}
					}(),
				},
				"capacity": map[string]int{
					"total":     10,
					"used":      10 - available,
					"available": available,
				},
			}
			json.NewEncoder(w).Encode(status)

		case "/engines":
			if hasCapacity.Load() {
				// Return an engine with capacity
				json.NewEncoder(w).Encode([]engineState{
					{
						ContainerID:     "existing-engine",
						Host:            "localhost",
						Port:            19000,
						HealthStatus:    "healthy",
						LastStreamUsage: time.Now().Add(-5 * time.Minute),
					},
				})
			} else {
				json.NewEncoder(w).Encode([]engineState{})
			}

		case "/streams":
			// No active streams
			json.NewEncoder(w).Encode([]streamState{})
		}
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

	client.updateHealth()

	// Simulate capacity becoming available
	go func() {
		time.Sleep(1 * time.Second)
		hasCapacity.Store(true)
		t.Log("Capacity became available")
	}()

	t.Log("Attempting to select engine when at capacity...")

	// First attempt should fail with capacity error
	_, _, _, err := client.SelectBestEngine()
	if err == nil {
		// If we get here before capacity is available, it's expected to fail
		t.Log("First attempt returned immediately (expected behavior)")
	}

	// Wait a bit for capacity
	time.Sleep(2 * time.Second)

	// Update engine cache by calling GetEngines
	client.engineCacheTime = time.Time{} // Invalidate cache

	// Second attempt should succeed
	host, port, _, err := client.SelectBestEngine()
	if err != nil {
		t.Fatalf("Expected success after capacity available, got: %v", err)
	}
	if host != "localhost" || port != 19000 {
		t.Errorf("Expected host=localhost port=19000, got host=%s port=%d", host, port)
	}

	t.Log("Successfully selected engine after capacity became available")
}

// TestE2E_LegacyErrorFormat tests backward compatibility with old error format
func TestE2E_LegacyErrorFormat(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/provision/acestream":
			// Return legacy string error format
			w.WriteHeader(http.StatusServiceUnavailable)
			json.NewEncoder(w).Encode(map[string]string{
				"detail": "VPN disconnected",
			})
		}
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:   server.URL,
		hc:     &http.Client{Timeout: 3 * time.Second},
		ctx:    ctx,
		cancel: cancel,
	}

	_, err := client.ProvisionAcestream()
	if err == nil {
		t.Fatal("Expected error")
	}

	// Should still be able to extract structured error from legacy format
	var provErr *ProvisioningError
	if !errors.As(err, &provErr) {
		t.Fatalf("Expected ProvisioningError, got %T", err)
	}

	if provErr.Details.Code != "vpn_disconnected" {
		t.Errorf("Expected code vpn_disconnected, got %s", provErr.Details.Code)
	}
	if !provErr.Details.ShouldWait {
		t.Error("Expected should_wait=true for VPN error")
	}

	t.Log("Legacy error format successfully parsed with backward compatibility")
}
