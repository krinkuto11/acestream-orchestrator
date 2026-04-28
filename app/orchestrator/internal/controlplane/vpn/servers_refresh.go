package vpn

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"
)

const officialGluetunServersURL = "https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json"

// ServersRefreshService periodically downloads the Gluetun official servers list
// and writes it to the configured servers directory.
//
// ProtonVPN server refresh is handled by the Python vpn_servers_refresh.py binary
// since it requires the Proton SRP auth flow and is not latency-sensitive.
type ServersRefreshService struct {
	serversDir  string
	rep         *ReputationManager
	mu          sync.Mutex
	inProgress  bool
	lastOK      *bool
	lastErr     string
	lastAt      time.Time

	initialDone chan struct{}
	once        sync.Once
}

func NewServersRefreshService(serversDir string, rep *ReputationManager) *ServersRefreshService {
	return &ServersRefreshService{
		serversDir:  serversDir,
		rep:         rep,
		initialDone: make(chan struct{}),
	}
}

// Run is the background refresh loop. It respects ctx cancellation.
func (s *ServersRefreshService) Run(ctx context.Context, autoRefresh bool, period time.Duration) {
	slog.Info("VPN servers refresh service started",
		"auto_refresh", autoRefresh,
		"period", period,
	)

	if autoRefresh {
		// Fire an immediate refresh on startup.
		if err := s.RefreshOfficial(ctx); err != nil {
			slog.Warn("Initial VPN servers refresh failed", "err", err)
		}
	} else {
		// Even if auto-refresh is disabled, ensure we have a catalog if it's
		// missing entirely (e.g. first run).
		if !s.rep.IsCatalogAvailable("servers.json") {
			slog.Info("VPN servers catalog missing; performing one-time download")
			if err := s.RefreshOfficial(ctx); err != nil {
				slog.Warn("One-time VPN servers download failed", "err", err)
			}
		}
		// Signal initial done so provisioning is not blocked.
		s.once.Do(func() { close(s.initialDone) })
		<-ctx.Done()
		return
	}

	ticker := time.NewTicker(period)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			slog.Info("VPN servers refresh service stopped")
			return
		case <-ticker.C:
			if err := s.RefreshOfficial(ctx); err != nil {
				slog.Warn("Scheduled VPN servers refresh failed", "err", err)
			}
		}
	}
}

// WaitForInitialRefresh blocks until the first successful refresh completes or
// timeout elapses. Returns true if refresh completed in time.
func (s *ServersRefreshService) WaitForInitialRefresh(ctx context.Context, timeout time.Duration) bool {
	timer := time.NewTimer(timeout)
	defer timer.Stop()
	select {
	case <-s.initialDone:
		return true
	case <-timer.C:
		slog.Warn("Timed out waiting for initial VPN servers refresh", "timeout", timeout)
		return false
	case <-ctx.Done():
		return false
	}
}

// RefreshOfficial downloads the official Gluetun servers list, writes
// servers-official.json, and merges it into servers.json.
func (s *ServersRefreshService) RefreshOfficial(ctx context.Context) error {
	s.mu.Lock()
	if s.inProgress {
		s.mu.Unlock()
		return fmt.Errorf("refresh already in progress")
	}
	s.inProgress = true
	s.mu.Unlock()

	defer func() {
		s.mu.Lock()
		s.inProgress = false
		s.mu.Unlock()
	}()

	slog.Info("Refreshing VPN servers from official Gluetun source")

	payload, err := s.download(ctx, officialGluetunServersURL)
	if err != nil {
		s.recordResult(err)
		return err
	}

	dir := s.resolveDir()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		s.recordResult(err)
		return err
	}

	officialPath := filepath.Join(dir, "servers-official.json")
	mergedPath := filepath.Join(dir, "servers.json")

	if err := atomicWriteJSON(officialPath, payload); err != nil {
		s.recordResult(err)
		return err
	}

	// Update mode: merge into existing servers.json.
	existing := loadExistingJSON(mergedPath)
	if ver, ok := payload["version"]; ok {
		existing["version"] = ver
	}
	for key, val := range payload {
		if key == "version" {
			continue
		}
		// If servers.json already has a protonvpn section from a dedicated
		// Proton refresh (via the Python binary), preserve it rather than
		// overwriting with the potentially stale official list.
		if key == "protonvpn" {
			if _, exists := existing["protonvpn"]; exists {
				continue
			}
		}
		existing[key] = val
	}
	if err := atomicWriteJSON(mergedPath, existing); err != nil {
		s.recordResult(err)
		return err
	}

	slog.Info("VPN servers.json updated",
		"official_file", officialPath,
		"merged_file", mergedPath,
	)

	// Sync into the shared Docker volume.
	syncCtx, cancel := context.WithTimeout(ctx, 30*time.Second)
	defer cancel()
	if err := SyncServersToVolume(syncCtx, mergedPath); err != nil {
		slog.Warn("Failed to sync servers.json to Docker volume", "err", err)
	}

	s.recordResult(nil)
	// Signal that the first successful refresh has completed.
	s.once.Do(func() { close(s.initialDone) })
	return nil
}

// Status returns a snapshot of the refresh service state.
func (s *ServersRefreshService) Status() map[string]interface{} {
	s.mu.Lock()
	defer s.mu.Unlock()
	m := map[string]interface{}{
		"in_progress":  s.inProgress,
		"official_url": officialGluetunServersURL,
	}
	if !s.lastAt.IsZero() {
		m["last_at"] = s.lastAt
		m["last_ok"] = s.lastOK != nil && *s.lastOK
		m["last_error"] = s.lastErr
	}
	return m
}

func (s *ServersRefreshService) recordResult(err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.lastAt = time.Now().UTC()
	ok := err == nil
	s.lastOK = &ok
	if err != nil {
		s.lastErr = err.Error()
	} else {
		s.lastErr = ""
	}
}

func (s *ServersRefreshService) resolveDir() string {
	if s.serversDir != "" {
		return s.serversDir
	}
	if v := os.Getenv("GLUETUN_SERVERS_JSON_PATH"); v != "" {
		if filepath.Ext(v) == ".json" {
			return filepath.Dir(v)
		}
		return v
	}
	return "."
}

func (s *ServersRefreshService) download(ctx context.Context, url string) (map[string]interface{}, error) {
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("User-Agent", "acestream-orchestrator/1.0")

	client := &http.Client{Timeout: 60 * time.Second}
	resp, err := client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("HTTP %d from %s", resp.StatusCode, url)
	}

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var payload map[string]interface{}
	if err := json.Unmarshal(body, &payload); err != nil {
		return nil, fmt.Errorf("parsing Gluetun servers JSON: %w", err)
	}
	return payload, nil
}

func atomicWriteJSON(path string, payload map[string]interface{}) error {
	data, err := json.MarshalIndent(payload, "", "  ")
	if err != nil {
		return err
	}
	tmp := path + ".tmp"
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func loadExistingJSON(path string) map[string]interface{} {
	data, err := os.ReadFile(path)
	if err != nil {
		return map[string]interface{}{"version": 1}
	}
	var m map[string]interface{}
	if err := json.Unmarshal(data, &m); err != nil {
		return map[string]interface{}{"version": 1}
	}
	return m
}
