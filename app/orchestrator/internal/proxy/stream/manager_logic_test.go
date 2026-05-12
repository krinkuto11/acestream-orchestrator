package stream

import (
	"errors"
	"testing"
)

// ── classifyProbeOutcome ──────────────────────────────────────────────────────

func TestClassifyProbeOutcome(t *testing.T) {
	cases := []struct {
		err        error
		wantStatus string
	}{
		{nil, "success"},
		{errors.New("context deadline exceeded"), "timeout"},
		{errors.New("request timeout"), "timeout"},
		{errors.New("engine error: bad content"), "engine_error"},
		{errors.New("engine returned empty playback_url"), "engine_error"},
		{errors.New("ace_api auth failed"), "engine_error"},
		{errors.New("connection refused"), "vpn_error"},
		{errors.New("connection reset by peer"), "vpn_error"},
		{errors.New("no route to host"), "vpn_error"},
		{errors.New("connect: network is unreachable"), "vpn_error"},
		{errors.New("read error: unexpected eof"), "vpn_error"},
		{errors.New("something completely unexpected"), "engine_error"},
		{errors.New(""), "engine_error"}, // empty message after trim
	}

	for _, tc := range cases {
		errStr := "<nil>"
		if tc.err != nil {
			errStr = tc.err.Error()
		}
		gotStatus, _ := classifyProbeOutcome(tc.err)
		if gotStatus != tc.wantStatus {
			t.Errorf("classifyProbeOutcome(%q) status = %q, want %q", errStr, gotStatus, tc.wantStatus)
		}
	}
}

func TestClassifyProbeOutcome_NilReturnsEmptyReason(t *testing.T) {
	status, reason := classifyProbeOutcome(nil)
	if status != "success" {
		t.Errorf("got status %q, want success", status)
	}
	if reason != "" {
		t.Errorf("got reason %q, want empty", reason)
	}
}

// ── isTransientStreamError ────────────────────────────────────────────────────

func TestIsTransientStreamError(t *testing.T) {
	cases := []struct {
		err  error
		want bool
	}{
		{nil, false},
		{errors.New("connection refused"), true},
		{errors.New("connection reset by peer"), true},
		{errors.New("EOF"), true},
		{errors.New("i/o timeout"), true},
		{errors.New("context deadline exceeded"), true},
		{errors.New("no route to host"), true},
		{errors.New("engine error: invalid content"), false},
		{errors.New("parse error"), false},
		{errors.New("out of memory"), false},
	}
	for _, tc := range cases {
		errStr := "<nil>"
		if tc.err != nil {
			errStr = tc.err.Error()
		}
		got := isTransientStreamError(tc.err)
		if got != tc.want {
			t.Errorf("isTransientStreamError(%q) = %v, want %v", errStr, got, tc.want)
		}
	}
}
