package main

import (
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

// TestParseProvisionError_StructuredFormat tests parsing of new structured error format
func TestParseProvisionError_StructuredFormat(t *testing.T) {
	testCases := []struct {
		name           string
		responseBody   string
		expectedCode   string
		expectedETA    int
		expectedWait   bool
		expectedRetry  bool
	}{
		{
			name: "VPN disconnected",
			responseBody: `{
				"detail": {
					"error": "provisioning_blocked",
					"code": "vpn_disconnected",
					"message": "VPN is disconnected",
					"recovery_eta_seconds": 60,
					"can_retry": true,
					"should_wait": true
				}
			}`,
			expectedCode:  "vpn_disconnected",
			expectedETA:   60,
			expectedWait:  true,
			expectedRetry: true,
		},
		{
			name: "Circuit breaker open",
			responseBody: `{
				"detail": {
					"error": "provisioning_blocked",
					"code": "circuit_breaker",
					"message": "Circuit breaker is open due to repeated failures",
					"recovery_eta_seconds": 180,
					"can_retry": true,
					"should_wait": true
				}
			}`,
			expectedCode:  "circuit_breaker",
			expectedETA:   180,
			expectedWait:  true,
			expectedRetry: true,
		},
		{
			name: "Max capacity reached",
			responseBody: `{
				"detail": {
					"error": "provisioning_blocked",
					"code": "max_capacity",
					"message": "All engines at capacity",
					"recovery_eta_seconds": 30,
					"can_retry": true,
					"should_wait": true
				}
			}`,
			expectedCode:  "max_capacity",
			expectedETA:   30,
			expectedWait:  true,
			expectedRetry: true,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			// Create a test server with the response body
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(http.StatusServiceUnavailable)
				w.Write([]byte(tc.responseBody))
			}))
			defer server.Close()
			
			// Make a request to get a real response
			testResp, err := http.Get(server.URL)
			if err != nil {
				t.Fatalf("Failed to get test response: %v", err)
			}
			defer testResp.Body.Close()

			// Parse the error
			provError, err := parseProvisionError(testResp)
			if err != nil {
				t.Fatalf("Failed to parse provision error: %v", err)
			}

			// Verify parsed values
			if provError.Code != tc.expectedCode {
				t.Errorf("Expected code %s, got %s", tc.expectedCode, provError.Code)
			}
			if provError.RecoveryETASeconds != tc.expectedETA {
				t.Errorf("Expected ETA %d, got %d", tc.expectedETA, provError.RecoveryETASeconds)
			}
			if provError.ShouldWait != tc.expectedWait {
				t.Errorf("Expected should_wait %v, got %v", tc.expectedWait, provError.ShouldWait)
			}
			if provError.CanRetry != tc.expectedRetry {
				t.Errorf("Expected can_retry %v, got %v", tc.expectedRetry, provError.CanRetry)
			}
		})
	}
}

// TestParseProvisionError_LegacyFormat tests parsing of legacy string error format
func TestParseProvisionError_LegacyFormat(t *testing.T) {
	testCases := []struct {
		name          string
		responseBody  string
		expectedCode  string
		expectedWait  bool
	}{
		{
			name:         "Legacy VPN error",
			responseBody: `{"detail": "VPN disconnected"}`,
			expectedCode: "vpn_disconnected",
			expectedWait: true,
		},
		{
			name:         "Legacy circuit breaker error",
			responseBody: `{"detail": "Circuit breaker is open"}`,
			expectedCode: "circuit_breaker",
			expectedWait: true,
		},
		{
			name:         "Legacy capacity error",
			responseBody: `{"detail": "At max capacity"}`,
			expectedCode: "max_capacity",
			expectedWait: true,
		},
		{
			name:         "Generic error",
			responseBody: `{"detail": "Some other error"}`,
			expectedCode: "general_error",
			expectedWait: false,
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
				w.WriteHeader(http.StatusServiceUnavailable)
				w.Write([]byte(tc.responseBody))
			}))
			defer server.Close()

			testResp, err := http.Get(server.URL)
			if err != nil {
				t.Fatalf("Failed to get test response: %v", err)
			}
			defer testResp.Body.Close()

			provError, err := parseProvisionError(testResp)
			if err != nil {
				t.Fatalf("Failed to parse provision error: %v", err)
			}

			if provError.Code != tc.expectedCode {
				t.Errorf("Expected code %s, got %s", tc.expectedCode, provError.Code)
			}
			if provError.ShouldWait != tc.expectedWait {
				t.Errorf("Expected should_wait %v, got %v", tc.expectedWait, provError.ShouldWait)
			}
		})
	}
}

// TestProvisionAcestream_StructuredError tests that ProvisionAcestream returns structured errors
func TestProvisionAcestream_StructuredError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"detail": map[string]interface{}{
				"error":                "provisioning_blocked",
				"code":                 "vpn_disconnected",
				"message":              "VPN is down",
				"recovery_eta_seconds": 60,
				"can_retry":            true,
				"should_wait":          true,
			},
		})
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
		t.Fatal("Expected error, got nil")
	}

	var provErr *ProvisioningError
	if !errors.As(err, &provErr) {
		t.Fatalf("Expected ProvisioningError, got %T: %v", err, err)
	}

	if provErr.StatusCode != http.StatusServiceUnavailable {
		t.Errorf("Expected status 503, got %d", provErr.StatusCode)
	}
	if provErr.Details.Code != "vpn_disconnected" {
		t.Errorf("Expected code vpn_disconnected, got %s", provErr.Details.Code)
	}
	if provErr.Details.RecoveryETASeconds != 60 {
		t.Errorf("Expected ETA 60, got %d", provErr.Details.RecoveryETASeconds)
	}
}

