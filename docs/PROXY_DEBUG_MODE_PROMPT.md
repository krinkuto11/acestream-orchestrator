# Proxy Debug Mode Implementation Prompt

## Context

The orchestrator repository has implemented a comprehensive debugging mode that writes detailed performance logs during stress situations. This document provides guidance for implementing similar debugging capabilities in the acexy proxy repository.

## Problem Statement

When investigating performance issues or stress situations (high load, VPN issues, provisioning failures), we need detailed logs from both the orchestrator and proxy to understand the complete picture. Currently:

- **Orchestrator**: Has comprehensive debug logging (see `DEBUG_MODE.md`)
- **Proxy**: Needs similar debug logging capabilities
- **Gap**: Cannot easily correlate events and timing between both systems

## Objective

Implement a debug mode in the acexy proxy that:

1. **Writes persistent debug logs** to a folder structure similar to orchestrator
2. **Captures performance metrics** for critical operations
3. **Detects stress situations** automatically
4. **Correlates with orchestrator logs** via timestamps and session IDs
5. **Minimizes performance impact** during normal operation

## Required Features

### 1. Debug Logger Module

Create a `debug_logger.go` module with similar capabilities to orchestrator's debug logger:

```go
package debug

import (
    "encoding/json"
    "fmt"
    "os"
    "path/filepath"
    "sync"
    "time"
)

type DebugLogger struct {
    enabled    bool
    logDir     string
    sessionID  string
    sessionStart time.Time
    mu         sync.Mutex
}

type LogEntry struct {
    SessionID      string                 `json:"session_id"`
    Timestamp      string                 `json:"timestamp"`
    ElapsedSeconds float64                `json:"elapsed_seconds"`
    Data           map[string]interface{} `json:",inline"`
}

func NewDebugLogger(enabled bool, logDir string) *DebugLogger {
    sessionID := time.Now().Format("20060102_150405")
    logger := &DebugLogger{
        enabled:      enabled,
        logDir:       logDir,
        sessionID:    sessionID,
        sessionStart: time.Now(),
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

func (d *DebugLogger) writeLog(category string, data map[string]interface{}) {
    if !d.enabled {
        return
    }
    
    d.mu.Lock()
    defer d.mu.Unlock()
    
    entry := LogEntry{
        SessionID:      d.sessionID,
        Timestamp:      time.Now().UTC().Format(time.RFC3339Nano),
        ElapsedSeconds: time.Since(d.sessionStart).Seconds(),
        Data:           data,
    }
    
    filename := filepath.Join(d.logDir, fmt.Sprintf("%s_%s.jsonl", d.sessionID, category))
    file, err := os.OpenFile(filename, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
    if err != nil {
        return
    }
    defer file.Close()
    
    json.NewEncoder(file).Encode(entry)
}

// Specific log methods
func (d *DebugLogger) LogRequest(method, path string, duration time.Duration, statusCode int, aceID string) {
    d.writeLog("requests", map[string]interface{}{
        "method":      method,
        "path":        path,
        "duration_ms": duration.Milliseconds(),
        "status_code": statusCode,
        "ace_id":      aceID,
    })
}

func (d *DebugLogger) LogEngineSelection(operation string, selectedHost string, selectedPort int, duration time.Duration, error string) {
    d.writeLog("engine_selection", map[string]interface{}{
        "operation":     operation,
        "selected_host": selectedHost,
        "selected_port": selectedPort,
        "duration_ms":   duration.Milliseconds(),
        "error":         error,
    })
}

func (d *DebugLogger) LogProvisioning(operation string, duration time.Duration, success bool, error string, retryCount int) {
    d.writeLog("provisioning", map[string]interface{}{
        "operation":   operation,
        "duration_ms": duration.Milliseconds(),
        "success":     success,
        "error":       error,
        "retry_count": retryCount,
    })
}

func (d *DebugLogger) LogOrchestratorHealth(status string, canProvision bool, blockedReason string, engineCount, streamCount int) {
    d.writeLog("orchestrator_health", map[string]interface{}{
        "status":         status,
        "can_provision":  canProvision,
        "blocked_reason": blockedReason,
        "engine_count":   engineCount,
        "stream_count":   streamCount,
    })
}

func (d *DebugLogger) LogStreamEvent(eventType, streamID, engineID string, duration time.Duration) {
    d.writeLog("streams", map[string]interface{}{
        "event_type":  eventType,
        "stream_id":   streamID,
        "engine_id":   engineID,
        "duration_ms": duration.Milliseconds(),
    })
}

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

var globalLogger *DebugLogger

func InitDebugLogger(enabled bool, logDir string) {
    globalLogger = NewDebugLogger(enabled, logDir)
}

func GetDebugLogger() *DebugLogger {
    if globalLogger == nil {
        globalLogger = NewDebugLogger(false, "")
    }
    return globalLogger
}
```

