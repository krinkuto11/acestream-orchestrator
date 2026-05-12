package circuitbreaker

import (
	"testing"
	"time"
)

func newTestManager() *Manager {
	return NewManager(3, 100*time.Millisecond, 2, 100*time.Millisecond)
}

// ── Breaker state machine ─────────────────────────────────────────────────────

func TestBreaker_ClosedByDefault(t *testing.T) {
	b := New(3, time.Second)
	if !b.CanExecute() {
		t.Fatal("new breaker must be closed (CanExecute=true)")
	}
	if b.IsOpen() {
		t.Fatal("new breaker must not be open")
	}
}

func TestBreaker_OpensAfterThreshold(t *testing.T) {
	b := New(3, time.Second)
	for i := 0; i < 3; i++ {
		b.RecordFailure()
	}
	if !b.IsOpen() {
		t.Fatal("breaker must open after threshold failures")
	}
	if b.CanExecute() {
		t.Fatal("open breaker must not allow execution")
	}
}

func TestBreaker_DoesNotOpenBeforeThreshold(t *testing.T) {
	b := New(3, time.Second)
	b.RecordFailure()
	b.RecordFailure()
	if b.IsOpen() {
		t.Fatal("breaker must stay closed below threshold")
	}
}

func TestBreaker_RecordSuccessResetsClosed(t *testing.T) {
	b := New(2, time.Second)
	b.RecordFailure()
	b.RecordFailure() // opens
	if !b.IsOpen() {
		t.Fatal("breaker should be open")
	}
	// Force closed via success (from half-open after timeout)
	b.mu.Lock()
	b.state = StateHalfOpen
	b.mu.Unlock()
	b.RecordSuccess()
	if b.IsOpen() {
		t.Fatal("breaker must close after success in half-open")
	}
}

func TestBreaker_HalfOpenAfterRecoveryTimeout(t *testing.T) {
	b := New(1, 50*time.Millisecond)
	b.RecordFailure()
	if !b.IsOpen() {
		t.Fatal("breaker should be open")
	}
	time.Sleep(60 * time.Millisecond)
	// CanExecute transitions Open→HalfOpen when timeout elapsed
	if !b.CanExecute() {
		t.Fatal("breaker must allow execution after recovery timeout (half-open)")
	}
	if b.IsOpen() {
		t.Fatal("breaker must not be open after transitioning to half-open")
	}
}

func TestBreaker_HalfOpenFailureReopens(t *testing.T) {
	b := New(1, 50*time.Millisecond)
	b.RecordFailure()
	time.Sleep(60 * time.Millisecond)
	b.CanExecute() // half-open
	b.RecordFailure()
	if !b.IsOpen() {
		t.Fatal("failure in half-open must reopen the breaker")
	}
}

func TestBreaker_ForceReset(t *testing.T) {
	b := New(1, time.Minute)
	b.RecordFailure()
	if !b.IsOpen() {
		t.Fatal("breaker should be open")
	}
	b.ForceReset()
	if b.IsOpen() {
		t.Fatal("breaker must be closed after ForceReset")
	}
	if !b.CanExecute() {
		t.Fatal("breaker must allow execution after ForceReset")
	}
}

// ── Manager ───────────────────────────────────────────────────────────────────

func TestManager_CanProvision_DefaultClosed(t *testing.T) {
	m := newTestManager()
	if !m.CanProvision("general") {
		t.Fatal("manager must allow provisioning when closed")
	}
}

func TestManager_UnknownOpFallsBackToGeneral(t *testing.T) {
	m := newTestManager()
	// Exhaust the general breaker via the unknown op name
	for i := 0; i < 3; i++ {
		m.RecordFailure("unknown_op")
	}
	// The general breaker should be open
	if m.CanProvision("general") {
		t.Fatal("unknown op must fall back to general breaker")
	}
}

func TestManager_ForceResetAll(t *testing.T) {
	m := newTestManager()
	for i := 0; i < 3; i++ {
		m.RecordFailure("general")
	}
	for i := 0; i < 2; i++ {
		m.RecordFailure("replacement")
	}
	m.ForceReset("")
	if !m.CanProvision("general") {
		t.Fatal("general must be closed after ForceReset all")
	}
	if !m.CanProvision("replacement") {
		t.Fatal("replacement must be closed after ForceReset all")
	}
}

func TestManager_GetStatus_ContainsExpectedKeys(t *testing.T) {
	m := newTestManager()
	status := m.GetStatus()
	if _, ok := status["general"]; !ok {
		t.Error("GetStatus must contain 'general' key")
	}
	if _, ok := status["replacement"]; !ok {
		t.Error("GetStatus must contain 'replacement' key")
	}
}

func TestManager_RecordSuccess_DoesNotPanic(t *testing.T) {
	m := newTestManager()
	// Should not panic even without prior failures
	m.RecordSuccess("general")
	m.RecordSuccess("replacement")
	m.RecordSuccess("nonexistent")
}
