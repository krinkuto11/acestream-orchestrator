package engine

import (
	"fmt"
	"testing"
	"time"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/state"
)

// testConfig returns a config.Config with safe defaults for logic tests.
func testConfig() *config.Config {
	cfg := config.C.Load()
	cp := *cfg
	cp.MinReplicas = 1
	cp.MaxReplicas = 10
	cp.MinFreeReplicas = 1
	cp.MaxStreamsPerEngine = 3
	cp.ManualMode = false
	cp.GracePeriod = time.Second // short grace for canStopEngine tests
	return &cp
}

// swapConfig stores cfg and returns a restore function.
func swapConfig(t *testing.T, cfg *config.Config) {
	t.Helper()
	orig := config.C.Load()
	config.C.Store(cfg)
	t.Cleanup(func() { config.C.Store(orig) })
}

// addTestEngine registers an engine in state.Global and schedules removal.
func addTestEngine(t *testing.T, id, name, vpn string, streams int, health state.HealthStatus) *state.Engine {
	t.Helper()
	e := &state.Engine{
		ContainerID:   id,
		ContainerName: name,
		HealthStatus:  health,
		VPNContainer:  vpn,
	}
	state.Global.AddEngine(e)
	if streams > 0 {
		state.Global.SetStreamCount(id, streams)
	}
	t.Cleanup(func() {
		state.Global.RemoveEngine(id)
		state.Global.ResetLookaheadLayer()
	})
	return e
}

// ── computeDesiredReplicas ────────────────────────────────────────────────────

func TestComputeDesiredReplicas_BasicBuffer(t *testing.T) {
	swapConfig(t, testConfig()) // MinFreeReplicas=1, MinReplicas=1, MaxReplicas=10
	state.Global.ResetLookaheadLayer()

	// 2 running, 1 free (1 occupied) → desired = occupied(1) + buffer(1) = 2
	streamCounts := map[string]int{"a": 1, "b": 0}
	engines := []*state.Engine{{ContainerID: "a"}, {ContainerID: "b"}}
	got, _ := computeDesiredReplicas(2, 1, streamCounts, engines)
	if got != 2 {
		t.Errorf("got %d, want 2", got)
	}
}

func TestComputeDesiredReplicas_AllOccupied(t *testing.T) {
	cfg := testConfig()
	cfg.MinFreeReplicas = 2
	swapConfig(t, cfg)
	state.Global.ResetLookaheadLayer()

	// 3 running, 0 free → desired = 3 + 2 = 5
	streamCounts := map[string]int{"a": 1, "b": 1, "c": 1}
	engines := []*state.Engine{{ContainerID: "a"}, {ContainerID: "b"}, {ContainerID: "c"}}
	got, _ := computeDesiredReplicas(3, 0, streamCounts, engines)
	if got != 5 {
		t.Errorf("got %d, want 5", got)
	}
}

func TestComputeDesiredReplicas_ClampsToMinReplicas(t *testing.T) {
	cfg := testConfig()
	cfg.MinReplicas = 3
	cfg.MinFreeReplicas = 1
	swapConfig(t, cfg)
	state.Global.ResetLookaheadLayer()

	// 0 running, 0 free → desired = 0+1 = 1, clamped to MinReplicas=3
	got, _ := computeDesiredReplicas(0, 0, map[string]int{}, []*state.Engine{})
	if got != 3 {
		t.Errorf("got %d, want 3", got)
	}
}

func TestComputeDesiredReplicas_ClampsToMaxReplicas(t *testing.T) {
	cfg := testConfig()
	cfg.MaxReplicas = 4
	cfg.MinFreeReplicas = 10 // would push to 12 without cap
	swapConfig(t, cfg)
	state.Global.ResetLookaheadLayer()

	streamCounts := map[string]int{"a": 1, "b": 1}
	engines := []*state.Engine{{ContainerID: "a"}, {ContainerID: "b"}}
	got, _ := computeDesiredReplicas(2, 0, streamCounts, engines)
	if got != 4 {
		t.Errorf("got %d, want 4 (MaxReplicas)", got)
	}
}

