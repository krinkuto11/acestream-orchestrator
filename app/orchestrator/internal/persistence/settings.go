package persistence

import (
	"database/sql"
	"encoding/json"
	"fmt"
	"log/slog"
	"sync"
	"time"
)

const settingsRowID = 1

// SettingsStore persists the five settings categories to SQLite and serves
// reads from a hot in-memory cache.
type SettingsStore struct {
	db  *sql.DB
	mu  sync.RWMutex
	// cached categories (never nil after first load)
	cache map[string]map[string]any
}

// NewSettingsStore initialises the store, ensuring the singleton row exists.
func NewSettingsStore(db *sql.DB) (*SettingsStore, error) {
	s := &SettingsStore{db: db}
	if err := s.ensureRow(); err != nil {
		return nil, err
	}
	if err := s.loadCache(); err != nil {
		return nil, err
	}
	return s, nil
}

// ── Public API ────────────────────────────────────────────────────────────────

// GetAll returns a deep copy of all five categories.
func (s *SettingsStore) GetAll() map[string]map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return deepCopyCategories(s.cache)
}

// Get returns a deep copy of one category.
func (s *SettingsStore) Get(category string) map[string]any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return deepCopyMap(s.cache[category])
}

// GetValue returns a single value from a category, or the provided default.
func (s *SettingsStore) GetValue(category, key string, def any) any {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if cat, ok := s.cache[category]; ok {
		if v, ok := cat[key]; ok {
			return v
		}
	}
	return def
}

// Save replaces one category atomically (DB + cache).
func (s *SettingsStore) Save(category string, payload map[string]any) error {
	// Normalize before persisting so values in the DB are always clean.
	switch category {
	case "proxy_settings":
		payload = normalizeProxySettings(payload)
	case "vpn_settings":
		payload = normalizeVPNSettings(payload)
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return fmt.Errorf("marshal %s: %w", category, err)
	}
	col := categoryColumn(category)
	if col == "" {
		return fmt.Errorf("unknown settings category: %s", category)
	}

	s.mu.Lock()
	defer s.mu.Unlock()

	query := fmt.Sprintf(
		`UPDATE runtime_settings SET %s = ?, updated_at = ? WHERE id = ?`,
		col,
	)
	if _, err := s.db.Exec(query, string(data), time.Now().UTC(), settingsRowID); err != nil {
		return fmt.Errorf("update %s: %w", category, err)
	}
	// Special case: vpn_settings saves credentials separately.
	if category == "vpn_settings" {
		if creds, ok := payload["credentials"]; ok {
			if list, ok := creds.([]any); ok {
				if err := s.saveCredentials(list, payload); err != nil {
					slog.Warn("Failed to persist VPN credentials", "err", err)
				}
			}
		}
	}
	cp := deepCopyMap(payload)
	s.cache[category] = cp
	return nil
}

// SaveCredentials replaces the vpn_credentials table for the singleton row.
func (s *SettingsStore) saveCredentials(list []any, vpnSettings map[string]any) error {
	tx, err := s.db.Begin()
	if err != nil {
		return err
	}
	defer tx.Rollback()

	if _, err := tx.Exec(`DELETE FROM vpn_credentials WHERE settings_id = ?`, settingsRowID); err != nil {
		return err
	}

	provider, _ := vpnSettings["provider"].(string)
	protocol, _ := vpnSettings["protocol"].(string)
	now := time.Now().UTC()

	for _, raw := range list {
		item, ok := raw.(map[string]any)
		if !ok {
			continue
		}
		id, _ := item["id"].(string)
		if id == "" {
			continue
		}
		p, _ := item["provider"].(string)
		if p == "" {
			p = provider
		}
		pr, _ := item["protocol"].(string)
		if pr == "" {
			pr = protocol
		}
		blob, _ := json.Marshal(item)
		if _, err := tx.Exec(
			`INSERT OR REPLACE INTO vpn_credentials(id, settings_id, provider, protocol, payload, created_at) VALUES(?,?,?,?,?,?)`,
			id, settingsRowID, p, pr, string(blob), now,
		); err != nil {
			return err
		}
	}
	return tx.Commit()
}

