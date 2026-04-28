package vpn

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"strings"
	"sync"
	"time"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/state"
)

// controlAPIURL returns the Gluetun control API base URL for a given container.
// It prefers the stored ControlHost (IP address) over the container name so
// that the API is reachable even when the orchestrator and the Gluetun
// container are attached to different Docker networks.
func controlAPIURL(vpnContainer string) string {
	host := vpnContainer
	if n, ok := state.Global.GetVPNNode(vpnContainer); ok && n.ControlHost != "" {
		host = n.ControlHost
	}
	return fmt.Sprintf("http://%s:%d", host, config.C.Load().GluetunAPIPort)
}

// ── Forwarded-port cache ──────────────────────────────────────────────────────

type portCacheEntry struct {
	port      int
	expiresAt time.Time
}

var (
	portCacheMu sync.Mutex
	portCache   = make(map[string]portCacheEntry)
)

func cachedForwardedPort(vpnContainer string) (int, bool) {
	portCacheMu.Lock()
	defer portCacheMu.Unlock()
	e, ok := portCache[vpnContainer]
	if !ok || time.Now().After(e.expiresAt) {
		return 0, false
	}
	return e.port, true
}

func setPortCache(vpnContainer string, port int) {
	ttl := config.C.Load().GluetunPortCacheTTL
	if ttl == 0 {
		ttl = 60 * time.Second
	}
	portCacheMu.Lock()
	defer portCacheMu.Unlock()
	portCache[vpnContainer] = portCacheEntry{port: port, expiresAt: time.Now().Add(ttl)}
}

func evictPortCache(vpnContainer string) {
	portCacheMu.Lock()
	defer portCacheMu.Unlock()
	delete(portCache, vpnContainer)
}

// ── API helpers ───────────────────────────────────────────────────────────────

// IsControlAPIReachable checks whether the Gluetun control API is up and
// (optionally) reports connected=true.
//
// Connectivity detection strategy:
//  1. GET /v1/publicip/ip — if it returns a non-empty public_ip, the VPN tunnel
//     is provably up (this endpoint is always auth-free in Gluetun).
//  2. Fallback to /v1/openvpn/status or /v1/wireguard/status. A 401 on these
//     endpoints means the API server is up and auth is configured on the
//     endpoint (e.g. via a persistent auth/config.toml in the Gluetun volume);
//     the tunnel is considered connected in that case too.
func IsControlAPIReachable(vpnContainer string, requireConnected bool) bool {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	url := controlAPIURL(vpnContainer) + "/v1/publicip/ip"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return false
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 500 {
		return false
	}

	if !requireConnected {
		return true
	}

	// Primary connectivity signal: parse the public IP from the response body.
	// A non-empty public_ip means the VPN tunnel is carrying internet traffic.
	// This endpoint is always auth-free in Gluetun regardless of auth config.
	body, _ := io.ReadAll(resp.Body)
	var ipResp struct {
		IP string `json:"public_ip"`
	}
	if err := json.Unmarshal(body, &ipResp); err == nil && ipResp.IP != "" {
		return true
	}

	// Fallback: try OpenVPN status.
	ctx2, cancel2 := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel2()

	statusURL := controlAPIURL(vpnContainer) + "/v1/openvpn/status"
	req2, err := http.NewRequestWithContext(ctx2, http.MethodGet, statusURL, nil)
	if err != nil {
		return isWireguardConnected(vpnContainer)
	}
	resp2, err := http.DefaultClient.Do(req2)
	if err != nil {
		return isWireguardConnected(vpnContainer)
	}
	defer resp2.Body.Close()

	// 401 = auth required = server is up = treat as connected.
	if resp2.StatusCode == http.StatusUnauthorized {
		return true
	}

	body2, _ := io.ReadAll(resp2.Body)
	var statusResp struct {
		Status string `json:"status"`
	}
	if err := json.Unmarshal(body2, &statusResp); err != nil {
		return isWireguardConnected(vpnContainer)
	}
	if strings.ToLower(statusResp.Status) == "running" {
		return true
	}
	// OpenVPN returned but reported not-running — try WireGuard before giving up.
	return isWireguardConnected(vpnContainer)
}

func isWireguardConnected(vpnContainer string) bool {
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	url := controlAPIURL(vpnContainer) + "/v1/wireguard/status"
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return false
	}
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return false
	}
	defer resp.Body.Close()

	// 401 = auth required = server is up = treat as connected.
	if resp.StatusCode == http.StatusUnauthorized {
		return true
	}

	body, _ := io.ReadAll(resp.Body)
	var s struct {
		Status string `json:"status"`
	}
	if err := json.Unmarshal(body, &s); err != nil {
		return false
	}
	return strings.ToLower(s.Status) == "running"
}

// GetForwardedPort fetches the current forwarded port from the Gluetun control
// API, with a TTL cache to avoid hammering the API on every probe.
func GetForwardedPort(vpnContainer string) int {
	if p, ok := cachedForwardedPort(vpnContainer); ok {
		return p
	}

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	// Try universal portforward endpoint first
	port := fetchPort(ctx, vpnContainer, "/v1/portforward")
	if port > 0 {
		setPortCache(vpnContainer, port)
	}
	return port
}

func fetchPort(ctx context.Context, vpnContainer, path string) int {
	url := controlAPIURL(vpnContainer) + path
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, url, nil)
	if err != nil {
		return 0
	}

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return 0
	}
	defer resp.Body.Close()

	if resp.StatusCode != http.StatusOK {
		return 0
	}

	body, _ := io.ReadAll(resp.Body)
	var portResp struct {
		Port int `json:"port"`
	}
	if err := json.Unmarshal(body, &portResp); err != nil {
		return 0
	}
	return portResp.Port
}

// WaitForForwardedPort polls the Gluetun control API until a forwarded port is
// available, the deadline expires, or ctx is cancelled.
func WaitForForwardedPort(ctx context.Context, vpnContainer string) int {
	deadline := time.Now().Add(30 * time.Second)
	for time.Now().Before(deadline) {
		select {
		case <-ctx.Done():
			return 0
		default:
		}
		if p := GetForwardedPort(vpnContainer); p > 0 {
			return p
		}
		select {
		case <-ctx.Done():
			return 0
		case <-time.After(500 * time.Millisecond):
		}
	}
	slog.Warn("timed out waiting for forwarded port", "vpn", vpnContainer)
	return 0
}

// InvalidatePortCache evicts the cached forwarded port for a VPN container.
// Call this when a VPN reconnects so stale ports are not returned.
func InvalidatePortCache(vpnContainer string) {
	evictPortCache(vpnContainer)
}

// portForwardingProviders is the set of VPN providers that natively support
// port forwarding (same list as Python PORT_FORWARDING_NATIVE_PROVIDERS).
var portForwardingProviders = map[string]bool{
	"private internet access": true,
	"perfect privacy":         true,
	"privatevpn":              true,
	"protonvpn":               true,
}

// ProviderSupportsForwarding returns true if the given provider natively
// supports port forwarding.
func ProviderSupportsForwarding(provider string) bool {
	return portForwardingProviders[strings.ToLower(strings.TrimSpace(provider))]
}
