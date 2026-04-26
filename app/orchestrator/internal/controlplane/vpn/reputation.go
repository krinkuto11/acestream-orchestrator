package vpn

import (
	"context"
	"encoding/json"
	"fmt"
	"log/slog"
	"math/rand"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"time"

	goredis "github.com/redis/go-redis/v9"
)

// providerStorageAliases maps shorthand names to the canonical key used in servers.json.
var providerStorageAliases = map[string]string{
	"pia":                    "private internet access",
	"privateinternetaccess":  "private internet access",
	"private_internet_access": "private internet access",
}

// providerFlagAliases maps canonical names to gluetun format-servers flags.
var providerFlagAliases = map[string]string{
	"private internet access": "pia",
	"privateinternetaccess":   "pia",
}

type catalogEntry struct {
	mtime float64
	data  map[string]interface{}
	// providerKey -> []server
	index map[string][]map[string]interface{}
}

// ReputationManager manages VPN hostname reputation backed by a Redis blacklist
// and a file-based servers.json catalog with mtime caching.
type ReputationManager struct {
	mu          sync.Mutex
	catalogs    map[string]*catalogEntry // absolute path -> entry
	rdb         *goredis.Client
	serversDir  string // directory containing servers.json files
}

func NewReputationManager(rdb *goredis.Client, serversDir string) *ReputationManager {
	return &ReputationManager{
		catalogs:   make(map[string]*catalogEntry),
		rdb:        rdb,
		serversDir: serversDir,
	}
}

// ── Blacklist ─────────────────────────────────────────────────────────────────

func blacklistKey(hostname string) string {
	return fmt.Sprintf("ace_proxy:blacklist:vpn:%s", hostname)
}

// BlacklistHostname marks a VPN hostname as burned in Redis with a TTL.
func (rm *ReputationManager) BlacklistHostname(ctx context.Context, hostname string, ttlSeconds int) {
	hostname = strings.ToLower(strings.TrimSpace(hostname))
	if hostname == "" || rm.rdb == nil {
		return
	}
	if ttlSeconds <= 0 {
		ttlSeconds = 86400
	}
	if err := rm.rdb.SetEx(ctx, blacklistKey(hostname), "burned", time.Duration(ttlSeconds)*time.Second).Err(); err != nil {
		slog.Warn("VPN blacklist set failed", "hostname", hostname, "err", err)
		return
	}
	slog.Info("VPN hostname blacklisted", "hostname", hostname, "ttl_s", ttlSeconds)
}

// IsBlacklisted returns true if the hostname is currently blacklisted.
func (rm *ReputationManager) IsBlacklisted(ctx context.Context, hostname string) bool {
	hostname = strings.ToLower(strings.TrimSpace(hostname))
	if hostname == "" || rm.rdb == nil {
		return false
	}
	n, err := rm.rdb.Exists(ctx, blacklistKey(hostname)).Result()
	if err != nil {
		return false
	}
	return n > 0
}

// ── Catalog ───────────────────────────────────────────────────────────────────

func (rm *ReputationManager) serversJSONPath(filename string) string {
	// Check GLUETUN_SERVERS_JSON_PATH env var.
	configured := strings.TrimSpace(os.Getenv("GLUETUN_SERVERS_JSON_PATH"))
	if configured == "" && rm.serversDir != "" {
		configured = rm.serversDir
	}
	if configured != "" {
		ext := filepath.Ext(configured)
		if ext == ".json" {
			// It's a specific file path.
			if filename == "servers.json" {
				return configured
			}
			return filepath.Join(filepath.Dir(configured), filename)
		}
		return filepath.Join(configured, filename)
	}
	// Default: working directory.
	return filename
}

