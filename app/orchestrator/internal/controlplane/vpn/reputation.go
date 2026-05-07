package vpn

import (
	"context"
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"math"
	"math/rand"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/persistence"
)

// providerStorageAliases maps shorthand names to the canonical key used in servers.json.
var providerStorageAliases = map[string]string{
	"pia":                     "private internet access",
	"privateinternetaccess":   "private internet access",
	"private_internet_access": "private internet access",
}

type catalogEntry struct {
	mtime float64
	data  map[string]interface{}
	index map[string][]map[string]interface{}
}

// ── CatalogManager ─────────────────────────────────────────────────────────────
// Handles servers.json catalog loading (kept separate from reputation scoring).

type CatalogManager struct {
	mu         sync.Mutex
	catalogs   map[string]*catalogEntry
	serversDir string
}

func NewCatalogManager(serversDir string) *CatalogManager {
	return &CatalogManager{
		catalogs:   make(map[string]*catalogEntry),
		serversDir: serversDir,
	}
}

func (cm *CatalogManager) serversJSONPath(filename string) string {
	configured := strings.TrimSpace(os.Getenv("GLUETUN_SERVERS_JSON_PATH"))
	if configured == "" && cm.serversDir != "" {
		configured = cm.serversDir
	}
	if configured != "" {
		ext := filepath.Ext(configured)
		if ext == ".json" {
			if filename == "servers.json" {
				return configured
			}
			return filepath.Join(filepath.Dir(configured), filename)
		}
		return filepath.Join(configured, filename)
	}
	return filename
}

