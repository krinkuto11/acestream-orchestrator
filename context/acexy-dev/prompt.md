> @copilot Taking into account the new additions and the proxy code inside the context folder, create a prompt that I can provide Copilot with in the Proxy repository so it can improve the proxy to hand...

Here's a comprehensive prompt for the Acexy proxy repository:

---

## Prompt for Acexy Proxy Integration Improvements

### Context

The acestream-orchestrator has been updated with critical fixes and new features for better proxy integration. The orchestrator now:

1. **Immediately syncs state after provisioning** - Engines appear in `/engines` endpoint right after creation
2. **Provides comprehensive status API** - New `/orchestrator/status` endpoint with full health visibility
3. **Enhanced error handling** - Proper HTTP codes (503/500) with clear error messages
4. **VPN-aware provisioning** - Blocks provisioning when VPN is down with `can_provision` flag

### Task

Update the Acexy proxy to leverage these orchestrator improvements for better reliability, error handling, and user experience.

### Current Implementation Review

The proxy currently:
- Calls `POST /provision/acestream` when no engines have capacity
- Waits 10 seconds after provisioning
- Calls `GET /engines` to find available engines
- Selects best engine based on health and load
- Falls back to configured engine if orchestrator unavailable

### Improvements Needed

#### 1. Add Health Check Before Provisioning

**Problem:** Proxy attempts to provision without checking if orchestrator can provision (VPN down, circuit breaker open).

**Solution:** Check `/orchestrator/status` before provisioning:

```go
func (c *orchClient) CanProvision() (bool, string) {
    resp, err := c.hc.Get(c.base + "/orchestrator/status")
    if err != nil {
        return false, "orchestrator unavailable"
    }
    defer resp.Body.Close()
    
    var status struct {
        Provisioning struct {
            CanProvision  bool   `json:"can_provision"`
            BlockedReason string `json:"blocked_reason"`
        } `json:"provisioning"`
    }
    
    json.NewDecoder(resp.Body).Decode(&status)
    return status.Provisioning.CanProvision, status.Provisioning.BlockedReason
}
```

**Update SelectBestEngine:**
```go
if len(availableEngines) == 0 {
    // Check if we can provision before attempting
    canProvision, reason := c.CanProvision()
    if !canProvision {
        return "", 0, fmt.Errorf("cannot provision: %s", reason)
    }
    
    // Proceed with provisioning...
}
```

#### 2. Handle Provisioning Errors Properly

**Problem:** Generic error handling doesn't distinguish between temporary (503) and permanent (500) failures.

**Solution:** Parse HTTP status codes and handle appropriately:

```go
func (c *orchClient) ProvisionAcestream() (*aceProvisionResponse, error) {
    // ... existing request setup ...
    
    resp, err := c.hc.Do(req)
    if err != nil {
        return nil, fmt.Errorf("failed to provision: %w", err)
    }
    defer resp.Body.Close()
    
    if resp.StatusCode == 503 {
        // Temporary failure - VPN down or circuit breaker
        var errResp struct {
            Detail string `json:"detail"`
        }
        json.NewDecoder(resp.Body).Decode(&errResp)
        return nil, fmt.Errorf("provisioning temporarily unavailable: %s", errResp.Detail)
    }
    
    if resp.StatusCode == 500 {
        // Permanent error - configuration issue
        var errResp struct {
            Detail string `json:"detail"`
        }
        json.NewDecoder(resp.Body).Decode(&errResp)
        return nil, fmt.Errorf("provisioning failed: %s", errResp.Detail)
    }
    
    if resp.StatusCode != 200 {
        return nil, fmt.Errorf("unexpected status: %d", resp.StatusCode)
    }
    
    // Parse successful response...
}
```

#### 3. Add Periodic Health Monitoring

**Problem:** Proxy only checks orchestrator when selecting engines, not proactively monitoring health.

**Solution:** Add background health check:

```go
type OrchestratorHealth struct {
    mu              sync.RWMutex
    lastCheck       time.Time
    status          string
    canProvision    bool
    blockedReason   string
    vpnConnected    bool
}

func (c *orchClient) StartHealthMonitor(ctx context.Context) {
    ticker := time.NewTicker(30 * time.Second)
    defer ticker.Stop()
    
    for {
        select {
        case <-ctx.Done():
            return
        case <-ticker.C:
            c.updateHealth()
        }
    }
}

func (c *orchClient) updateHealth() {
    resp, err := c.hc.Get(c.base + "/orchestrator/status")
    if err != nil {
        slog.Warn("Health check failed", "error", err)
        return
    }
    defer resp.Body.Close()
    
    var status struct {
        Status string `json:"status"`
        VPN struct {
            Connected bool `json:"connected"`
        } `json:"vpn"`
        Provisioning struct {
            CanProvision  bool   `json:"can_provision"`
            BlockedReason string `json:"blocked_reason"`
        } `json:"provisioning"`
    }
    
    if err := json.NewDecoder(resp.Body).Decode(&status); err != nil {
        return
    }
    
    c.health.mu.Lock()
    defer c.health.mu.Unlock()
    c.health.lastCheck = time.Now()
    c.health.status = status.Status
    c.health.canProvision = status.Provisioning.CanProvision
    c.health.blockedReason = status.Provisioning.BlockedReason
    c.health.vpnConnected = status.VPN.Connected
    
    slog.Debug("Orchestrator health updated", 
        "status", status.Status,
        "can_provision", status.Provisioning.CanProvision,
        "vpn_connected", status.VPN.Connected)
}
```

