package debug

import (
	"bytes"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestDebugLogger_Disabled(t *testing.T) {
	// Create logger with debug disabled
	logger := NewDebugLogger(false, "/tmp/test")

	// Log some events
	logger.LogRequest("GET", "/test", 100*time.Millisecond, 200, "test_ace_id")
	logger.LogEngineSelection("select", "localhost", 6878, "container1", 50*time.Millisecond, "")

	// Verify no files were created
	files, _ := filepath.Glob("/tmp/test/*.jsonl")
	if len(files) > 0 {
		t.Errorf("Expected no log files when disabled, got %d files", len(files))
	}
}

func TestDebugLogger_Request(t *testing.T) {
	tempDir := t.TempDir()
	logger := NewDebugLogger(true, tempDir)

	// Log a request
	logger.LogRequest("GET", "/ace/getstream", 100*time.Millisecond, 200, "test_ace_id_123")

	// Verify log file exists
	files, _ := filepath.Glob(filepath.Join(tempDir, "*_requests.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 request log file, got %d", len(files))
	}

	// Verify log content
	data, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatalf("Failed to read log file: %v", err)
	}

	// Parse the lines (session start goes to session.jsonl, not requests.jsonl)
	lines := parseJSONLines(t, data)
	if len(lines) < 1 {
		t.Fatalf("Expected at least 1 log entry, got %d", len(lines))
	}

	entry := lines[0]
	if entry["method"] != "GET" {
		t.Errorf("Expected method GET, got %v", entry["method"])
	}
	if entry["path"] != "/ace/getstream" {
		t.Errorf("Expected path /ace/getstream, got %v", entry["path"])
	}
	if entry["status_code"] != float64(200) {
		t.Errorf("Expected status_code 200, got %v", entry["status_code"])
	}
	if entry["ace_id"] != "test_ace_id_123" {
		t.Errorf("Expected ace_id test_ace_id_123, got %v", entry["ace_id"])
	}
}

func TestDebugLogger_EngineSelection(t *testing.T) {
	tempDir := t.TempDir()
	logger := NewDebugLogger(true, tempDir)

	// Log engine selection
	logger.LogEngineSelection("select_best_engine", "localhost", 19000, "container-abc", 250*time.Millisecond, "")

	// Verify log file exists
	files, _ := filepath.Glob(filepath.Join(tempDir, "*_engine_selection.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 engine_selection log file, got %d", len(files))
	}

	// Verify log content
	data, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatalf("Failed to read log file: %v", err)
	}

	lines := parseJSONLines(t, data)
	if len(lines) < 1 {
		t.Fatalf("Expected at least 1 log entry, got %d", len(lines))
	}

	entry := lines[0]
	if entry["operation"] != "select_best_engine" {
		t.Errorf("Expected operation select_best_engine, got %v", entry["operation"])
	}
	if entry["selected_host"] != "localhost" {
		t.Errorf("Expected selected_host localhost, got %v", entry["selected_host"])
	}
	if entry["selected_port"] != float64(19000) {
		t.Errorf("Expected selected_port 19000, got %v", entry["selected_port"])
	}
	if entry["container_id"] != "container-abc" {
		t.Errorf("Expected container_id container-abc, got %v", entry["container_id"])
	}
}

func TestDebugLogger_Provisioning(t *testing.T) {
	tempDir := t.TempDir()
	logger := NewDebugLogger(true, tempDir)

	// Log provisioning success
	logger.LogProvisioning("provision_success", 2*time.Second, true, "", 2)

	// Verify log file exists
	files, _ := filepath.Glob(filepath.Join(tempDir, "*_provisioning.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 provisioning log file, got %d", len(files))
	}

	// Verify log content
	data, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatalf("Failed to read log file: %v", err)
	}

	lines := parseJSONLines(t, data)
	if len(lines) < 1 {
		t.Fatalf("Expected at least 1 log entry, got %d", len(lines))
	}

	entry := lines[0]
	if entry["operation"] != "provision_success" {
		t.Errorf("Expected operation provision_success, got %v", entry["operation"])
	}
	if entry["success"] != true {
		t.Errorf("Expected success true, got %v", entry["success"])
	}
	if entry["retry_count"] != float64(2) {
		t.Errorf("Expected retry_count 2, got %v", entry["retry_count"])
	}
}