func (cm *CatalogManager) loadCatalog(filename string) (map[string]interface{}, error) {
	path := cm.serversJSONPath(filename)

	fi, err := os.Stat(path)
	if err != nil {
		if filename != "servers.json" {
			return cm.loadCatalog("servers.json")
		}
		return nil, fmt.Errorf("servers catalog not found at %s", path)
	}
	mtime := float64(fi.ModTime().UnixNano())

	cm.mu.Lock()
	cached, ok := cm.catalogs[path]
	cm.mu.Unlock()

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
	cm.mu.Lock()
	cm.catalogs[path] = entry
	cm.mu.Unlock()

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

func (cm *CatalogManager) IsCatalogAvailable(filename string) bool {
	path := cm.serversJSONPath(filename)
	_, err := os.Stat(path)
	return err == nil
}

func (cm *CatalogManager) ProviderServers(provider, catalogFile string) []map[string]interface{} {
	if _, err := cm.loadCatalog(catalogFile); err != nil {
		return nil
	}
	path := cm.serversJSONPath(catalogFile)
	if _, err := os.Stat(path); err != nil && catalogFile != "servers.json" {
		path = cm.serversJSONPath("servers.json")
	}

	provKey := normalizeProviderStorage(provider)
	cm.mu.Lock()
	entry := cm.catalogs[path]
	cm.mu.Unlock()

	if entry == nil {
		return nil
	}
	return entry.index[provKey]
}

// ── ReputationEngine ──────────────────────────────────────────────────────────

// ProbeEngineSpawner abstracts engine provisioning for active probes.
// Implemented outside the vpn package to avoid circular imports with engine.
type ProbeEngineSpawner interface {
	// SpawnProbeEngine provisions a probe AceStream engine on the given VPN container.
	// Returns the container name, HTTP (webui) port, telnet API port, container ID, and any error.
	SpawnProbeEngine(ctx context.Context, vpnContainer string) (containerName string, httpPort int, apiPort int, containerID string, err error)
	// StopEngine stops and removes the probe engine container.
	StopEngine(ctx context.Context, containerID string) error
}

// ReputationEngine is the central coordinator for VPN server reputation.
// It replaces the old Redis-backed blacklist with a SQLite-backed scoring system.
type ReputationEngine struct {
	db      *sql.DB
	catalog *CatalogManager
	probes  *ProbeCollector
	spawner ProbeEngineSpawner
	prov    *Provisioner

	// serverIDCache maps "source:hostname" → server_id to avoid repeated DB lookups.
	serverIDMu    sync.Mutex
	serverIDCache map[string]string
}

func NewReputationEngine(db *sql.DB, serversDir string) *ReputationEngine {
	return &ReputationEngine{
		db:            db,
		catalog:       NewCatalogManager(serversDir),
		serverIDCache: make(map[string]string),
	}
}

func (re *ReputationEngine) SetProbeCollector(pc *ProbeCollector) {
	re.probes = pc
}

func (re *ReputationEngine) SetProbeEngineSpawner(s ProbeEngineSpawner) {
	re.spawner = s
}

func (re *ReputationEngine) SetProvisioner(p *Provisioner) {
	re.prov = p
}

// DB exposes the underlying database for API handlers.
func (re *ReputationEngine) DB() *sql.DB {
	return re.db
}

// Start launches all background jobs. Call with a root context.
func (re *ReputationEngine) Start(ctx context.Context) {
	go runReputationJobs(ctx, re.db, re)
}

// ── Server ID resolution ──────────────────────────────────────────────────────

// ResolveServerID returns (or creates) the canonical server ID for a hostname/source pair.
func (re *ReputationEngine) ResolveServerID(ctx context.Context, source, hostname string) (string, error) {
	hostname = strings.ToLower(strings.TrimSpace(hostname))
	if hostname == "" || source == "" {
		return "", fmt.Errorf("empty hostname or source")
	}

	key := source + ":" + hostname
	re.serverIDMu.Lock()
	if id, ok := re.serverIDCache[key]; ok {
		re.serverIDMu.Unlock()
		return id, nil
	}
	re.serverIDMu.Unlock()

	id := persistence.ServerID(source, hostname)

	// Ensure a row exists (upsert with minimal data; catalog sync fills details later).
	existing, _ := persistence.GetVPNServer(ctx, re.db, id)
	if existing == nil {
		_ = persistence.UpsertVPNServer(ctx, re.db, persistence.VPNServerRow{
			ID:       id,
			Source:   source,
			Hostname: hostname,
			Status:   "unknown",
		})
	}

	re.serverIDMu.Lock()
	re.serverIDCache[key] = id
	re.serverIDMu.Unlock()
	return id, nil
}

// ── Scheduling integration ────────────────────────────────────────────────────

// GetSafeHostname selects a high-reputation, non-excluded hostname from the SQLite catalog.
// Falls back to catalog-only selection if the reputation table is empty.
func (re *ReputationEngine) GetSafeHostname(
	ctx context.Context,
	provider string,
	regions []string,
	protocol string,
	requirePortForwarding bool,
	catalogFile string,
	excludeHostnames []string,
) string {
	excluded := make(map[string]bool, len(excludeHostnames))
	for _, h := range excludeHostnames {
		excluded[strings.ToLower(strings.TrimSpace(h))] = true
	}

	cfg := config.C.Load()

	// Try reputation-ranked selection when enabled and data exists.
	if cfg.ReputationEnabled {
		candidates, err := persistence.GetScoredCandidates(ctx, re.db, "_overall", excluded)
		if err == nil && len(candidates) > 0 {
			// Filter by catalog constraints (provider, regions, protocol, port-forwarding).
			catalogCandidates := re.candidateServers(provider, regions, protocol, requirePortForwarding, catalogFile)
			allowed := make(map[string]bool, len(catalogCandidates))
			for _, s := range catalogCandidates {
				hn := strings.ToLower(strings.TrimSpace(strVal(s["hostname"])))
				allowed[hn] = true
			}

			var eligible []persistence.ScoredServer
			for _, c := range candidates {
				if len(allowed) == 0 || allowed[strings.ToLower(c.Hostname)] {
					eligible = append(eligible, c)
				}
			}

			if len(eligible) > 0 {
				eligible = applyExplorationBonus(ctx, re.db, eligible, cfg)
				chosen := pickWithJitter(eligible, cfg.ReputationPickTopN)
				slog.Info("Selected VPN hostname by reputation", "hostname", chosen.Hostname, "score", chosen.Score)
				return chosen.Hostname
			}
		}
	}

	// Fallback: catalog-based selection (load-ranked).
	return re.getSafeHostnameCatalog(ctx, provider, regions, protocol, requirePortForwarding, catalogFile, excluded)
}

// getSafeHostnameCatalog is the original catalog-based hostname selection logic.
func (re *ReputationEngine) getSafeHostnameCatalog(
	ctx context.Context,
	provider string,
	regions []string,
	protocol string,
	requirePortForwarding bool,
	catalogFile string,
	excluded map[string]bool,
) string {
	candidates := re.candidateServers(provider, regions, protocol, requirePortForwarding, catalogFile)
	if len(candidates) == 0 {
		if requirePortForwarding {
			slog.Warn("No port-forwarding-capable servers in catalog",
				"provider", provider, "protocol", protocol, "regions", regions)
		}
		return ""
	}

	var safe []map[string]interface{}
	for _, s := range candidates {
		hn := strings.ToLower(strings.TrimSpace(strVal(s["hostname"])))
		if !excluded[hn] {
			safe = append(safe, s)
		}
	}
	if len(safe) == 0 {
		slog.Warn("All catalog hostnames excluded", "provider", provider)
		return ""
	}

	sortByLoad(safe)

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
		chosen := safe[rand.Intn(len(safe))]
		hn := strings.ToLower(strings.TrimSpace(strVal(chosen["hostname"])))
		slog.Info("Selected VPN hostname (random, no load data)", "hostname", hn)
		return hn
	}

	top := safe
	if len(top) > 5 {
		top = top[:5]
	}
	chosen := top[rand.Intn(len(top))]
	hn := strings.ToLower(strings.TrimSpace(strVal(chosen["hostname"])))
	slog.Info("Selected VPN hostname (catalog)", "hostname", hn, "load", chosen["load"])
	return hn
}