// TestUpdateHealth_EnhancedFields tests that updateHealth extracts enhanced status fields
func TestUpdateHealth_EnhancedFields(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/orchestrator/status" {
			t.Errorf("Expected /orchestrator/status, got %s", r.URL.Path)
			return
		}

		json.NewEncoder(w).Encode(map[string]interface{}{
			"status": "degraded",
			"vpn": map[string]bool{
				"connected": true,
			},
			"provisioning": map[string]interface{}{
				"can_provision":  false,
				"blocked_reason": "Circuit breaker is open",
				"blocked_reason_details": map[string]interface{}{
					"code":                 "circuit_breaker",
					"message":              "Too many failures",
					"recovery_eta_seconds": 180,
					"should_wait":          true,
				},
			},
			"capacity": map[string]int{
				"total":     10,
				"used":      8,
				"available": 2,
			},
		})
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

	// Call updateHealth
	client.updateHealth()

	// Verify health fields
	client.health.mu.RLock()
	defer client.health.mu.RUnlock()

	if client.health.status != "degraded" {
		t.Errorf("Expected status degraded, got %s", client.health.status)
	}
	if client.health.canProvision != false {
		t.Error("Expected canProvision false")
	}
	if client.health.blockedReasonCode != "circuit_breaker" {
		t.Errorf("Expected blocked reason code circuit_breaker, got %s", client.health.blockedReasonCode)
	}
	if client.health.recoveryETA != 180 {
		t.Errorf("Expected recovery ETA 180, got %d", client.health.recoveryETA)
	}
	if client.health.shouldWait != true {
		t.Error("Expected shouldWait true")
	}
	if client.health.capacity.Total != 10 {
		t.Errorf("Expected capacity total 10, got %d", client.health.capacity.Total)
	}
	if client.health.capacity.Available != 2 {
		t.Errorf("Expected capacity available 2, got %d", client.health.capacity.Available)
	}
}

// TestCalculateWaitTime tests the wait time calculation logic
func TestCalculateWaitTime(t *testing.T) {
	testCases := []struct {
		name        string
		recoveryETA int
		attempt     int
		expected    int
	}{
		{
			name:        "First retry with ETA",
			recoveryETA: 60,
			attempt:     1,
			expected:    30,
		},
		{
			name:        "Second retry with ETA",
			recoveryETA: 60,
			attempt:     2,
			expected:    60,
		},
		{
			name:        "No ETA - first attempt",
			recoveryETA: 0,
			attempt:     1,
			expected:    60, // 30 * 2^1
		},
		{
			name:        "No ETA - second attempt",
			recoveryETA: 0,
			attempt:     2,
			expected:    120, // 30 * 2^2 capped at 120
		},
		{
			name:        "No ETA - third attempt (capped)",
			recoveryETA: 0,
			attempt:     3,
			expected:    120, // Would be 240 but capped at 120
		},
	}

	for _, tc := range testCases {
		t.Run(tc.name, func(t *testing.T) {
			result := calculateWaitTime(tc.recoveryETA, tc.attempt)
			if result != tc.expected {
				t.Errorf("Expected %d seconds, got %d", tc.expected, result)
			}
		})
	}
}

// TestSelectBestEngine_StructuredError tests that SelectBestEngine returns structured errors
func TestSelectBestEngine_StructuredError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/engines" {
			// Return empty engine list (no capacity)
			json.NewEncoder(w).Encode([]engineState{})
		} else if r.URL.Path == "/orchestrator/status" {
			// Return degraded status with blocking reason
			json.NewEncoder(w).Encode(map[string]interface{}{
				"status": "degraded",
				"vpn": map[string]bool{
					"connected": false,
				},
				"provisioning": map[string]interface{}{
					"can_provision":  false,
					"blocked_reason": "VPN disconnected",
					"blocked_reason_details": map[string]interface{}{
						"code":                 "vpn_disconnected",
						"message":              "VPN is down",
						"recovery_eta_seconds": 60,
						"should_wait":          true,
					},
				},
				"capacity": map[string]int{
					"total":     0,
					"used":      0,
					"available": 0,
				},
			})
		}
	}))
	defer server.Close()

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:           server.URL,
		hc:             &http.Client{Timeout: 3 * time.Second},
		ctx:            ctx,
		cancel:         cancel,
		pendingStreams: make(map[string]int),
	}

	// Update health first
	client.updateHealth()

	// Try to select engine
	_, _, _, err := client.SelectBestEngine()
	if err == nil {
		t.Fatal("Expected error, got nil")
	}

	var provErr *ProvisioningError
	if !errors.As(err, &provErr) {
		t.Fatalf("Expected ProvisioningError, got %T: %v", err, err)
	}

	if provErr.Details.Code != "vpn_disconnected" {
		t.Errorf("Expected code vpn_disconnected, got %s", provErr.Details.Code)
	}
	if provErr.Details.RecoveryETASeconds != 60 {
		t.Errorf("Expected ETA 60, got %d", provErr.Details.RecoveryETASeconds)
	}
}

// TestGetProvisioningStatus tests the helper method
func TestGetProvisioningStatus(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	client := &orchClient{
		base:   "http://test",
		hc:     &http.Client{Timeout: 3 * time.Second},
		ctx:    ctx,
		cancel: cancel,
	}

	// Set health state
	client.health.canProvision = false
	client.health.shouldWait = true
	client.health.recoveryETA = 120

	canProvision, shouldWait, recoveryETA := client.GetProvisioningStatus()

	if canProvision {
		t.Error("Expected canProvision false")
	}
	if !shouldWait {
		t.Error("Expected shouldWait true")
	}
	if recoveryETA != 120 {
		t.Errorf("Expected recoveryETA 120, got %d", recoveryETA)
	}
}
