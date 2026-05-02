package persistence

import (
	"testing"
)

// ── normalizeProxySettings ────────────────────────────────────────────────────

func TestNormalizeProxySettings_StreamModeEnum(t *testing.T) {
	cases := []struct{ in, want string }{
		{"TS", "TS"},
		{"HLS", "HLS"},
		{"ts", "TS"},   // lowercase is NOT coerced — the function stores as-is and only validates the stored string value
		{"bad", "TS"},  // invalid → default
		{"", "TS"},     // empty → default
	}
	for _, tc := range cases {
		m := map[string]any{"stream_mode": tc.in}
		out := normalizeProxySettings(m)
		got, _ := out["stream_mode"].(string)
		if got != tc.want {
			t.Errorf("stream_mode %q → got %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestNormalizeProxySettings_ControlModeEnum(t *testing.T) {
	cases := []struct{ in, want string }{
		{"http", "http"},
		{"api", "api"},
		{"invalid", "api"},
		{"", "api"},
	}
	for _, tc := range cases {
		m := map[string]any{"control_mode": tc.in}
		out := normalizeProxySettings(m)
		got, _ := out["control_mode"].(string)
		if got != tc.want {
			t.Errorf("control_mode %q → got %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestNormalizeProxySettings_LegacyApiPreflight(t *testing.T) {
	cases := []struct{ in, want string }{
		{"light", "light"},
		{"standard", "standard"},
		{"heavy", "heavy"},
		{"ultra", "light"},
		{"", "light"},
	}
	for _, tc := range cases {
		m := map[string]any{"legacy_api_preflight_tier": tc.in}
		out := normalizeProxySettings(m)
		got, _ := out["legacy_api_preflight_tier"].(string)
		if got != tc.want {
			t.Errorf("legacy_api_preflight_tier %q → got %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestNormalizeProxySettings_MaxStreamsClamp(t *testing.T) {
	cases := []struct {
		in   any
		want int
	}{
		{5, 5},
		{0, 2},   // floor replaces 0 with default 2
		{-3, 1},  // clamp min
		{99, 20}, // clamp max
		{float64(7), 7},
	}
	for _, tc := range cases {
		m := map[string]any{"max_streams_per_engine": tc.in}
		out := normalizeProxySettings(m)
		got := toIntNorm(out["max_streams_per_engine"])
		if got != tc.want {
			t.Errorf("max_streams_per_engine %v → got %d, want %d", tc.in, got, tc.want)
		}
	}
}

func TestNormalizeProxySettings_PrebufferClamp(t *testing.T) {
	cases := []struct {
		in   any
		want float64
	}{
		{float64(3), 3},
		{float64(-1), 0},   // clamp min
		{float64(100), 30}, // clamp max
		{int(5), 5},
	}
	for _, tc := range cases {
		m := map[string]any{"proxy_prebuffer_seconds": tc.in}
		out := normalizeProxySettings(m)
		got := toFloatNorm(out["proxy_prebuffer_seconds"])
		if got != tc.want {
			t.Errorf("proxy_prebuffer_seconds %v → got %v, want %v", tc.in, got, tc.want)
		}
	}
}

func TestNormalizeProxySettings_IntegerCoercion(t *testing.T) {
	m := map[string]any{
		"connection_timeout":     float64(30),
		"no_data_timeout_checks": float64(60),
	}
	out := normalizeProxySettings(m)
	if toIntNorm(out["connection_timeout"]) != 30 {
		t.Error("connection_timeout should be coerced to int 30")
	}
	if toIntNorm(out["no_data_timeout_checks"]) != 60 {
		t.Error("no_data_timeout_checks should be coerced to int 60")
	}
}

func TestNormalizeProxySettings_UnknownKeysPassThrough(t *testing.T) {
	m := map[string]any{"custom_key": "custom_value"}
	out := normalizeProxySettings(m)
	if out["custom_key"] != "custom_value" {
		t.Error("unknown keys must pass through unchanged")
	}
}

// ── normalizeVPNSettings ──────────────────────────────────────────────────────

func TestNormalizeVPNSettings_LegacyKeyRemoval(t *testing.T) {
	m := map[string]any{
		"providers":        []any{"prov1"},
		"vpn_mode":         "auto",
		"container_name":   "gluetun",
		"enabled":          true,
	}
	out := normalizeVPNSettings(m)
	for _, stale := range []string{"providers", "vpn_mode", "container_name"} {
		if _, ok := out[stale]; ok {
			t.Errorf("stale key %q must be removed by normalization", stale)
		}
	}
	if v, _ := out["enabled"].(bool); !v {
		t.Error("'enabled' must be preserved")
	}
}

func TestNormalizeVPNSettings_ProtocolEnum(t *testing.T) {
	cases := []struct{ in, want string }{
		{"wireguard", "wireguard"},
		{"openvpn", "openvpn"},
		{"ipsec", "wireguard"}, // invalid → default
		{"", "wireguard"},
	}
	for _, tc := range cases {
		m := map[string]any{"protocol": tc.in}
		out := normalizeVPNSettings(m)
		got, _ := out["protocol"].(string)
		if got != tc.want {
			t.Errorf("protocol %q → got %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestNormalizeVPNSettings_RefreshSourceEnum(t *testing.T) {
	cases := []struct{ in, want string }{
		{"proton_paid", "proton_paid"},
		{"gluetun_official", "gluetun_official"},
		{"other", "gluetun_official"},
	}
	for _, tc := range cases {
		m := map[string]any{"vpn_servers_refresh_source": tc.in}
		out := normalizeVPNSettings(m)
		got, _ := out["vpn_servers_refresh_source"].(string)
		if got != tc.want {
			t.Errorf("vpn_servers_refresh_source %q → got %q, want %q", tc.in, got, tc.want)
		}
	}
}

func TestNormalizeVPNSettings_FilterFields(t *testing.T) {
	filterKeys := []string{
		"vpn_servers_filter_ipv6",
		"vpn_servers_filter_secure_core",
		"vpn_servers_filter_tor",
		"vpn_servers_filter_free_tier",
	}
	for _, k := range filterKeys {
		for _, bad := range []string{"bad", "", "yes"} {
			m := map[string]any{k: bad}
			out := normalizeVPNSettings(m)
			got, _ := out[k].(string)
			if got != "include" {
				t.Errorf("%s %q → got %q, want 'include'", k, bad, got)
			}
		}
		for _, good := range []string{"exclude", "include", "only"} {
			m := map[string]any{k: good}
			out := normalizeVPNSettings(m)
			got, _ := out[k].(string)
			if got != good {
				t.Errorf("%s %q → got %q, want %q", k, good, got, good)
			}
		}
	}
}

func TestNormalizeVPNSettings_BoolCoercion(t *testing.T) {
	m := map[string]any{
		"enabled":               "true",
		"vpn_servers_auto_refresh": 1,
	}
	out := normalizeVPNSettings(m)
	if v, _ := out["enabled"].(bool); !v {
		t.Error("'enabled' string 'true' must coerce to bool true")
	}
	if v, _ := out["vpn_servers_auto_refresh"].(bool); !v {
		t.Error("'vpn_servers_auto_refresh' int 1 must coerce to bool true")
	}
}

func TestNormalizeVPNSettings_IntegerCoercion(t *testing.T) {
	m := map[string]any{
		"api_port":                  float64(8001),
		"preferred_engines_per_vpn": float64(10),
	}
	out := normalizeVPNSettings(m)
	if toIntNorm(out["api_port"]) != 8001 {
		t.Error("api_port must coerce to int 8001")
	}
	if toIntNorm(out["preferred_engines_per_vpn"]) != 10 {
		t.Error("preferred_engines_per_vpn must coerce to int 10")
	}
}

// ── Type helpers ──────────────────────────────────────────────────────────────

func TestToIntNorm(t *testing.T) {
	cases := []struct {
		in   any
		want int
	}{
		{int(5), 5},
		{int64(7), 7},
		{float64(3.9), 3},
		{float32(2.1), 2},
		{"str", 0},
		{nil, 0},
	}
	for _, tc := range cases {
		if got := toIntNorm(tc.in); got != tc.want {
			t.Errorf("toIntNorm(%v) = %d, want %d", tc.in, got, tc.want)
		}
	}
}

func TestToFloatNorm(t *testing.T) {
	cases := []struct {
		in   any
		want float64
	}{
		{float64(1.5), 1.5},
		{float32(2.0), 2.0},
		{int(3), 3.0},
		{int64(4), 4.0},
		{nil, 0},
	}
	for _, tc := range cases {
		if got := toFloatNorm(tc.in); got != tc.want {
			t.Errorf("toFloatNorm(%v) = %v, want %v", tc.in, got, tc.want)
		}
	}
}

func TestToBoolNorm(t *testing.T) {
	cases := []struct {
		in   any
		want bool
	}{
		{true, true},
		{false, false},
		{"true", true},
		{"yes", true},
		{"1", true},
		{"false", false},
		{1, true},
		{0, false},
		{float64(1), true},
		{nil, false},
	}
	for _, tc := range cases {
		if got := toBoolNorm(tc.in); got != tc.want {
			t.Errorf("toBoolNorm(%v) = %v, want %v", tc.in, got, tc.want)
		}
	}
}
