package acexy

import (
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"
)

// TestGetStreamTimeout verifies that GetStream respects the NoResponseTimeout
func TestGetStreamTimeout(t *testing.T) {
	// Create a test server that hangs and never responds
	hangingServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate a hanging server by sleeping longer than the timeout
		time.Sleep(5 * time.Second)
		w.WriteHeader(http.StatusOK)
	}))
	defer hangingServer.Close()

	// Parse the test server URL to get host and port
	serverURL, err := url.Parse(hangingServer.URL)
	if err != nil {
		t.Fatalf("Failed to parse test server URL: %v", err)
	}

	// Create an Acexy instance with a short timeout
	acexy := &Acexy{
		Scheme:            serverURL.Scheme,
		Host:              serverURL.Hostname(),
		Port:              80, // Will be overridden by the actual server port
		Endpoint:          MPEG_TS_ENDPOINT,
		NoResponseTimeout: 500 * time.Millisecond, // Short timeout for testing
	}

	// Override port with actual test server port
	if serverURL.Port() != "" {
		acexy.Port = 0
		// We'll use the full URL by reconstructing it
		acexy.Host = serverURL.Host
	}

	// Create a test AceID
	aceID, err := NewAceID("test123", "")
	if err != nil {
		t.Fatalf("Failed to create AceID: %v", err)
	}

	// Attempt to get the stream - should timeout instead of hanging
	start := time.Now()
	_, err = GetStream(acexy, aceID, url.Values{})
	elapsed := time.Since(start)

	// Verify that we got an error (timeout)
	if err == nil {
		t.Error("Expected timeout error, got nil")
	}

	// Verify that it timed out quickly (within reasonable bounds of our timeout)
	// Should be close to 500ms, allow up to 2 seconds for test overhead
	if elapsed > 2*time.Second {
		t.Errorf("GetStream took too long: %v (expected ~500ms timeout)", elapsed)
	}

	t.Logf("GetStream correctly timed out after %v", elapsed)
}

// TestCloseStreamTimeout verifies that CloseStream has a timeout
func TestCloseStreamTimeout(t *testing.T) {
	// Create a test server that hangs and never responds
	hangingServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Simulate a hanging server
		time.Sleep(15 * time.Second)
		w.WriteHeader(http.StatusOK)
	}))
	defer hangingServer.Close()

	// Create a test stream with the hanging server's URL as command URL
	stream := &AceStream{
		CommandURL: hangingServer.URL,
	}

	// Attempt to close the stream - should timeout instead of hanging
	start := time.Now()
	err := CloseStream(stream)
	elapsed := time.Since(start)

	// Verify that we got an error (timeout)
	if err == nil {
		t.Error("Expected timeout error, got nil")
	}

	// Verify that it timed out within reasonable bounds (10s + overhead)
	// Should be close to 10s, allow up to 12s for test overhead
	if elapsed > 12*time.Second {
		t.Errorf("CloseStream took too long: %v (expected ~10s timeout)", elapsed)
	}

	// Verify it didn't complete too quickly (should at least attempt the request)
	if elapsed < 9*time.Second {
		t.Errorf("CloseStream completed too quickly: %v (expected ~10s timeout)", elapsed)
	}

	t.Logf("CloseStream correctly timed out after %v", elapsed)
}