func TestComputeDesiredReplicas_LookaheadTriggered(t *testing.T) {
	cfg := testConfig()
	cfg.MaxStreamsPerEngine = 3 // threshold = 2
	cfg.MinFreeReplicas = 0
	cfg.MinReplicas = 0
	cfg.MaxReplicas = 10
	swapConfig(t, cfg)
	state.Global.ResetLookaheadLayer()

	// Both engines are at threshold (2 streams each) → burst of min(2,2)=2 added
	streamCounts := map[string]int{"a": 2, "b": 2}
	engines := []*state.Engine{{ContainerID: "a"}, {ContainerID: "b"}}
	got, desc := computeDesiredReplicas(2, 0, streamCounts, engines)
	if got < 4 { // 2 running + 2 burst
		t.Errorf("got %d, want >= 4; desc=%s", got, desc)
	}
	if got > 4 { // burst capped at 2
		t.Errorf("got %d, want <= 4 (burst cap=2)", got)
	}
}

func TestComputeDesiredReplicas_NoLookaheadBelowThreshold(t *testing.T) {
	cfg := testConfig()
	cfg.MaxStreamsPerEngine = 3 // threshold = 2
	cfg.MinFreeReplicas = 1
	cfg.MinReplicas = 0
	cfg.MaxReplicas = 10
	swapConfig(t, cfg)
	state.Global.ResetLookaheadLayer()

	// Engines at 1 stream each (below threshold 2) — no lookahead
	streamCounts := map[string]int{"a": 1, "b": 1}
	engines := []*state.Engine{{ContainerID: "a"}, {ContainerID: "b"}}
	got, _ := computeDesiredReplicas(2, 0, streamCounts, engines)
	// occupiedCount=2, MinFreeReplicas=1 → desired=3
	if got != 3 {
		t.Errorf("got %d, want 3 (no lookahead below threshold)", got)
	}
}

// ── selectTerminationCandidates ───────────────────────────────────────────────

func TestSelectTerminationCandidates_FewestStreamsFirst(t *testing.T) {
	cfg := testConfig()
	cfg.MinFreeReplicas = 0
	swapConfig(t, cfg)
	c := NewController(nil)

	e1 := addTestEngine(t, "tc-idle", "idle", "", 0, state.HealthHealthy)
	e2 := addTestEngine(t, "tc-busy", "busy", "", 2, state.HealthHealthy)
	// Mark idle engine's empty timestamp so canStopEngine passes
	state.Global.RecordEmpty(e1.ContainerID)
	time.Sleep(2 * time.Second) // let GracePeriod elapse

	candidates := c.selectTerminationCandidates([]*state.Engine{e1, e2}, 1)
	if len(candidates) != 1 {
		t.Fatalf("got %d candidates, want 1", len(candidates))
	}
	if candidates[0] != "tc-idle" {
		t.Errorf("selected %q, want tc-idle (fewest streams)", candidates[0])
	}
}

func TestSelectTerminationCandidates_LeaderProtectedWhenFollowerExists(t *testing.T) {
	cfg := testConfig()
	cfg.MinFreeReplicas = 0
	swapConfig(t, cfg)
	c := NewController(nil)

	// Leader on a VPN, plus a follower on the same VPN
	leader := addTestEngine(t, "tc-leader", "leader", "vpn-1", 0, state.HealthHealthy)
	follower := addTestEngine(t, "tc-follower", "follower", "vpn-1", 0, state.HealthHealthy)
	leader.Forwarded = true
	state.Global.RecordEmpty(leader.ContainerID)
	state.Global.RecordEmpty(follower.ContainerID)
	time.Sleep(2 * time.Second)

	candidates := c.selectTerminationCandidates([]*state.Engine{leader, follower}, 1)
	for _, cid := range candidates {
		if cid == "tc-leader" {
			t.Error("leader must not be selected while follower exists on same VPN")
		}
	}
}

func TestSelectTerminationCandidates_LeaderEligibleWhenAlone(t *testing.T) {
	cfg := testConfig()
	cfg.MinFreeReplicas = 0
	cfg.MinReplicas = 0
	swapConfig(t, cfg)
	c := NewController(nil)

	leader := addTestEngine(t, "tc-solo-leader", "solo", "vpn-2", 0, state.HealthHealthy)
	leader.Forwarded = true
	state.Global.RecordEmpty(leader.ContainerID)
	time.Sleep(2 * time.Second)

	candidates := c.selectTerminationCandidates([]*state.Engine{leader}, 1)
	if len(candidates) != 1 || candidates[0] != "tc-solo-leader" {
		t.Errorf("solo leader must be selectable; got %v", candidates)
	}
}