### 2. Configuration

Add environment variables to proxy configuration:

```go
// In config.go or similar

type Config struct {
    // ... existing config ...
    
    // Debug mode configuration
    DebugMode   bool   `env:"DEBUG_MODE" envDefault:"false"`
    DebugLogDir string `env:"DEBUG_LOG_DIR" envDefault:"./debug_logs"`
}
```

### 3. Integration Points

#### A. HTTP Request Handling

Wrap request handlers to log timing and outcomes:

```go
// In proxy.go or main handler

func (p *Proxy) HandleStream(w http.ResponseWriter, r *http.Request) {
    debugLog := debug.GetDebugLogger()
    startTime := time.Now()
    
    // Parse request
    aceID := extractAceID(r)
    
    defer func() {
        duration := time.Since(startTime)
        statusCode := getStatusCode(w)
        debugLog.LogRequest(r.Method, r.URL.Path, duration, statusCode, aceID.String())
        
        // Detect slow requests
        if duration > 5*time.Second {
            debugLog.LogStressEvent(
                "slow_request",
                "warning",
                fmt.Sprintf("Request took %.2fs", duration.Seconds()),
                map[string]interface{}{
                    "path":     r.URL.Path,
                    "ace_id":   aceID.String(),
                    "duration": duration.Seconds(),
                },
            )
        }
    }()
    
    // ... existing handler code ...
}
```

#### B. Engine Selection

Log engine selection timing and decisions:

```go
// In orchestrator client or engine selection logic

func (c *orchClient) SelectBestEngine() (string, int, string, error) {
    debugLog := debug.GetDebugLogger()
    startTime := time.Now()
    
    host, port, engineID, err := c.selectEngineInternal()
    duration := time.Since(startTime)
    
    var errMsg string
    if err != nil {
        errMsg = err.Error()
    }
    
    debugLog.LogEngineSelection("select_best_engine", host, port, duration, errMsg)
    
    // Detect slow selection
    if duration > 2*time.Second {
        debugLog.LogStressEvent(
            "slow_engine_selection",
            "warning",
            fmt.Sprintf("Engine selection took %.2fs", duration.Seconds()),
            map[string]interface{}{
                "duration": duration.Seconds(),
                "error":    errMsg,
            },
        )
    }
    
    return host, port, engineID, err
}
```

#### C. Provisioning Operations

Track provisioning attempts, retries, and failures:

