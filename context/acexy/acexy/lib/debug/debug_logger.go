// Acexy - Copyright (C) 2024 - Javinator9889 <dev at javinator9889 dot com>
// This program comes with ABSOLUTELY NO WARRANTY; for details type `show w'.
// This is free software, and you are welcome to redistribute it
// under certain conditions; type `show c' for details.
package debug

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"time"
)

// DebugLogger handles structured debug logging to persistent files
type DebugLogger struct {
	enabled      bool
	logDir       string
	sessionID    string
	sessionStart time.Time
	mu           sync.Mutex
}

// LogEntry represents a single log entry with metadata
type LogEntry struct {
	SessionID      string  `json:"session_id"`
	Timestamp      string  `json:"timestamp"`
	ElapsedSeconds float64 `json:"elapsed_seconds"`
}

// NewDebugLogger creates a new debug logger instance
func NewDebugLogger(enabled bool, logDir string) *DebugLogger {
	sessionID := time.Now().Format("20060102_150405")
	logger := &DebugLogger{
		enabled:      enabled,
		logDir:       logDir,
		sessionStart: time.Now(),
		sessionID:    sessionID,
	}

	if enabled {
		os.MkdirAll(logDir, 0755)
		logger.writeLog("session", map[string]interface{}{
			"event":      "session_start",
			"session_id": sessionID,
		})
	}

	return logger
}

// writeLog writes a log entry to the appropriate category file
func (d *DebugLogger) writeLog(category string, data map[string]interface{}) {
	if !d.enabled {
		return
	}

	d.mu.Lock()
	defer d.mu.Unlock()

	// Create a combined map with metadata and data
	entry := map[string]interface{}{
		"session_id":      d.sessionID,
		"timestamp":       time.Now().UTC().Format(time.RFC3339Nano),
		"elapsed_seconds": time.Since(d.sessionStart).Seconds(),
	}

	// Add all data fields to the entry
	for k, v := range data {
		entry[k] = v
	}

	filename := filepath.Join(d.logDir, fmt.Sprintf("%s_%s.jsonl", d.sessionID, category))
	file, err := os.OpenFile(filename, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
	if err != nil {
		return
	}
	defer file.Close()

	json.NewEncoder(file).Encode(entry)
}

// LogRequest logs HTTP request timing and outcomes
func (d *DebugLogger) LogRequest(method, path string, duration time.Duration, statusCode int, aceID string) {
	d.writeLog("requests", map[string]interface{}{
		"method":      method,
		"path":        path,
		"duration_ms": duration.Milliseconds(),
		"status_code": statusCode,
		"ace_id":      aceID,
	})
}

// LogEngineSelection logs engine selection decisions and timing
func (d *DebugLogger) LogEngineSelection(operation string, selectedHost string, selectedPort int, containerID string, duration time.Duration, errorMsg string) {
	d.writeLog("engine_selection", map[string]interface{}{
		"operation":     operation,
		"selected_host": selectedHost,
		"selected_port": selectedPort,
		"container_id":  containerID,
		"duration_ms":   duration.Milliseconds(),
		"error":         errorMsg,
	})
}

// LogProvisioning logs provisioning operations with retry information
func (d *DebugLogger) LogProvisioning(operation string, duration time.Duration, success bool, errorMsg string, retryCount int) {
	d.writeLog("provisioning", map[string]interface{}{
		"operation":   operation,
		"duration_ms": duration.Milliseconds(),
		"success":     success,
		"error":       errorMsg,
		"retry_count": retryCount,
	})
}

// LogOrchestratorHealth logs orchestrator health check results
func (d *DebugLogger) LogOrchestratorHealth(status string, canProvision bool, blockedReason string, blockedReasonCode string, recoveryETA int, vpnConnected bool, capacityTotal, capacityUsed, capacityAvailable int) {
	d.writeLog("orchestrator_health", map[string]interface{}{
		"status":               status,
		"can_provision":        canProvision,
		"blocked_reason":       blockedReason,
		"blocked_reason_code":  blockedReasonCode,
		"recovery_eta_seconds": recoveryETA,
		"vpn_connected":        vpnConnected,
		"capacity_total":       capacityTotal,
		"capacity_used":        capacityUsed,
		"capacity_available":   capacityAvailable,
	})
}

// LogStreamEvent logs stream lifecycle events
func (d *DebugLogger) LogStreamEvent(eventType, streamID, engineID string, duration time.Duration, additionalData map[string]interface{}) {
	data := map[string]interface{}{
		"event_type":  eventType,
		"stream_id":   streamID,
		"engine_id":   engineID,
		"duration_ms": duration.Milliseconds(),
	}
	for k, v := range additionalData {
		data[k] = v
	}
	d.writeLog("streams", data)
}

// LogStressEvent logs detected stress situations
func (d *DebugLogger) LogStressEvent(eventType, severity, description string, details map[string]interface{}) {
	data := map[string]interface{}{
		"event_type":  eventType,
		"severity":    severity,
		"description": description,
	}
	for k, v := range details {
		data[k] = v
	}
	d.writeLog("stress", data)
}

// LogError logs error details with context
func (d *DebugLogger) LogError(component, operation string, err error, context map[string]interface{}) {
	data := map[string]interface{}{
		"component":     component,
		"operation":     operation,
		"error_type":    fmt.Sprintf("%T", err),
		"error_message": err.Error(),
	}
	for k, v := range context {
		data[k] = v
	}
	d.writeLog("errors", data)
}

// LogDisconnect logs client disconnect events with detailed reason
func (d *DebugLogger) LogDisconnect(streamID, aceID, reason, errorMsg string, bytesCopied int64, duration time.Duration, context map[string]interface{}) {
	data := map[string]interface{}{
		"stream_id":    streamID,
		"ace_id":       aceID,
		"reason":       reason,
		"error":        errorMsg,
		"bytes_copied": bytesCopied,
		"duration_ms":  duration.Milliseconds(),
	}
	for k, v := range context {
		data[k] = v
	}
	d.writeLog("disconnects", data)
}

var globalLogger *DebugLogger

// InitDebugLogger initializes the global debug logger
func InitDebugLogger(enabled bool, logDir string) {
	globalLogger = NewDebugLogger(enabled, logDir)
}

// GetDebugLogger returns the global debug logger instance
func GetDebugLogger() *DebugLogger {
	if globalLogger == nil {
		globalLogger = NewDebugLogger(false, "")
	}
	return globalLogger
}