// GetCredentials returns all VPN credentials from the DB ordered by creation time.
func (s *SettingsStore) GetCredentials() ([]map[string]any, error) {
	rows, err := s.db.Query(
		`SELECT payload FROM vpn_credentials WHERE settings_id = ? ORDER BY created_at ASC`,
		settingsRowID,
	)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	var out []map[string]any
	for rows.Next() {
		var blob string
		if err := rows.Scan(&blob); err != nil {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(blob), &m); err != nil {
			continue
		}
		out = append(out, m)
	}
	return out, rows.Err()
}

// ── Internal helpers ──────────────────────────────────────────────────────────

func (s *SettingsStore) ensureRow() error {
	var count int
	if err := s.db.QueryRow(`SELECT COUNT(*) FROM runtime_settings`).Scan(&count); err != nil {
		return err
	}
	if count > 0 {
		return nil
	}
	defaults := defaultCategories()
	for _, cat := range []string{"engine_config", "engine_settings", "orchestrator_settings", "proxy_settings", "vpn_settings"} {
		blob, _ := json.Marshal(defaults[cat])
		if _, err := s.db.Exec(
			fmt.Sprintf(`INSERT OR IGNORE INTO runtime_settings(id, %s, updated_at) VALUES(?, ?, ?)`, categoryColumn(cat)),
			settingsRowID, string(blob), time.Now().UTC(),
		); err != nil {
			// Might already exist from a parallel init — ignore.
		}
		_ = blob
	}
	// Simpler: insert the full row at once.
	var (
		ec, _ = json.Marshal(defaults["engine_config"])
		es, _ = json.Marshal(defaults["engine_settings"])
		os_, _ = json.Marshal(defaults["orchestrator_settings"])
		ps, _ = json.Marshal(defaults["proxy_settings"])
		vs, _ = json.Marshal(defaults["vpn_settings"])
	)
	_, err := s.db.Exec(
		`INSERT OR IGNORE INTO runtime_settings(id, engine_config, engine_settings, orchestrator_settings, proxy_settings, vpn_settings, updated_at) VALUES(?,?,?,?,?,?,?)`,
		settingsRowID, string(ec), string(es), string(os_), string(ps), string(vs), time.Now().UTC(),
	)
	return err
}

func (s *SettingsStore) loadCache() error {
	row := s.db.QueryRow(
		`SELECT engine_config, engine_settings, orchestrator_settings, proxy_settings, vpn_settings FROM runtime_settings WHERE id = ?`,
		settingsRowID,
	)
	var ec, es, os_, ps, vs string
	if err := row.Scan(&ec, &es, &os_, &ps, &vs); err != nil {
		return err
	}

	defaults := defaultCategories()
	s.cache = make(map[string]map[string]any, 5)

	for cat, blob := range map[string]string{
		"engine_config":        ec,
		"engine_settings":      es,
		"orchestrator_settings": os_,
		"proxy_settings":       ps,
		"vpn_settings":         vs,
	} {
		m := deepCopyMap(defaults[cat])
		if blob != "" && blob != "{}" {
			var loaded map[string]any
			if err := json.Unmarshal([]byte(blob), &loaded); err == nil {
				for k, v := range loaded {
					// Don't override a positive numeric default with zero —
					// a stored 0 for a timeout/interval means "unset/corrupted".
					if defV, hasDefault := m[k]; hasDefault && isPositiveNumeric(defV) && isZeroNumeric(v) {
						continue
					}
					m[k] = v
				}
			}
		}
		s.cache[cat] = m
	}

	// Attach credentials to vpn_settings cache.
	creds, err := s.GetCredentials()
	if err == nil {
		s.cache["vpn_settings"]["credentials"] = creds
	}

	return nil
}

func categoryColumn(category string) string {
	switch category {
	case "engine_config":
		return "engine_config"
	case "engine_settings":
		return "engine_settings"
	case "orchestrator_settings":
		return "orchestrator_settings"
	case "proxy_settings":
		return "proxy_settings"
	case "vpn_settings":
		return "vpn_settings"
	default:
		return ""
	}
}

// ── Defaults ──────────────────────────────────────────────────────────────────

func defaultCategories() map[string]map[string]any {
	return map[string]map[string]any{
		"engine_config":        defaultEngineConfig(),
		"engine_settings":      defaultEngineSettings(),
		"orchestrator_settings": defaultOrchestratorSettings(),
		"proxy_settings":       defaultProxySettings(),
		"vpn_settings":         defaultVPNSettings(),
	}
}

