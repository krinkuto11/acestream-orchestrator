package monitor

import (
	"testing"
)

// ── isStuck ───────────────────────────────────────────────────────────────────

func makeSample(pos, lastTS, downloaded int64) StatusSample {
	return StatusSample{
		LivePos:    &LivePosData{Pos: pos, LastTS: lastTS},
		Downloaded: int(downloaded),
	}
}

func TestIsStuck_InsufficientSamples_NotStuck(t *testing.T) {
	samples := []StatusSample{makeSample(100, 1000, 500)}
	if isStuck(samples, 1.0) {
		t.Fatal("single sample must never be stuck")
	}
}

func TestIsStuck_MovingPos_NotStuck(t *testing.T) {
	var samples []StatusSample
	for i := 0; i < 25; i++ {
		samples = append(samples, makeSample(int64(i*100), int64(i*1000), int64(i*500)))
	}
	if isStuck(samples, 1.0) {
		t.Fatal("advancing position must not be stuck")
	}
}

func TestIsStuck_StaticPosAndTS_Stuck(t *testing.T) {
	var samples []StatusSample
	// 25 samples all with same pos/ts and no download growth
	for i := 0; i < 25; i++ {
		samples = append(samples, makeSample(100, 1000, 1000))
	}
	if !isStuck(samples, 1.0) {
		t.Fatal("static pos, ts, and no download growth must be stuck")
	}
}

func TestIsStuck_StaticPos_ButDownloadGrowing_NotStuck(t *testing.T) {
	var samples []StatusSample
	for i := 0; i < 25; i++ {
		// pos/ts static, but download increases
		samples = append(samples, makeSample(100, 1000, int64(i*1000)))
	}
	if isStuck(samples, 1.0) {
		t.Fatal("static pos but growing download must not be stuck")
	}
}

func TestIsStuck_NoLivePos_Stuck(t *testing.T) {
	var samples []StatusSample
	for i := 0; i < 25; i++ {
		samples = append(samples, StatusSample{LivePos: nil, Downloaded: 100})
	}
	if !isStuck(samples, 1.0) {
		t.Fatal("all nil LivePos must be stuck")
	}
}

func TestIsStuck_WindowBasedOnInterval(t *testing.T) {
	// With intervalS=2.0, threshold=20s → need 11 samples
	// Provide only 8 samples with static pos → not enough window → not stuck
	var samples []StatusSample
	for i := 0; i < 8; i++ {
		samples = append(samples, makeSample(100, 1000, 1000))
	}
	if isStuck(samples, 2.0) {
		t.Fatal("too few samples for window must not be stuck")
	}
}

// ── buildLiveposMovement ──────────────────────────────────────────────────────

func TestBuildLiveposMovement_Empty(t *testing.T) {
	m := buildLiveposMovement(nil)
	if m["is_moving"] != false {
		t.Error("empty samples must report is_moving=false")
	}
	if m["direction"] != "unknown" {
		t.Error("empty samples must report direction=unknown")
	}
}

func TestBuildLiveposMovement_ForwardMovement(t *testing.T) {
	samples := []StatusSample{
		makeSample(100, 1000, 0),
		makeSample(200, 2000, 0),
		makeSample(300, 3000, 0),
	}
	m := buildLiveposMovement(samples)
	if m["is_moving"] != true {
		t.Error("advancing pos must report is_moving=true")
	}
	if m["direction"] != "forward" {
		t.Errorf("advancing pos must report direction=forward, got %v", m["direction"])
	}
}

func TestBuildLiveposMovement_Static(t *testing.T) {
	samples := []StatusSample{
		makeSample(100, 1000, 0),
		makeSample(100, 1000, 0),
		makeSample(100, 1000, 0),
	}
	m := buildLiveposMovement(samples)
	if m["is_moving"] != false {
		t.Error("static pos must report is_moving=false")
	}
	if m["direction"] != "stable" {
		t.Errorf("static pos must report direction=stable, got %v", m["direction"])
	}
}

func TestBuildLiveposMovement_SamplePoints(t *testing.T) {
	samples := []StatusSample{
		makeSample(100, 1000, 0),
		makeSample(200, 2000, 0),
	}
	m := buildLiveposMovement(samples)
	if m["sample_points"] != len(samples) {
		t.Errorf("sample_points=%v, want %d", m["sample_points"], len(samples))
	}
}

func TestBuildLiveposMovement_NilLivePos(t *testing.T) {
	samples := []StatusSample{
		{LivePos: nil, Downloaded: 0},
		{LivePos: nil, Downloaded: 100},
	}
	// Must not panic
	m := buildLiveposMovement(samples)
	if m == nil {
		t.Fatal("must return a non-nil map even with nil LivePos entries")
	}
}
