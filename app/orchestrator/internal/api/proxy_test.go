package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/acestream/acestream/internal/config"
)

// ── requireAPIKey ─────────────────────────────────────────────────────────────

func setTestAPIKey(key string) {
	cfg := config.C.Load()
	cfg.APIKey = key
	config.C.Store(cfg)
}

func TestRequireAPIKey_NoKeyConfigured_Allows(t *testing.T) {
	setTestAPIKey("")
	called := false
	h := requireAPIKey(func(w http.ResponseWriter, r *http.Request) { called = true })

	r := httptest.NewRequest("GET", "/", nil)
	w := httptest.NewRecorder()
	h(w, r)

	if !called {
		t.Fatal("handler must be called when no API key is configured")
	}
}

func TestRequireAPIKey_MissingKey_Returns401(t *testing.T) {
	setTestAPIKey("secret")
	h := requireAPIKey(func(w http.ResponseWriter, r *http.Request) {
		t.Fatal("handler must not be called")
	})

	r := httptest.NewRequest("GET", "/", nil)
	w := httptest.NewRecorder()
	h(w, r)

	if w.Code != http.StatusUnauthorized {
		t.Fatalf("missing key: got %d, want 401", w.Code)
	}
}

func TestRequireAPIKey_WrongKey_Returns403(t *testing.T) {
	setTestAPIKey("secret")
	h := requireAPIKey(func(w http.ResponseWriter, r *http.Request) {
		t.Fatal("handler must not be called")
	})

	r := httptest.NewRequest("GET", "/", nil)
	r.Header.Set("X-API-Key", "wrong")
	w := httptest.NewRecorder()
	h(w, r)

	if w.Code != http.StatusForbidden {
		t.Fatalf("wrong key: got %d, want 403", w.Code)
	}
}

func TestRequireAPIKey_CorrectKey_XApiKeyHeader(t *testing.T) {
	setTestAPIKey("secret")
	called := false
	h := requireAPIKey(func(w http.ResponseWriter, r *http.Request) { called = true })

	r := httptest.NewRequest("GET", "/", nil)
	r.Header.Set("X-API-Key", "secret")
	w := httptest.NewRecorder()
	h(w, r)

	if !called || w.Code != http.StatusOK {
		t.Fatalf("correct X-API-Key header: called=%v code=%d", called, w.Code)
	}
}

func TestRequireAPIKey_CorrectKey_BearerToken(t *testing.T) {
	setTestAPIKey("secret")
	called := false
	h := requireAPIKey(func(w http.ResponseWriter, r *http.Request) { called = true })

	r := httptest.NewRequest("GET", "/", nil)
	r.Header.Set("Authorization", "Bearer secret")
	w := httptest.NewRecorder()
	h(w, r)

	if !called {
		t.Fatal("Bearer token must be accepted")
	}
}

func TestRequireAPIKey_CorrectKey_QueryParam(t *testing.T) {
	setTestAPIKey("secret")
	called := false
	h := requireAPIKey(func(w http.ResponseWriter, r *http.Request) { called = true })

	r := httptest.NewRequest("GET", "/?key=secret", nil)
	w := httptest.NewRecorder()
	h(w, r)

	if !called {
		t.Fatal("query param key must be accepted")
	}
}

// ── sanitizeID ────────────────────────────────────────────────────────────────

func TestSanitizeID(t *testing.T) {
	cases := []struct {
		in   string
		want string
	}{
		{"abc123", "abc123"},
		{"ABC123", "abc123"},                      // lowercased
		{"  abc  ", "abc"},                         // trimmed
		{`{abc}`, "abc"},                           // braces stripped
		{`"abc"`, "abc"},                           // quotes stripped
		{"abc.def-ghi_jkl", "abc.def-ghi_jkl"},    // allowed punctuation
		{"abc|def", "abc|def"},                     // pipe allowed
		{"abc def", "abc_def"},                     // space replaced
		{"a/b/c", "a_b_c"},                         // slash replaced
		{"", ""},                                   // empty stays empty
		{"   ", ""},                                // whitespace-only → empty
	}
	for _, tc := range cases {
		got := sanitizeID(tc.in)
		if got != tc.want {
			t.Errorf("sanitizeID(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}

// ── selectInput ───────────────────────────────────────────────────────────────

func TestSelectInput_SingleParam_OK(t *testing.T) {
	typ, val, err := selectInput("abc123", "", "", "", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if typ != "content_id" || val != "abc123" {
		t.Fatalf("got type=%q val=%q", typ, val)
	}
}

func TestSelectInput_NoParams_Error(t *testing.T) {
	_, _, err := selectInput("", "", "", "", "")
	if err == nil {
		t.Fatal("expected error for no params")
	}
}

func TestSelectInput_MultipleParams_Error(t *testing.T) {
	_, _, err := selectInput("abc", "def", "", "", "")
	if err == nil {
		t.Fatal("expected error for multiple params")
	}
}

func TestSelectInput_Infohash(t *testing.T) {
	typ, val, err := selectInput("", "abcdef1234", "", "", "")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if typ != "infohash" {
		t.Fatalf("expected type=infohash, got %q", typ)
	}
	_ = val
}

// ── buildStreamKey ────────────────────────────────────────────────────────────

func TestBuildStreamKey_ContentIDSimple(t *testing.T) {
	key := buildStreamKey("content_id", "abc123", "0", 0)
	if key != "abc123" {
		t.Fatalf("simple content_id key must equal the ID, got %q", key)
	}
}

func TestBuildStreamKey_ContentIDWithFileIndex_Hashed(t *testing.T) {
	key := buildStreamKey("content_id", "abc123", "1", 0)
	// Should be hashed, not the raw ID
	if key == "abc123" {
		t.Fatal("non-zero file_index must produce a hashed key")
	}
	if !strings.Contains(key, "content_id:") {
		t.Fatalf("hashed key must be prefixed with input type, got %q", key)
	}
}

func TestBuildStreamKey_Deterministic(t *testing.T) {
	k1 := buildStreamKey("torrent_url", "http://example.com/x.torrent", "0", 0)
	k2 := buildStreamKey("torrent_url", "http://example.com/x.torrent", "0", 0)
	if k1 != k2 {
		t.Fatal("buildStreamKey must be deterministic")
	}
}

func TestBuildStreamKey_DifferentInputsDifferentKeys(t *testing.T) {
	k1 := buildStreamKey("content_id", "aaa", "0", 0)
	k2 := buildStreamKey("content_id", "bbb", "0", 0)
	if k1 == k2 {
		t.Fatal("different inputs must produce different keys")
	}
}

// ── validateStreamKey ─────────────────────────────────────────────────────────

func TestValidateStreamKey(t *testing.T) {
	cases := []struct {
		in   string
		want string
	}{
		{"abc123", "abc123"},
		{"ABC:def-ghi", "abc:def-ghi"}, // lowercased, colon allowed
		{"abc def", "abcdef"},           // space stripped
		{"abc/def", "abcdef"},           // slash stripped
		{"", ""},
	}
	for _, tc := range cases {
		got := validateStreamKey(tc.in)
		if got != tc.want {
			t.Errorf("validateStreamKey(%q) = %q, want %q", tc.in, got, tc.want)
		}
	}
}