```go
// In provisioning logic

func (c *orchClient) ProvisionWithRetry(maxAttempts int) (*aceProvisionResponse, error) {
    debugLog := debug.GetDebugLogger()
    startTime := time.Now()
    
    var lastErr error
    for attempt := 0; attempt < maxAttempts; attempt++ {
        attemptStart := time.Now()
        
        resp, err := c.ProvisionAcestream()
        attemptDuration := time.Since(attemptStart)
        
        if err == nil {
            totalDuration := time.Since(startTime)
            debugLog.LogProvisioning("provision_success", totalDuration, true, "", attempt)
            return resp, nil
        }
        
        lastErr = err
        debugLog.LogProvisioning("provision_attempt_failed", attemptDuration, false, err.Error(), attempt+1)
        
        // Check if error indicates stress situation
        if strings.Contains(err.Error(), "circuit_breaker") {
            debugLog.LogStressEvent(
                "provisioning_circuit_breaker",
                "critical",
                "Provisioning blocked by circuit breaker",
                map[string]interface{}{
                    "attempt": attempt + 1,
                    "error":   err.Error(),
                },
            )
        }
    }
    
    totalDuration := time.Since(startTime)
    debugLog.LogProvisioning("provision_failed", totalDuration, false, lastErr.Error(), maxAttempts)
    return nil, lastErr
}
```

#### D. Orchestrator Health Monitoring

Log health check results and transitions:

```go
// In health monitoring loop

func (c *orchClient) updateHealth() {
    debugLog := debug.GetDebugLogger()
    
    status := c.getOrchestratorStatus()
    
    debugLog.LogOrchestratorHealth(
        status.Status,
        status.Provisioning.CanProvision,
        status.Provisioning.BlockedReason,
        status.Engines.Total,
        status.Streams.Active,
    )
    
    // Detect degraded state
    if status.Status == "degraded" {
        debugLog.LogStressEvent(
            "orchestrator_degraded",
            "warning",
            fmt.Sprintf("Orchestrator is degraded: %s", status.Provisioning.BlockedReason),
            map[string]interface{}{
                "blocked_reason": status.Provisioning.BlockedReason,
                "engine_count":   status.Engines.Total,
            },
        )
    }
}
```

#### E. Stream Lifecycle

Track stream start, end, and errors:

```go
// In stream event reporting

func (c *orchClient) ReportStreamStarted(evt StreamStartedEvent) error {
    debugLog := debug.GetDebugLogger()
    startTime := time.Now()
    
    err := c.sendStreamStartedEvent(evt)
    duration := time.Since(startTime)
    
    debugLog.LogStreamEvent("stream_started", evt.StreamID, evt.EngineID, duration)
    
    if err != nil {
        debugLog.LogError("stream_events", "report_stream_started", err, map[string]interface{}{
            "stream_id": evt.StreamID,
            "engine_id": evt.EngineID,
        })
    }
    
    return err
}

func (c *orchClient) ReportStreamEnded(evt StreamEndedEvent) error {
    debugLog := debug.GetDebugLogger()
    startTime := time.Now()
    
    err := c.sendStreamEndedEvent(evt)
    duration := time.Since(startTime)
    
    debugLog.LogStreamEvent("stream_ended", evt.StreamID, evt.EngineID, duration)
    
    if err != nil {
        debugLog.LogError("stream_events", "report_stream_ended", err, map[string]interface{}{
            "stream_id": evt.StreamID,
            "engine_id": evt.EngineID,
        })
    }
    
    return err
}
```

### 4. Stress Situation Detection

Implement automatic detection of stress situations:

```go
// Stress detection helper

type StressDetector struct {
    mu                    sync.Mutex
    slowRequestCount      int
    provisionFailureCount int
    windowStart           time.Time
}

func (s *StressDetector) CheckAndLog() {
    s.mu.Lock()
    defer s.mu.Unlock()
    
    debugLog := debug.GetDebugLogger()
    now := time.Now()
    
    // Reset counters every minute
    if now.Sub(s.windowStart) > time.Minute {
        s.slowRequestCount = 0
        s.provisionFailureCount = 0
        s.windowStart = now
        return
    }
    
    // High slow request rate
    if s.slowRequestCount > 10 {
        debugLog.LogStressEvent(
            "high_slow_request_rate",
            "warning",
            fmt.Sprintf("%d slow requests in the last minute", s.slowRequestCount),
            map[string]interface{}{
                "count": s.slowRequestCount,
                "window": "1m",
            },
        )
    }
    
    // High provisioning failure rate
    if s.provisionFailureCount > 5 {
        debugLog.LogStressEvent(
            "high_provisioning_failure_rate",
            "critical",
            fmt.Sprintf("%d provisioning failures in the last minute", s.provisionFailureCount),
            map[string]interface{}{
                "count": s.provisionFailureCount,
                "window": "1m",
            },
        )
    }
}
```

