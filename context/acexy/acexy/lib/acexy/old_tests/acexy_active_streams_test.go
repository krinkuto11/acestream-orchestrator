// Acexy - Copyright (C) 2024 - Javinator9889 <dev at javinator9889 dot com>
// This program comes with ABSOLUTELY NO WARRANTY; for details type `show w'.
// This is free software, and you are welcome to redistribute it
// under certain conditions; type `show c' for details.
package acexy

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"net/url"
	"testing"
	"time"
)

// TestGetActiveStreams verifies that GetActiveStreams returns correct information
// about all currently active streams
func TestGetActiveStreams(t *testing.T) {
	// Create a mock acestream engine
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path == "/ace/getstream" {
			w.Header().Set("Content-Type", "application/json")
			streamID := r.URL.Query().Get("id")
			w.Write([]byte(fmt.Sprintf(`{
				"response": {
					"playback_url": "http://localhost:9999/stream/%s",
					"stat_url": "http://localhost:6878/ace/stat/%s/playback123",
					"command_url": "http://localhost:6878/ace/cmd/%s/playback123"
				}
			}`, streamID, streamID, streamID)))
			return
		}
	}))
	defer engine.Close()

	// Parse engine URL
	engineURL, _ := url.Parse(engine.URL)

	// Create an Acexy instance
	acexy := &Acexy{
		Scheme:            "http",
		Host:              engineURL.Hostname(),
		Port:              mustParsePort(t, engineURL.Port()),
		Endpoint:          MPEG_TS_ENDPOINT,
		EmptyTimeout:      10 * time.Second,
		BufferSize:        1024,
		NoResponseTimeout: 5 * time.Second,
	}
	acexy.Init()

	// Initially, there should be no active streams
	streams := acexy.GetActiveStreams()
	if len(streams) != 0 {
		t.Errorf("Expected 0 streams initially, got %d", len(streams))
	}

	// Create first stream
	aceId1, _ := NewAceID("stream1", "")
	stream1, err := acexy.FetchStream(aceId1, nil)
	if err != nil {
		t.Fatalf("Failed to fetch first stream: %v", err)
	}

	// Check active streams after first stream
	streams = acexy.GetActiveStreams()
	if len(streams) != 1 {
		t.Errorf("Expected 1 stream after first fetch, got %d", len(streams))
	}

	// Verify stream information
	if len(streams) > 0 {
		info := streams[0]
		if info.PlaybackURL != stream1.PlaybackURL {
			t.Errorf("Expected playback URL %s, got %s", stream1.PlaybackURL, info.PlaybackURL)
		}
		if info.StatURL != stream1.StatURL {
			t.Errorf("Expected stat URL %s, got %s", stream1.StatURL, info.StatURL)
		}
		if info.CommandURL != stream1.CommandURL {
			t.Errorf("Expected command URL %s, got %s", stream1.CommandURL, info.CommandURL)
		}
		if info.Clients != 0 {
			t.Errorf("Expected 0 clients, got %d", info.Clients)
		}
		if info.HasPlayer {
			t.Errorf("Expected no player, but HasPlayer is true")
		}
		if info.CreatedAt.IsZero() {
			t.Error("Expected CreatedAt to be set")
		}
	}

	// Create second stream
	aceId2, _ := NewAceID("stream2", "")
	_, err = acexy.FetchStream(aceId2, nil)
	if err != nil {
		t.Fatalf("Failed to fetch second stream: %v", err)
	}

	// Check active streams after second stream
	streams = acexy.GetActiveStreams()
	if len(streams) != 2 {
		t.Errorf("Expected 2 streams after second fetch, got %d", len(streams))
	}

	t.Logf("Successfully tracked %d active streams", len(streams))
}

// TestGetActiveStreamsEmpty verifies that GetActiveStreams returns empty when no streams
func TestGetActiveStreamsEmpty(t *testing.T) {
	acexy := &Acexy{
		Scheme:            "http",
		Host:              "localhost",
		Port:              6878,
		Endpoint:          MPEG_TS_ENDPOINT,
		EmptyTimeout:      10 * time.Second,
		BufferSize:        1024,
		NoResponseTimeout: 5 * time.Second,
	}
	acexy.Init()

	streams := acexy.GetActiveStreams()
	if len(streams) != 0 {
		t.Errorf("Expected 0 streams, got %d", len(streams))
	}
}

func mustParsePort(t *testing.T, port string) int {
	var p int
	_, err := fmt.Sscanf(port, "%d", &p)
	if err != nil {
		t.Fatalf("Failed to parse port %s: %v", port, err)
	}
	return p
}