func (rm *ReputationManager) loadCatalog(filename string) (map[string]interface{}, error) {
	path := rm.serversJSONPath(filename)

	fi, err := os.Stat(path)
	if err != nil {
		if filename != "servers.json" {
			return rm.loadCatalog("servers.json")
		}
		return nil, fmt.Errorf("servers catalog not found at %s", path)
	}
	mtime := float64(fi.ModTime().UnixNano())

	rm.mu.Lock()
	cached, ok := rm.catalogs[path]
	rm.mu.Unlock()

	if ok && cached.mtime == mtime {
		return cached.data, nil
	}

	data, err := os.ReadFile(path)
	if err != nil {
		return nil, err
	}
	var payload map[string]interface{}
	if err := json.Unmarshal(data, &payload); err != nil {
		return nil, err
	}

	// Build provider index.
	index := make(map[string][]map[string]interface{})
	for key, section := range payload {
		if key == "version" {
			continue
		}
		sec, ok := section.(map[string]interface{})
		if !ok {
			continue
		}
		servers, ok := sec["servers"].([]interface{})
		if !ok {
			continue
		}
		var provServers []map[string]interface{}
		for _, s := range servers {
			if sm, ok := s.(map[string]interface{}); ok {
				provServers = append(provServers, sm)
			}
		}
		index[key] = provServers
	}

	entry := &catalogEntry{mtime: mtime, data: payload, index: index}
	rm.mu.Lock()
	rm.catalogs[path] = entry
	rm.mu.Unlock()

	totalServers := 0
	for _, ss := range index {
		totalServers += len(ss)
	}
	slog.Info("VPN servers catalog loaded",
		"file", filepath.Base(path),
		"providers", len(index),
		"total_servers", totalServers,
	)
	return payload, nil
}

// IsCatalogAvailable reports whether the catalog file exists on disk.
func (rm *ReputationManager) IsCatalogAvailable(filename string) bool {
	path := rm.serversJSONPath(filename)
	_, err := os.Stat(path)
	return err == nil
}

// ProviderServers returns the server list for a provider from the catalog.
func (rm *ReputationManager) ProviderServers(provider, catalogFile string) []map[string]interface{} {
	if _, err := rm.loadCatalog(catalogFile); err != nil {
		return nil
	}
	path := rm.serversJSONPath(catalogFile)
	if _, err := os.Stat(path); err != nil && catalogFile != "servers.json" {
		path = rm.serversJSONPath("servers.json")
	}

	provKey := normalizeProviderStorage(provider)
	rm.mu.Lock()
	entry := rm.catalogs[path]
	rm.mu.Unlock()

	if entry == nil {
		return nil
	}
	return entry.index[provKey]
}

// ── Hostname selection ────────────────────────────────────────────────────────

// GetSafeHostname selects a low-load, non-blacklisted hostname for the given
// provider/protocol/regions combination. Returns "" if none available.
func (rm *ReputationManager) GetSafeHostname(
	ctx context.Context,
	provider string,
	regions []string,
	protocol string,
	requirePortForwarding bool,
	catalogFile string,
) string {
	candidates := rm.candidateServers(provider, regions, protocol, requirePortForwarding, catalogFile)
	if len(candidates) == 0 {
		if requirePortForwarding {
			slog.Warn("No port-forwarding-capable servers in catalog",
				"provider", provider, "protocol", protocol, "regions", regions)
		}
		return ""
	}

	// Filter blacklisted.
	var safe []map[string]interface{}
	for _, s := range candidates {
		hn := strings.ToLower(strings.TrimSpace(strVal(s["hostname"])))
		if !rm.IsBlacklisted(ctx, hn) {
			safe = append(safe, s)
		}
	}
	if len(safe) == 0 {
		slog.Warn("All catalog hostnames blacklisted", "provider", provider)
		return ""
	}

	// Sort by load ascending (unknowns get load=100).
	sortByLoad(safe)

	// Check if any real load data exists.
	hasRealLoad := false
	limit := len(safe)
	if limit > 10 {
		limit = 10
	}
	for _, s := range safe[:limit] {
		if s["load"] != nil {
			hasRealLoad = true
			break
		}
	}

	if !hasRealLoad {
		// Pure random.
		chosen := safe[rand.Intn(len(safe))]
		hn := strings.ToLower(strings.TrimSpace(strVal(chosen["hostname"])))
		slog.Info("Selected VPN hostname (random, no load data)", "hostname", hn)
		return hn
	}

	// Pick randomly from top 5.
	top := safe
	if len(top) > 5 {
		top = top[:5]
	}
	chosen := top[rand.Intn(len(top))]
	hn := strings.ToLower(strings.TrimSpace(strVal(chosen["hostname"])))
	slog.Info("Selected VPN hostname", "hostname", hn, "load", chosen["load"], "candidates", len(top))
	return hn
}