func defaultEngineConfig() map[string]any {
	return map[string]any{
		"total_max_download_rate":        0,
		"total_max_upload_rate":          0,
		"live_cache_type":                "memory",
		"buffer_time":                    10,
		"memory_limit":                   nil,
		"parameters":                     []any{},
		"torrent_folder_mount_enabled":   false,
		"torrent_folder_host_path":       nil,
		"torrent_folder_container_path":  nil,
		"disk_cache_mount_enabled":       false,
		"disk_cache_prune_enabled":       false,
		"disk_cache_prune_interval":      1440,
	}
}

func defaultEngineSettings() map[string]any {
	return map[string]any{
		"min_replicas":   2,
		"max_replicas":   6,
		"auto_delete":    true,
		"manual_mode":    false,
		"manual_engines": []any{},
	}
}

func defaultOrchestratorSettings() map[string]any {
	return map[string]any{
		"monitor_interval_s":                    10,
		"engine_grace_period_s":                 30,
		"autoscale_interval_s":                  30,
		"startup_timeout_s":                     25,
		"idle_ttl_s":                            600,
		"collect_interval_s":                    1,
		"stats_history_max":                     720,
		"health_check_interval_s":               20,
		"health_failure_threshold":              3,
		"health_unhealthy_grace_period_s":       60,
		"health_replacement_cooldown_s":         60,
		"circuit_breaker_failure_threshold":     5,
		"circuit_breaker_recovery_timeout_s":    300,
		"circuit_breaker_replacement_threshold": 3,
		"circuit_breaker_replacement_timeout_s": 180,
		"max_concurrent_provisions":             5,
		"min_provision_interval_s":              0.5,
		"port_range_host":                       "19000-19999",
		"ace_http_range":                        "40000-44999",
		"ace_https_range":                       "45000-49999",
		"debug_mode":                            false,
	}
}

func defaultProxySettings() map[string]any {
	return map[string]any{
		"initial_data_wait_timeout":  10,
		"initial_data_check_interval": 0.2,
		"no_data_timeout_checks":     60,
		"no_data_check_interval":     1.0,
		"connection_timeout":         30,
		"upstream_connect_timeout":   3,
		"upstream_read_timeout":      90,
		"stream_timeout":             60,
		"channel_shutdown_delay":     5,
		"proxy_prebuffer_seconds":    3,
		"pacing_bitrate_multiplier":  1.5,
		"max_streams_per_engine":     3,
		"stream_mode":                "TS",
		"control_mode":               "api",
		"legacy_api_preflight_tier":  "light",
		"ace_live_edge_delay":        0,
		"hls_max_segments":           20,
		"hls_initial_segments":       3,
		"hls_window_size":            6,
		"hls_buffer_ready_timeout":   30,
		"hls_first_segment_timeout":  30,
		"hls_initial_buffer_seconds": 10,
		"hls_max_initial_segments":   10,
		"hls_segment_fetch_interval": 0.5,
	}
}

func defaultVPNSettings() map[string]any {
	return map[string]any{
		"enabled":                    false,
		"dynamic_vpn_management":     true,
		"preferred_engines_per_vpn":  10,
		"protocol":                   "wireguard",
		"provider":                   "protonvpn",
		"regions":                    []any{},
		"api_port":                   8001,
		"health_check_interval_s":    5,
		"port_cache_ttl_s":           60,
		"restart_engines_on_reconnect": true,
		"unhealthy_restart_timeout_s": 60,
		"vpn_servers_auto_refresh":   false,
		"vpn_servers_refresh_period_s": 86400,
		"vpn_servers_refresh_source": "gluetun_official",
		"vpn_servers_gluetun_json_mode": "update",
		"vpn_servers_storage_path":   nil,
		"vpn_servers_official_url":   "https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json",
		"vpn_servers_proton_credentials_source": "env",
		"vpn_servers_proton_username_env":  "PROTON_USERNAME",
		"vpn_servers_proton_password_env":  "PROTON_PASSWORD",
		"vpn_servers_proton_totp_code_env": "PROTON_TOTP_CODE",
		"vpn_servers_proton_totp_secret_env": "PROTON_TOTP_SECRET",
		"vpn_servers_proton_username":    nil,
		"vpn_servers_proton_password":    nil,
		"vpn_servers_proton_totp_code":   nil,
		"vpn_servers_proton_totp_secret": nil,
		"vpn_servers_filter_ipv6":        "exclude",
		"vpn_servers_filter_secure_core": "include",
		"vpn_servers_filter_tor":         "include",
		"vpn_servers_filter_free_tier":   "include",
		"credentials":                    []any{},
		"wireguard_mtu":                  0,
	}
}