### 5. Log Categories

Implement the same categories as orchestrator:

- `*_session.jsonl` - Session lifecycle
- `*_requests.jsonl` - HTTP request timing
- `*_engine_selection.jsonl` - Engine selection decisions
- `*_provisioning.jsonl` - Provisioning operations
- `*_orchestrator_health.jsonl` - Orchestrator status checks
- `*_streams.jsonl` - Stream lifecycle events
- `*_stress.jsonl` - Stress situation detection
- `*_errors.jsonl` - Error details

### 6. Configuration Example

```yaml
# docker-compose.yml or similar
services:
  acexy-proxy:
    environment:
      - DEBUG_MODE=true
      - DEBUG_LOG_DIR=/app/debug_logs
    volumes:
      - ./proxy_debug_logs:/app/debug_logs
```

## Correlation with Orchestrator Logs

### Time Synchronization

Ensure both systems use synchronized time:

```bash
# Use NTP or similar
apt-get install ntp
systemctl enable ntp
systemctl start ntp
```

### Session Correlation

While orchestrator and proxy have separate session IDs, correlate by:

1. **Timestamps**: Use RFC3339 format with nanosecond precision
2. **Container/Engine IDs**: Log container IDs in both systems
3. **Stream IDs**: Use consistent stream ID format
4. **Request IDs**: Pass request IDs between systems (future enhancement)

### Example Correlation Query

```python
import json
from pathlib import Path
from datetime import datetime, timedelta

# Load orchestrator provisioning logs
orch_provisions = []
for log_file in Path("orchestrator_debug_logs").glob("*_provisioning.jsonl"):
    with open(log_file) as f:
        for line in f:
            orch_provisions.append(json.loads(line))

# Load proxy provisioning logs  
proxy_provisions = []
for log_file in Path("proxy_debug_logs").glob("*_provisioning.jsonl"):
    with open(log_file) as f:
        for line in f:
            proxy_provisions.append(json.loads(line))

# Find provisions within 1 second of each other
for proxy_prov in proxy_provisions:
    proxy_time = datetime.fromisoformat(proxy_prov["timestamp"])
    
    for orch_prov in orch_provisions:
        orch_time = datetime.fromisoformat(orch_prov["timestamp"])
        
        if abs((proxy_time - orch_time).total_seconds()) < 1:
            print(f"Correlated provision:")
            print(f"  Proxy: {proxy_prov}")
            print(f"  Orch: {orch_prov}")
            print()
```

## Testing

### Unit Tests

```go
func TestDebugLogger(t *testing.T) {
    tempDir := t.TempDir()
    logger := NewDebugLogger(true, tempDir)
    
    // Test request logging
    logger.LogRequest("GET", "/stream", 100*time.Millisecond, 200, "test_ace_id")
    
    // Verify log file exists
    files, _ := filepath.Glob(filepath.Join(tempDir, "*_requests.jsonl"))
    if len(files) != 1 {
        t.Errorf("Expected 1 request log file, got %d", len(files))
    }
    
    // Verify log content
    data, _ := os.ReadFile(files[0])
    var entry LogEntry
    json.Unmarshal(data, &entry)
    
    if entry.Data["method"] != "GET" {
        t.Errorf("Expected method GET, got %v", entry.Data["method"])
    }
}
```

### Integration Tests