func TestDebugLogger_OrchestratorHealth(t *testing.T) {
	tempDir := t.TempDir()
	logger := NewDebugLogger(true, tempDir)

	// Log orchestrator health
	logger.LogOrchestratorHealth("healthy", true, "", "", 0, true, 10, 3, 7)

	// Verify log file exists
	files, _ := filepath.Glob(filepath.Join(tempDir, "*_orchestrator_health.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 orchestrator_health log file, got %d", len(files))
	}

	// Verify log content
	data, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatalf("Failed to read log file: %v", err)
	}

	lines := parseJSONLines(t, data)
	if len(lines) < 1 {
		t.Fatalf("Expected at least 1 log entry, got %d", len(lines))
	}

	entry := lines[0]
	if entry["status"] != "healthy" {
		t.Errorf("Expected status healthy, got %v", entry["status"])
	}
	if entry["can_provision"] != true {
		t.Errorf("Expected can_provision true, got %v", entry["can_provision"])
	}
	if entry["capacity_total"] != float64(10) {
		t.Errorf("Expected capacity_total 10, got %v", entry["capacity_total"])
	}
}

func TestDebugLogger_StreamEvent(t *testing.T) {
	tempDir := t.TempDir()
	logger := NewDebugLogger(true, tempDir)

	// Log stream event with additional data
	additionalData := map[string]interface{}{
		"client_count": 2,
		"buffer_size":  1024,
	}
	logger.LogStreamEvent("stream_started", "stream-123", "engine-456", 100*time.Millisecond, additionalData)

	// Verify log file exists
	files, _ := filepath.Glob(filepath.Join(tempDir, "*_streams.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 streams log file, got %d", len(files))
	}

	// Verify log content
	data, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatalf("Failed to read log file: %v", err)
	}

	lines := parseJSONLines(t, data)
	if len(lines) < 1 {
		t.Fatalf("Expected at least 1 log entry, got %d", len(lines))
	}

	entry := lines[0]
	if entry["event_type"] != "stream_started" {
		t.Errorf("Expected event_type stream_started, got %v", entry["event_type"])
	}
	if entry["stream_id"] != "stream-123" {
		t.Errorf("Expected stream_id stream-123, got %v", entry["stream_id"])
	}
	if entry["engine_id"] != "engine-456" {
		t.Errorf("Expected engine_id engine-456, got %v", entry["engine_id"])
	}
	if entry["client_count"] != float64(2) {
		t.Errorf("Expected client_count 2, got %v", entry["client_count"])
	}
}

func TestDebugLogger_StressEvent(t *testing.T) {
	tempDir := t.TempDir()
	logger := NewDebugLogger(true, tempDir)

	// Log stress event
	details := map[string]interface{}{
		"slow_request_count": 15,
		"window":             "1m",
	}
	logger.LogStressEvent("high_slow_request_rate", "warning", "15 slow requests detected", details)

	// Verify log file exists
	files, _ := filepath.Glob(filepath.Join(tempDir, "*_stress.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 stress log file, got %d", len(files))
	}

	// Verify log content
	data, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatalf("Failed to read log file: %v", err)
	}

	lines := parseJSONLines(t, data)
	if len(lines) < 1 {
		t.Fatalf("Expected at least 1 log entry, got %d", len(lines))
	}

	entry := lines[0]
	if entry["event_type"] != "high_slow_request_rate" {
		t.Errorf("Expected event_type high_slow_request_rate, got %v", entry["event_type"])
	}
	if entry["severity"] != "warning" {
		t.Errorf("Expected severity warning, got %v", entry["severity"])
	}
	if entry["description"] != "15 slow requests detected" {
		t.Errorf("Expected description '15 slow requests detected', got %v", entry["description"])
	}
}

func TestDebugLogger_Error(t *testing.T) {
	tempDir := t.TempDir()
	logger := NewDebugLogger(true, tempDir)

	// Log error
	testErr := errors.New("connection timeout")
	context := map[string]interface{}{
		"host": "localhost",
		"port": 6878,
	}
	logger.LogError("orchestrator_client", "provision_acestream", testErr, context)

	// Verify log file exists
	files, _ := filepath.Glob(filepath.Join(tempDir, "*_errors.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 errors log file, got %d", len(files))
	}

	// Verify log content
	data, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatalf("Failed to read log file: %v", err)
	}

	lines := parseJSONLines(t, data)
	if len(lines) < 1 {
		t.Fatalf("Expected at least 1 log entry, got %d", len(lines))
	}

	entry := lines[0]
	if entry["component"] != "orchestrator_client" {
		t.Errorf("Expected component orchestrator_client, got %v", entry["component"])
	}
	if entry["operation"] != "provision_acestream" {
		t.Errorf("Expected operation provision_acestream, got %v", entry["operation"])
	}
	if entry["error_message"] != "connection timeout" {
		t.Errorf("Expected error_message 'connection timeout', got %v", entry["error_message"])
	}
	if entry["host"] != "localhost" {
		t.Errorf("Expected host localhost, got %v", entry["host"])
	}
}