// IsExcluded returns true if the server is down or quarantined.
func (re *ReputationEngine) IsExcluded(ctx context.Context, hostname string) bool {
	hostname = strings.ToLower(strings.TrimSpace(hostname))
	if hostname == "" {
		return false
	}
	// Try to find in DB by hostname (check both sources).
	for _, source := range []string{"proton", "gluetun"} {
		srv, err := persistence.GetVPNServerByHostname(ctx, re.db, source, hostname)
		if err != nil || srv == nil {
			continue
		}
		if srv.Status == "down" {
			return true
		}
		if srv.QuarantinedUntil != nil && srv.QuarantinedUntil.After(time.Now()) {
			return true
		}
		return false
	}
	return false
}

// SetQuarantine sets or lifts a quarantine on a server.
func (re *ReputationEngine) SetQuarantine(ctx context.Context, serverID string, until *time.Time, reason string) error {
	return persistence.SetQuarantine(ctx, re.db, serverID, until, reason)
}

// SetPinned sets the pinned flag on a server.
func (re *ReputationEngine) SetPinned(ctx context.Context, serverID string, pinned bool) error {
	return persistence.SetPinned(ctx, re.db, serverID, pinned)
}

// RecordProbe enqueues a probe for async write to the DB.
func (re *ReputationEngine) RecordProbe(probe persistence.VPNProbeRow) {
	if re.probes != nil {
		re.probes.Record(probe)
	}
}

// ── Catalog-facing methods (used by node_provisioner) ─────────────────────────

func (re *ReputationEngine) IsCatalogAvailable(filename string) bool {
	return re.catalog.IsCatalogAvailable(filename)
}

func (re *ReputationEngine) ProviderServers(provider, catalogFile string) []map[string]interface{} {
	return re.catalog.ProviderServers(provider, catalogFile)
}