// ── Util ──────────────────────────────────────────────────────────────────────

func deepCopyMap(m map[string]any) map[string]any {
	if m == nil {
		return map[string]any{}
	}
	data, _ := json.Marshal(m)
	var out map[string]any
	_ = json.Unmarshal(data, &out)
	return out
}

func deepCopyCategories(cats map[string]map[string]any) map[string]map[string]any {
	out := make(map[string]map[string]any, len(cats))
	for k, v := range cats {
		out[k] = deepCopyMap(v)
	}
	return out
}

// ── Normalization ─────────────────────────────────────────────────────────────

func normalizeProxySettings(m map[string]any) map[string]any {
	out := deepCopyMap(m)

	// Integer fields
	for _, k := range []string{
		"initial_data_wait_timeout", "no_data_timeout_checks",
		"connection_timeout", "upstream_connect_timeout", "upstream_read_timeout",
		"stream_timeout", "channel_shutdown_delay", "max_streams_per_engine",
		"hls_max_segments", "hls_initial_segments", "hls_window_size",
		"hls_buffer_ready_timeout", "hls_first_segment_timeout",
		"hls_initial_buffer_seconds", "hls_max_initial_segments",
		"ace_live_edge_delay",
	} {
		if v, ok := out[k]; ok {
			out[k] = toIntNorm(v)
		}
	}

	// Float fields
	for _, k := range []string{
		"initial_data_check_interval", "no_data_check_interval",
		"proxy_prebuffer_seconds", "pacing_bitrate_multiplier",
		"hls_segment_fetch_interval",
	} {
		if v, ok := out[k]; ok {
			out[k] = toFloatNorm(v)
		}
	}

	// Enum: stream_mode
	if v, ok := out["stream_mode"].(string); ok {
		if v != "TS" && v != "HLS" {
			out["stream_mode"] = "TS"
		}
	}

	// Enum: control_mode
	if v, ok := out["control_mode"].(string); ok {
		if v != "http" && v != "api" {
			out["control_mode"] = "api"
		}
	}

	// Enum: legacy_api_preflight_tier
	if v, ok := out["legacy_api_preflight_tier"].(string); ok {
		valid := map[string]bool{"light": true, "standard": true, "heavy": true}
		if !valid[v] {
			out["legacy_api_preflight_tier"] = "light"
		}
	}

	// Enforce positive minimums for timeout/interval fields so a UI save
	// of 0 (field not rendered) can't override the working default.
	timeoutFloors := map[string]int{
		"upstream_read_timeout":      90,
		"upstream_connect_timeout":   3,
		"connection_timeout":         30,
		"stream_timeout":             60,
		"channel_shutdown_delay":     5,
		"initial_data_wait_timeout":  10,
		"no_data_timeout_checks":     60,
		"hls_buffer_ready_timeout":   30,
		"hls_first_segment_timeout":  30,
		"hls_initial_buffer_seconds": 10,
		"hls_max_initial_segments":   10,
		"hls_max_segments":           20,
		"hls_initial_segments":       3,
		"hls_window_size":            6,
	}
	for k, floor := range timeoutFloors {
		if v, ok := out[k]; ok && toIntNorm(v) == 0 {
			out[k] = floor
		}
	}
	floatFloors := map[string]float64{
		"no_data_check_interval":      1.0,
		"initial_data_check_interval": 0.2,
		"hls_segment_fetch_interval":  0.5,
	}
	for k, floor := range floatFloors {
		if v, ok := out[k]; ok && toFloatNorm(v) == 0 {
			out[k] = floor
		}
	}

	// Clamp max_streams_per_engine to [1, 20]
	if v, ok := out["max_streams_per_engine"]; ok {
		n := toIntNorm(v)
		if n < 1 {
			n = 1
		} else if n > 20 {
			n = 20
		}
		out["max_streams_per_engine"] = n
	}

	// Clamp proxy_prebuffer_seconds to [0, 30]
	if v, ok := out["proxy_prebuffer_seconds"]; ok {
		f := toFloatNorm(v)
		if f < 0 {
			f = 0
		} else if f > 30 {
			f = 30
		}
		out["proxy_prebuffer_seconds"] = f
	}

	return out
}

