package engine

import (
	"encoding/binary"
	"testing"
)

// ── buildLivePushPayload ──────────────────────────────────────────────────────

func TestBuildLivePushPayload_AllFields(t *testing.T) {
	cfg := map[string]any{
		"total_max_upload_rate":   float64(1024),
		"total_max_download_rate": float64(2048),
		"buffer_time":             float64(10),
		"live_cache_type":         "disk",
	}
	payload := buildLivePushPayload(cfg)

	if payload["upload_limit"] != 1024 {
		t.Errorf("upload_limit = %v, want 1024", payload["upload_limit"])
	}
	if payload["download_limit"] != 2048 {
		t.Errorf("download_limit = %v, want 2048", payload["download_limit"])
	}
	if payload["live_buffer"] != 10 {
		t.Errorf("live_buffer = %v, want 10", payload["live_buffer"])
	}
	if payload["live_cache_type"] != "disk" {
		t.Errorf("live_cache_type = %v, want 'disk'", payload["live_cache_type"])
	}
}

func TestBuildLivePushPayload_EmptyConfig(t *testing.T) {
	payload := buildLivePushPayload(map[string]any{})
	if len(payload) != 0 {
		t.Errorf("empty cfg must produce empty payload, got %v", payload)
	}
}

func TestBuildLivePushPayload_RestartRequiredFieldsNotIncluded(t *testing.T) {
	cfg := map[string]any{
		"memory_limit":           "512m",
		"torrent_folder_host_path": "/data",
		"disk_cache_prune_interval": float64(1440),
	}
	payload := buildLivePushPayload(cfg)
	for k := range payload {
		t.Errorf("restart-required field %q must not appear in live push payload", k)
	}
}

func TestBuildLivePushPayload_NonStringCacheType_Skipped(t *testing.T) {
	cfg := map[string]any{"live_cache_type": 42} // wrong type
	payload := buildLivePushPayload(cfg)
	if _, ok := payload["live_cache_type"]; ok {
		t.Error("non-string live_cache_type must be skipped")
	}
}

// ── anyToInt ──────────────────────────────────────────────────────────────────

func TestAnyToInt(t *testing.T) {
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
		if got := anyToInt(tc.in); got != tc.want {
			t.Errorf("anyToInt(%v) = %d, want %d", tc.in, got, tc.want)
		}
	}
}

// ── stripDockerMux ────────────────────────────────────────────────────────────

// buildMuxFrame creates a Docker stream-multiplexed frame (stream type 1 = stdout).
func buildMuxFrame(data []byte) []byte {
	frame := make([]byte, 8+len(data))
	frame[0] = 1 // stdout
	binary.BigEndian.PutUint32(frame[4:], uint32(len(data)))
	copy(frame[8:], data)
	return frame
}

func TestStripDockerMux_SingleFrame(t *testing.T) {
	payload := []byte(`{"access_token":"tok123"}`)
	frame := buildMuxFrame(payload)
	got := stripDockerMux(frame)
	if string(got) != string(payload) {
		t.Errorf("got %q, want %q", got, payload)
	}
}

func TestStripDockerMux_MultipleFrames(t *testing.T) {
	part1 := []byte(`{"access_`)
	part2 := []byte(`token":"tok"}`)
	frame := append(buildMuxFrame(part1), buildMuxFrame(part2)...)
	got := stripDockerMux(frame)
	want := `{"access_token":"tok"}`
	if string(got) != want {
		t.Errorf("got %q, want %q", got, want)
	}
}

func TestStripDockerMux_ShortInput_FallsBack(t *testing.T) {
	// Fewer than 8 bytes — no complete frame header, fallback returns raw bytes.
	raw := []byte(`{"ok"}`)
	got := stripDockerMux(raw)
	if string(got) != string(raw) {
		t.Errorf("short fallback: got %q, want %q", got, raw)
	}
}

func TestStripDockerMux_EmptyInput(t *testing.T) {
	got := stripDockerMux([]byte{})
	if len(got) != 0 {
		t.Errorf("empty input must return empty, got %q", got)
	}
}

func TestStripDockerMux_TruncatedFrame_Handled(t *testing.T) {
	// Frame header claims 100 bytes but only 4 bytes of data follow
	frame := buildMuxFrame([]byte("data"))
	// Corrupt: claim 100 bytes
	binary.BigEndian.PutUint32(frame[4:], 100)
	// Must not panic
	got := stripDockerMux(frame)
	if got == nil {
		t.Error("truncated frame must return non-nil")
	}
}