#### 4. Improve User-Facing Error Messages

**Problem:** Generic errors don't explain why stream failed.

**Solution:** Return specific errors to clients:

```go
func (p *Proxy) HandleStream(w http.ResponseWriter, r *http.Request) {
    // ... existing code ...
    
    host, port, err := p.Orch.SelectBestEngine()
    if err != nil {
        // Check if it's a provisioning issue
        if strings.Contains(err.Error(), "VPN") {
            slog.Error("Stream failed due to VPN issue", "error", err)
            http.Error(w, "Service temporarily unavailable: VPN connection required", http.StatusServiceUnavailable)
            return
        }
        if strings.Contains(err.Error(), "circuit breaker") {
            slog.Error("Stream failed due to circuit breaker", "error", err)
            http.Error(w, "Service temporarily unavailable: Too many failures, please retry later", http.StatusServiceUnavailable)
            return
        }
        
        slog.Warn("Failed to select engine, falling back", "error", err)
        selectedHost = p.Acexy.Host
        selectedPort = p.Acexy.Port
    }
    
    // ... rest of stream handling ...
}
```

#### 5. Reduce Wait Time After Provisioning

**Problem:** 10-second wait may be unnecessary now that state syncs immediately.

**Solution:** Verify engine availability and reduce wait:

```go
if len(availableEngines) == 0 {
    slog.Info("No available engines, provisioning new one")
    
    provResp, err := c.ProvisionAcestream()
    if err != nil {
        return "", 0, err
    }
    
    // Shorter wait since orchestrator now syncs state immediately
    time.Sleep(5 * time.Second)
    
    // Verify engine appears in list
    engines, err := c.GetEngines()
    if err == nil {
        for _, eng := range engines {
            if eng.ContainerID == provResp.ContainerID {
                slog.Info("Provisioned engine found in orchestrator", 
                    "container_id", provResp.ContainerID)
                return "localhost", provResp.HostHTTPPort, nil
            }
        }
    }
    
    // Still not found, wait a bit more and return anyway
    slog.Warn("Engine not immediately available, continuing anyway")
    time.Sleep(5 * time.Second)
    
    return "localhost", provResp.HostHTTPPort, nil
}
```

#### 6. Add Retry Logic for Temporary Failures

**Problem:** Single failure causes stream to fail.

**Solution:** Retry provisioning with exponential backoff:

```go
func (c *orchClient) ProvisionWithRetry(maxRetries int) (*aceProvisionResponse, error) {
    var lastErr error
    
    for attempt := 0; attempt < maxRetries; attempt++ {
        if attempt > 0 {
            backoff := time.Duration(1<<uint(attempt)) * time.Second
            slog.Info("Retrying provision after backoff", "attempt", attempt+1, "backoff", backoff)
            time.Sleep(backoff)
        }
        
        resp, err := c.ProvisionAcestream()
        if err == nil {
            return resp, nil
        }
        
        lastErr = err
        
        // Don't retry on permanent errors (500)
        if strings.Contains(err.Error(), "failed:") {
            return nil, err
        }
        
        // Retry on temporary errors (503)
        slog.Warn("Provision attempt failed", "attempt", attempt+1, "error", err)
    }
    
    return nil, fmt.Errorf("provisioning failed after %d attempts: %w", maxRetries, lastErr)
}
```

### Testing

After implementing changes:

1. **Test VPN failure scenario:** Stop VPN, attempt stream, verify clear error message
2. **Test provisioning:** Verify engines appear immediately in orchestrator
3. **Test circuit breaker:** Trigger multiple failures, verify retry behavior
4. **Test fallback:** Stop orchestrator, verify fallback to configured engine

### Expected Outcomes

- ✅ Clearer error messages for users when services unavailable
- ✅ Faster provisioning (reduced wait time)
- ✅ Better reliability with retry logic
- ✅ Proactive health monitoring
- ✅ VPN-aware stream handling
- ✅ Graceful degradation on failures

---

Implement these improvements to make Acexy more resilient and user-friendly when integrated with the updated orchestrator.