func normalizeVPNSettings(m map[string]any) map[string]any {
	out := deepCopyMap(m)

	// Legacy key migrations
	if _, ok := out["providers"]; ok {
		delete(out, "providers")
	}
	for _, stale := range []string{"vpn_mode", "container_name", "vpn_container_name"} {
		delete(out, stale)
	}

	// Boolean fields
	for _, k := range []string{
		"enabled", "dynamic_vpn_management", "restart_engines_on_reconnect",
		"vpn_servers_auto_refresh",
	} {
		if v, ok := out[k]; ok {
			out[k] = toBoolNorm(v)
		}
	}

	// Integer fields
	for _, k := range []string{
		"preferred_engines_per_vpn", "api_port",
		"unhealthy_restart_timeout_s", "port_cache_ttl_s",
		"health_check_interval_s", "vpn_servers_refresh_period_s",
		"wireguard_mtu",
	} {
		if v, ok := out[k]; ok {
			out[k] = toIntNorm(v)
		}
	}

	// Enum: protocol
	if v, ok := out["protocol"].(string); ok {
		if v != "wireguard" && v != "openvpn" {
			out["protocol"] = "wireguard"
		}
	}

	// Enum: vpn_servers_refresh_source
	if v, ok := out["vpn_servers_refresh_source"].(string); ok {
		valid := map[string]bool{"proton_paid": true, "gluetun_official": true}
		if !valid[v] {
			out["vpn_servers_refresh_source"] = "gluetun_official"
		}
	}

	// Enum: vpn_servers_gluetun_json_mode
	if v, ok := out["vpn_servers_gluetun_json_mode"].(string); ok {
		if v != "update" && v != "replace" {
			out["vpn_servers_gluetun_json_mode"] = "update"
		}
	}

	// Enum: vpn_servers_proton_credentials_source
	if v, ok := out["vpn_servers_proton_credentials_source"].(string); ok {
		if v != "env" && v != "settings" {
			out["vpn_servers_proton_credentials_source"] = "env"
		}
	}

	// Enum: filter fields (exclude/include/only)
	for _, k := range []string{
		"vpn_servers_filter_ipv6", "vpn_servers_filter_secure_core",
		"vpn_servers_filter_tor", "vpn_servers_filter_free_tier",
	} {
		if v, ok := out[k].(string); ok {
			if v != "exclude" && v != "include" && v != "only" {
				out[k] = "include"
			}
		}
	}

	return out
}

// toIntNorm coerces a JSON-decoded numeric value to int.
func toIntNorm(v any) int {
	switch n := v.(type) {
	case int:
		return n
	case int64:
		return int(n)
	case float64:
		return int(n)
	case float32:
		return int(n)
	}
	return 0
}

// toFloatNorm coerces a JSON-decoded numeric value to float64.
func toFloatNorm(v any) float64 {
	switch n := v.(type) {
	case float64:
		return n
	case float32:
		return float64(n)
	case int:
		return float64(n)
	case int64:
		return float64(n)
	}
	return 0
}

func isZeroNumeric(v any) bool {
	switch n := v.(type) {
	case float64:
		return n == 0
	case float32:
		return n == 0
	case int:
		return n == 0
	case int64:
		return n == 0
	}
	return false
}

func isPositiveNumeric(v any) bool {
	switch n := v.(type) {
	case float64:
		return n > 0
	case float32:
		return n > 0
	case int:
		return n > 0
	case int64:
		return n > 0
	}
	return false
}

// toBoolNorm coerces common truthy representations to bool.
func toBoolNorm(v any) bool {
	switch b := v.(type) {
	case bool:
		return b
	case int:
		return b != 0
	case float64:
		return b != 0
	case string:
		return b == "true" || b == "1" || b == "yes"
	}
	return false
}
