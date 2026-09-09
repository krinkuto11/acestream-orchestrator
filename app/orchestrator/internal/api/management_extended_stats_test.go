package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strconv"
	"strings"
	"testing"

	"github.com/acestream/acestream/internal/state"
)

// splitHostPort parses an httptest server URL into host and port parts.
func splitTestServerHostPort(t *testing.T, rawURL string) (string, int) {
	t.Helper()
	u, err := url.Parse(rawURL)
	if err != nil {
		t.Fatalf("parse test server URL: %v", err)
	}
	host := u.Hostname()
	port, err := strconv.Atoi(u.Port())
	if err != nil {
		t.Fatalf("parse test server port: %v", err)
	}
	return host, port
}

func newExtendedStatsTestServer(t *testing.T, st *state.Store, engineID, contentID, title string) *ProxyServer {
	t.Helper()
	srv := &ProxyServer{st: st, mux: http.NewServeMux()}
	st.OnStreamStarted(state.StreamStartedEvent{
		ContentID:  contentID,
		EngineID:   engineID,
		EngineName: engineID,
	})
	t.Cleanup(func() {
		st.OnStreamEnded(state.StreamEndedEvent{ContentID: contentID})
		st.RemoveEngine(engineID)
	})
	_ = title
	return srv
}

func doExtendedStats(t *testing.T, srv *ProxyServer, streamID string) *httptest.ResponseRecorder {
	t.Helper()
	r := httptest.NewRequest(http.MethodGet, "/api/v1/streams/"+streamID+"/extended-stats", nil)
	r.SetPathValue("id", streamID)
	w := httptest.NewRecorder()
	srv.mgHandleStreamExtendedStats(w, r)
	return w
}

func TestExtendedStats_IncludesEngineTitle(t *testing.T) {
	var gotQuery string
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotQuery = r.URL.Query().Get("query")
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"result":{"title":"Test Channel"}}`))
	}))
	defer engine.Close()
	host, port := splitTestServerHostPort(t, engine.URL)

	engineID := "test-engine-title-ok"
	contentID := "test-content-title-ok"
	st := state.Global
	st.AddEngine(&state.Engine{ContainerID: engineID, ContainerName: engineID, Host: host, Port: port})
	srv := newExtendedStatsTestServer(t, st, engineID, contentID, "")

	w := doExtendedStats(t, srv, contentID)
	if w.Code != http.StatusOK {
		t.Fatalf("status: got %d, want 200", w.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if body["title"] != "Test Channel" {
		t.Fatalf("title: got %v, want %q", body["title"], "Test Channel")
	}
	if gotQuery != contentID {
		t.Fatalf("engine query: got %q, want %q", gotQuery, contentID)
	}
	// Existing fields must keep working.
	if body["available"] != true || body["stream_id"] != contentID {
		t.Fatalf("existing fields changed: %v", body)
	}
}

func TestExtendedStats_EngineDown_OmitsTitleWithoutError(t *testing.T) {
	engineID := "test-engine-title-down"
	contentID := "test-content-title-down"
	st := state.Global
	// Engine points at a closed port so analyze_content fails fast.
	st.AddEngine(&state.Engine{ContainerID: engineID, ContainerName: engineID, Host: "127.0.0.1", Port: 1})
	srv := newExtendedStatsTestServer(t, st, engineID, contentID, "")

	w := doExtendedStats(t, srv, contentID)
	if w.Code != http.StatusOK {
		t.Fatalf("status: got %d, want 200", w.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if _, present := body["title"]; present {
		t.Fatalf("title must be omitted when engine is unreachable, got %v", body["title"])
	}
}

func TestExtendedStats_UnknownStream_404(t *testing.T) {
	srv := &ProxyServer{st: state.Global, mux: http.NewServeMux()}
	w := doExtendedStats(t, srv, "test-content-title-missing")
	if w.Code != http.StatusNotFound {
		t.Fatalf("status: got %d, want 404", w.Code)
	}
}

func TestParseAnalyzeContentTitle(t *testing.T) {
	cases := []struct {
		name string
		body string
		want string
	}{
		{"nested result", `{"result":{"title":"Nested Channel"}}`, "Nested Channel"},
		{"top level", `{"title":"Top Channel"}`, "Top Channel"},
		{"top level wins", `{"title":"Top","result":{"title":"Nested"}}`, "Top"},
		{"missing", `{"result":{}}`, ""},
		{"no result", `{"error":null}`, ""},
		{"invalid json", `not json`, ""},
		{"blank title", `{"result":{"title":"  "}}`, ""},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			if got := parseAnalyzeContentTitle([]byte(tc.body)); got != tc.want {
				t.Fatalf("got %q, want %q", got, tc.want)
			}
		})
	}
}

func TestExtendedStats_TitleShapeTolerance(t *testing.T) {
	engine := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"title":"Plain Title"}`))
	}))
	defer engine.Close()
	host, port := splitTestServerHostPort(t, engine.URL)

	engineID := "test-engine-title-plain"
	contentID := "test-content-title-plain"
	st := state.Global
	st.AddEngine(&state.Engine{ContainerID: engineID, ContainerName: engineID, Host: host, Port: port})
	srv := newExtendedStatsTestServer(t, st, engineID, contentID, "")

	w := doExtendedStats(t, srv, contentID)
	var body map[string]any
	if err := json.Unmarshal(w.Body.Bytes(), &body); err != nil {
		t.Fatalf("decode response: %v", err)
	}
	if body["title"] != "Plain Title" {
		t.Fatalf("title: got %v, want %q", body["title"], "Plain Title")
	}
	if !strings.Contains(w.Body.String(), `"title"`) {
		t.Fatal("response must contain title key")
	}
}