```go
func TestDebugModeEndToEnd(t *testing.T) {
    // Start proxy with debug mode enabled
    tempDir := t.TempDir()
    os.Setenv("DEBUG_MODE", "true")
    os.Setenv("DEBUG_LOG_DIR", tempDir)
    
    // Make test request
    resp, _ := http.Get("http://localhost:8080/test")
    
    // Verify logs were created
    time.Sleep(100 * time.Millisecond) // Allow time for async writes
    
    files, _ := filepath.Glob(filepath.Join(tempDir, "*.jsonl"))
    if len(files) == 0 {
        t.Error("No debug logs were created")
    }
}
```

## Performance Considerations

### Async Writes

Use buffered channels for async log writes:

```go
type AsyncDebugLogger struct {
    *DebugLogger
    logChan chan logMessage
}

type logMessage struct {
    category string
    data     map[string]interface{}
}

func (a *AsyncDebugLogger) writeLog(category string, data map[string]interface{}) {
    if !a.enabled {
        return
    }
    
    select {
    case a.logChan <- logMessage{category, data}:
    default:
        // Channel full, drop log (or log error)
    }
}

func (a *AsyncDebugLogger) startWriter() {
    go func() {
        for msg := range a.logChan {
            a.DebugLogger.writeLog(msg.category, msg.data)
        }
    }()
}
```

### Minimal Overhead

- Check `enabled` flag early in log methods
- Use defer for cleanup but not for logging
- Buffer writes to reduce syscalls
- Consider sampling under extreme load

## Documentation

Create `docs/DEBUG_MODE.md` in proxy repository with:

1. Overview and purpose
2. Configuration instructions
3. Log structure and categories
4. Usage examples
5. Correlation with orchestrator
6. Performance impact
7. Security considerations

## Success Criteria

Implementation is successful when:

1. ✅ Debug mode can be enabled/disabled via environment variable
2. ✅ Logs are written to persistent folder structure
3. ✅ All critical operations are logged with timing
4. ✅ Stress situations are automatically detected
5. ✅ Logs can be correlated with orchestrator logs
6. ✅ Performance impact is negligible (<1% overhead)
7. ✅ Documentation is complete and clear
8. ✅ Tests verify functionality

## Example Combined Analysis

With debug mode enabled on both systems, you can:

```bash
# Find slow end-to-end provisioning
# 1. From proxy: when proxy requested provisioning
grep "provision_attempt" proxy_debug_logs/*_provisioning.jsonl

# 2. From orchestrator: when orchestrator started provisioning
grep "start_acestream_begin" orchestrator_debug_logs/*_provisioning.jsonl

# 3. Compare timestamps to find delays

# Find VPN issues impacting both systems
# Orchestrator logs VPN status changes
grep "vpn_disconnection" orchestrator_debug_logs/*_stress.jsonl

# Proxy logs provisioning failures around same time
grep "provision.*vpn" proxy_debug_logs/*_provisioning.jsonl

# Find circuit breaker coordination
# Orchestrator: circuit breaker opens
grep "circuit_breaker_opened" orchestrator_debug_logs/*_stress.jsonl

# Proxy: sees orchestrator degraded
grep "orchestrator_degraded" proxy_debug_logs/*_stress.jsonl
```

## Summary

Implementing this debug mode in the proxy will provide:

1. **Complete visibility** into proxy operations during stress
2. **Correlation** with orchestrator logs for end-to-end analysis
3. **Automatic detection** of performance issues
4. **Data-driven optimization** based on real timing metrics
5. **Faster debugging** of production issues

The implementation follows the same patterns as orchestrator, making it easy to:
- Use the same analysis tools
- Apply the same monitoring strategies  
- Understand issues spanning both systems
- Train team members on consistent tooling

## Next Steps

1. Review this prompt with the proxy team
2. Create GitHub issue for proxy debug mode implementation
3. Assign to appropriate developer(s)
4. Implement in phases (core logger → integrations → stress detection → docs)
5. Test with stress scenarios
6. Document findings and refine detection thresholds