func (re *ReputationEngine) candidateServers(
	provider string,
	regions []string,
	protocol string,
	requirePF bool,
	catalogFile string,
) []map[string]interface{} {
	servers := re.catalog.ProviderServers(provider, catalogFile)
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

// ── Catalog → DB sync ────────────────────────────────────────────────────────

// sourceForProvider maps a Gluetun servers.json provider key to the DB source value.
// The DB CHECK constraint only allows "proton" or "gluetun".
func sourceForProvider(key string) string {
	if key == "protonvpn" {
		return "proton"
	}
	return "gluetun"
}

// SyncCatalogToDB imports all servers from servers.json into the vpn_server table
// so the VPN page has rows to display immediately after a refresh.
func (re *ReputationEngine) SyncCatalogToDB(ctx context.Context) error {
	payload, err := re.catalog.loadCatalog("servers.json")
	if err != nil {
		return fmt.Errorf("SyncCatalogToDB: load catalog: %w", err)
	}

	type seenKey struct{ source, id string }
	seenBySource := make(map[string][]string)

	for provKey, section := range payload {
		if provKey == "version" {
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

		source := sourceForProvider(provKey)

		for _, rawSrv := range servers {
			srv, ok := rawSrv.(map[string]interface{})
			if !ok {
				continue
			}
			hn := strings.ToLower(strings.TrimSpace(strVal(srv["hostname"])))
			if hn == "" {
				continue
			}

			id := persistence.ServerID(source, hn)

			var ips []string
			if rawIPs, ok := srv["ips"].([]interface{}); ok {
				for _, ip := range rawIPs {
					if s := strings.TrimSpace(strVal(ip)); s != "" {
						ips = append(ips, s)
					}
				}
			}

			var loadPct *int
			if lv, ok := srv["load"].(float64); ok {
				v := int(lv)
				loadPct = &v
			}

			tier := 0
			if tv, ok := srv["tier"].(float64); ok {
				tier = int(tv)
			}

			flags := persistence.VPNServerFlags{
				PortForward: coerceBool(srv["port_forward"]),
				SecureCore:  coerceBool(srv["secure_core"]),
				Tor:         coerceBool(srv["tor"]),
				Free:        coerceBool(srv["free"]),
				Stream:      coerceBool(srv["stream"]),
			}

			cc := strVal(srv["country_code"])
			if cc == "" {
				cc = strVal(srv["countrycode"])
			}

			row := persistence.VPNServerRow{
				ID:          id,
				Source:      source,
				Hostname:    hn,
				IPs:         ips,
				Country:     strVal(srv["country"]),
				CountryCode: strings.ToUpper(cc),
				City:        strVal(srv["city"]),
				ServerName:  strVal(srv["server_name"]),
				Tier:        tier,
				Flags:       flags,
				LoadPct:     loadPct,
				Status:      "unknown",
			}

			if err := persistence.UpsertVPNServer(ctx, re.db, row); err != nil {
				slog.Warn("SyncCatalogToDB: upsert failed", "id", id, "err", err)
				continue
			}
			seenBySource[source] = append(seenBySource[source], id)
		}
	}

	for source, ids := range seenBySource {
		if err := persistence.MarkServersDown(ctx, re.db, source, ids); err != nil {
			slog.Warn("SyncCatalogToDB: mark down failed", "source", source, "err", err)
		}
	}

	totals := make([]string, 0, len(seenBySource))
	for src, ids := range seenBySource {
		totals = append(totals, fmt.Sprintf("%s=%d", src, len(ids)))
	}
	slog.Info("SyncCatalogToDB: catalog imported into DB", "by_source", strings.Join(totals, " "))
	return nil
}

// ── Jitter selection ─────────────────────────────────────────────────────────

// applyExplorationBonus adds a UCB1 exploration bonus to each candidate,
// boosting servers with few probes so they compete against well-scored incumbents.
// After boosting, the slice is re-sorted by effective score descending.
func applyExplorationBonus(ctx context.Context, db *sql.DB, candidates []persistence.ScoredServer, cfg *config.Config) []persistence.ScoredServer {
	C := cfg.ReputationExplorationC
	if C <= 0 {
		return candidates
	}
	totalN, err := persistence.GetTotalProbeCount(ctx, db)
	if err != nil || totalN == 0 {
		return candidates
	}
	logN := math.Log(float64(totalN + 1))
	for i := range candidates {
		bonus := C * math.Sqrt(logN/float64(candidates[i].ProbesN+1))
		candidates[i].Score += bonus
	}
	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].Pinned != candidates[j].Pinned {
			return candidates[i].Pinned
		}
		return candidates[i].Score > candidates[j].Score
	})
	return candidates
}

// pickWithJitter selects from the top-topN candidates with small score jitter to avoid herding.
func pickWithJitter(candidates []persistence.ScoredServer, topN int) persistence.ScoredServer {
	if len(candidates) == 0 {
		return persistence.ScoredServer{}
	}

	// Prefer pinned servers first.
	for _, c := range candidates {
		if c.Pinned {
			return c
		}
	}

	if topN <= 0 {
		topN = 5
	}
	top := candidates
	if len(top) > topN {
		top = top[:topN]
	}
	best := top[0]
	bestScore := best.Score + (rand.Float64()-0.5)*0.06
	for _, c := range top[1:] {
		s := c.Score + (rand.Float64()-0.5)*0.06
		if s > bestScore {
			bestScore = s
			best = c
		}
	}
	return best
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