func (rm *ReputationManager) candidateServers(
	provider string,
	regions []string,
	protocol string,
	requirePF bool,
	catalogFile string,
) []map[string]interface{} {
	servers := rm.ProviderServers(provider, catalogFile)
	if len(servers) == 0 {
		return nil
	}

	normalProto := normalizeProtocol(protocol)
	normalRegions := normalizeRegions(regions)

	var candidates []map[string]interface{}
	for _, s := range servers {
		hn := strings.TrimSpace(strVal(s["hostname"]))
		if hn == "" {
			continue
		}
		sProto := normalizeProtocol(strVal(s["vpn"]))
		if normalProto != "" && sProto != "" && sProto != normalProto {
			continue
		}
		if normalProto != "" && sProto == "" {
			continue
		}
		if requirePF && !serverSupportsPF(s) {
			continue
		}
		if len(normalRegions) > 0 && !serverMatchesRegions(s, normalRegions) {
			continue
		}
		candidates = append(candidates, s)
	}
	return candidates
}

// ── Helpers ───────────────────────────────────────────────────────────────────

func normalizeProviderStorage(provider string) string {
	n := strings.ToLower(strings.TrimSpace(provider))
	if v, ok := providerStorageAliases[n]; ok {
		return v
	}
	compact := regexp.MustCompile(`[^a-z0-9]`).ReplaceAllString(n, "")
	if v, ok := providerStorageAliases[compact]; ok {
		return v
	}
	return n
}

func normalizeProtocol(p interface{}) string {
	s := strings.ToLower(strings.TrimSpace(strVal(p)))
	if s == "wireguard" || s == "openvpn" {
		return s
	}
	return ""
}

func normalizeRegions(regions []string) []string {
	var out []string
	for _, r := range regions {
		v := strings.ToLower(strings.TrimSpace(r))
		if v == "" {
			continue
		}
		if idx := strings.Index(v, ":"); idx >= 0 {
			v = strings.TrimSpace(v[idx+1:])
		}
		if v != "" {
			out = append(out, v)
		}
	}
	return out
}

func serverSupportsPF(s map[string]interface{}) bool {
	pf := s["port_forward"]
	if pf == nil {
		return false
	}
	return coerceBool(pf)
}

func serverMatchesRegions(s map[string]interface{}, regions []string) bool {
	fields := []string{
		strings.ToLower(strVal(s["country"])),
		strings.ToLower(strVal(s["city"])),
		strings.ToLower(strVal(s["region"])),
		strings.ToLower(strVal(s["server_name"])),
		strings.ToLower(strVal(s["hostname"])),
	}
	for _, req := range regions {
		for _, f := range fields {
			if f != "" && (f == req || strings.Contains(f, req)) {
				return true
			}
		}
	}
	return false
}

func sortByLoad(servers []map[string]interface{}) {
	// Insertion sort — catalog sizes are typically <1000.
	for i := 1; i < len(servers); i++ {
		key := servers[i]
		ki := loadVal(key)
		j := i - 1
		for j >= 0 && loadVal(servers[j]) > ki {
			servers[j+1] = servers[j]
			j--
		}
		servers[j+1] = key
	}
}

func loadVal(s map[string]interface{}) int {
	if s["load"] == nil {
		return 100
	}
	switch v := s["load"].(type) {
	case float64:
		return int(v)
	case int:
		return v
	}
	return 100
}

func strVal(v interface{}) string {
	if v == nil {
		return ""
	}
	if s, ok := v.(string); ok {
		return s
	}
	return fmt.Sprintf("%v", v)
}

func coerceBool(v interface{}) bool {
	if b, ok := v.(bool); ok {
		return b
	}
	s := strings.ToLower(strings.TrimSpace(strVal(v)))
	return s == "1" || s == "true" || s == "yes" || s == "on"
}