func TestDebugLogger_SessionMetadata(t *testing.T) {
	tempDir := t.TempDir()
	logger := NewDebugLogger(true, tempDir)

	// Log an event
	logger.LogRequest("GET", "/test", 10*time.Millisecond, 200, "test")

	// Read the log file
	files, _ := filepath.Glob(filepath.Join(tempDir, "*_requests.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 log file, got %d", len(files))
	}

	data, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatalf("Failed to read log file: %v", err)
	}

	lines := parseJSONLines(t, data)
	if len(lines) < 1 {
		t.Fatalf("Expected at least 1 log entry")
	}

	// Verify metadata fields exist
	entry := lines[0]
	if _, ok := entry["session_id"]; !ok {
		t.Error("Expected session_id field")
	}
	if _, ok := entry["timestamp"]; !ok {
		t.Error("Expected timestamp field")
	}
	if _, ok := entry["elapsed_seconds"]; !ok {
		t.Error("Expected elapsed_seconds field")
	}

	// Verify timestamp format is RFC3339
	timestampStr, ok := entry["timestamp"].(string)
	if !ok {
		t.Fatal("timestamp is not a string")
	}
	_, err = time.Parse(time.RFC3339Nano, timestampStr)
	if err != nil {
		t.Errorf("Timestamp not in RFC3339 format: %v", err)
	}
}

func TestGlobalDebugLogger(t *testing.T) {
	tempDir := t.TempDir()

	// Initialize global logger
	InitDebugLogger(true, tempDir)

	// Get the global logger
	logger := GetDebugLogger()
	if logger == nil {
		t.Fatal("Expected non-nil global logger")
	}

	// Use the logger
	logger.LogRequest("POST", "/test", 50*time.Millisecond, 201, "global_test")

	// Verify log was written
	files, _ := filepath.Glob(filepath.Join(tempDir, "*_requests.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 log file, got %d", len(files))
	}
}

func TestGetDebugLogger_Uninitialized(t *testing.T) {
	// Reset global logger
	globalLogger = nil

	// Get logger without initialization
	logger := GetDebugLogger()
	if logger == nil {
		t.Fatal("Expected non-nil logger even when uninitialized")
	}

	// Verify it's disabled
	if logger.enabled {
		t.Error("Expected uninitialized logger to be disabled")
	}
}

func TestDebugLogger_Disconnect(t *testing.T) {
	tempDir := t.TempDir()
	logger := NewDebugLogger(true, tempDir)

	// Log a disconnect event
	context := map[string]interface{}{
		"detailed_reason": "client closed connection (broken pipe)",
		"engine_host":     "localhost",
		"engine_port":     19000,
		"container_id":    "container-xyz",
	}
	logger.LogDisconnect("stream-123|session-456", "test_ace_id_789", "client_disconnected", "write tcp: broken pipe", 1048576, 5*time.Second, context)

	// Verify log file exists
	files, _ := filepath.Glob(filepath.Join(tempDir, "*_disconnects.jsonl"))
	if len(files) != 1 {
		t.Fatalf("Expected 1 disconnects log file, got %d", len(files))
	}

	// Verify log content
	data, err := os.ReadFile(files[0])
	if err != nil {
		t.Fatalf("Failed to read log file: %v", err)
	}

	lines := parseJSONLines(t, data)
	if len(lines) < 1 {
		t.Fatalf("Expected at least 1 log entry, got %d", len(lines))
	}

	entry := lines[0]
	if entry["stream_id"] != "stream-123|session-456" {
		t.Errorf("Expected stream_id 'stream-123|session-456', got %v", entry["stream_id"])
	}
	if entry["ace_id"] != "test_ace_id_789" {
		t.Errorf("Expected ace_id test_ace_id_789, got %v", entry["ace_id"])
	}
	if entry["reason"] != "client_disconnected" {
		t.Errorf("Expected reason client_disconnected, got %v", entry["reason"])
	}
	if entry["error"] != "write tcp: broken pipe" {
		t.Errorf("Expected error 'write tcp: broken pipe', got %v", entry["error"])
	}
	if entry["bytes_copied"] != float64(1048576) {
		t.Errorf("Expected bytes_copied 1048576, got %v", entry["bytes_copied"])
	}
	if entry["detailed_reason"] != "client closed connection (broken pipe)" {
		t.Errorf("Expected detailed_reason 'client closed connection (broken pipe)', got %v", entry["detailed_reason"])
	}
	if entry["engine_host"] != "localhost" {
		t.Errorf("Expected engine_host localhost, got %v", entry["engine_host"])
	}
	if entry["engine_port"] != float64(19000) {
		t.Errorf("Expected engine_port 19000, got %v", entry["engine_port"])
	}
}

// Helper function to parse JSONL file
func parseJSONLines(t *testing.T, data []byte) []map[string]interface{} {
	t.Helper()
	lines := []map[string]interface{}{}

	decoder := json.NewDecoder(bytes.NewReader(data))
	for decoder.More() {
		var entry map[string]interface{}
		if err := decoder.Decode(&entry); err != nil {
			t.Fatalf("Failed to decode JSON: %v", err)
		}
		lines = append(lines, entry)
	}

	return lines
}