func TestSelectTerminationCandidates_CountRespected(t *testing.T) {
	cfg := testConfig()
	cfg.MinFreeReplicas = 0
	swapConfig(t, cfg)
	c := NewController(nil)

	e1 := addTestEngine(t, "tc-cnt-1", "cnt1", "", 0, state.HealthHealthy)
	e2 := addTestEngine(t, "tc-cnt-2", "cnt2", "", 0, state.HealthHealthy)
	e3 := addTestEngine(t, "tc-cnt-3", "cnt3", "", 0, state.HealthHealthy)
	state.Global.RecordEmpty(e1.ContainerID)
	state.Global.RecordEmpty(e2.ContainerID)
	state.Global.RecordEmpty(e3.ContainerID)
	time.Sleep(2 * time.Second)

	candidates := c.selectTerminationCandidates([]*state.Engine{e1, e2, e3}, 2)
	if len(candidates) != 2 {
		t.Errorf("got %d candidates, want exactly 2", len(candidates))
	}
}

// ── canStopEngine ─────────────────────────────────────────────────────────────

func TestCanStopEngine_NonZeroLoad_ReturnsFalse(t *testing.T) {
	swapConfig(t, testConfig())
	addTestEngine(t, "cs-busy", "busy", "", 2, state.HealthHealthy)
	if canStopEngine("cs-busy", false) {
		t.Error("canStopEngine must return false when engine has active streams")
	}
}

func TestCanStopEngine_BelowMinReplicas_ReturnsFalse(t *testing.T) {
	cfg := testConfig()
	cfg.MinReplicas = 2
	cfg.GracePeriod = 0
	swapConfig(t, cfg)

	// Only 1 engine in the store; stopping it would leave 0 < MinReplicas=2
	addTestEngine(t, "cs-min", "only", "", 0, state.HealthHealthy)
	if canStopEngine("cs-min", true) { // bypassGrace=true to skip grace period check
		t.Error("canStopEngine must return false when stopping would break MinReplicas")
	}
}

func TestCanStopEngine_GracePeriodNotElapsed_ReturnsFalse(t *testing.T) {
	cfg := testConfig()
	cfg.GracePeriod = 10 * time.Minute // long grace
	cfg.MinReplicas = 0
	cfg.MinFreeReplicas = 0
	swapConfig(t, cfg)

	addTestEngine(t, "cs-grace", "grace", "", 0, state.HealthHealthy)
	// First call: records empty time
	canStopEngine("cs-grace", false)
	// Second call: grace not elapsed
	if canStopEngine("cs-grace", false) {
		t.Error("canStopEngine must return false when grace period has not elapsed")
	}
}

func TestCanStopEngine_BypassGrace_ReturnsTrue(t *testing.T) {
	cfg := testConfig()
	cfg.GracePeriod = 10 * time.Minute
	cfg.MinReplicas = 0
	cfg.MinFreeReplicas = 0
	swapConfig(t, cfg)

	addTestEngine(t, "cs-bypass", "bypass", "", 0, state.HealthHealthy)
	if !canStopEngine("cs-bypass", true) {
		t.Error("canStopEngine must return true when bypassGrace=true and load=0")
	}
}

// ── isTransientVPNError ───────────────────────────────────────────────────────

func TestIsTransientVPNError(t *testing.T) {
	cases := []struct {
		msg  string
		want bool
	}{
		{"", false},
		{"no healthy active dynamic vpn nodes available", true},
		{"cannot schedule acestream engine: no slot", true},
		{"control api not reachable", true},
		{"awaiting vpn node provisioning", true},
		{"docker daemon unavailable", false},
		{"out of memory", false},
	}
	for _, tc := range cases {
		var err error
		if tc.msg != "" {
			err = fmt.Errorf("%s", tc.msg)
		}
		got := isTransientVPNError(err)
		if got != tc.want {
			t.Errorf("isTransientVPNError(%q) = %v, want %v", tc.msg, got, tc.want)
		}
	}
}

// ── containsAny ───────────────────────────────────────────────────────────────

func TestContainsAny(t *testing.T) {
	cases := []struct {
		s    string
		subs []string
		want bool
	}{
		{"hello world", []string{"world"}, true},
		{"hello world", []string{"xyz", "world"}, true},
		{"hello world", []string{"xyz", "abc"}, false},
		{"", []string{"abc"}, false},
		{"abc", []string{}, false},
		{"abcdef", []string{"abcdef"}, true},
		{"abcdef", []string{"abcdefg"}, false}, // longer than string
	}
	for _, tc := range cases {
		got := containsAny(tc.s, tc.subs)
		if got != tc.want {
			t.Errorf("containsAny(%q, %v) = %v, want %v", tc.s, tc.subs, got, tc.want)
		}
	}
}
