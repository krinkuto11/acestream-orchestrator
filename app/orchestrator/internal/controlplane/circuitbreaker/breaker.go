package circuitbreaker

import (
	"log/slog"
	"sync"
	"time"

	"github.com/acestream/acestream/internal/metrics"
)

type State int

const (
	StateClosed   State = iota // Normal operation
	StateOpen                  // Blocking
	StateHalfOpen              // Testing recovery
)

func (s State) String() string {
	switch s {
	case StateClosed:
		return "closed"
	case StateOpen:
		return "open"
	case StateHalfOpen:
		return "half_open"
	default:
		return "unknown"
	}
}

// Breaker is a single circuit breaker instance.
type Breaker struct {
	mu               sync.Mutex
	state            State
	failureCount     int
	failureThreshold int
	recoveryTimeout  time.Duration
	lastFailureTime  time.Time
	lastSuccessTime  time.Time
}

func New(failureThreshold int, recoveryTimeout time.Duration) *Breaker {
	return &Breaker{
		state:            StateClosed,
		failureThreshold: failureThreshold,
		recoveryTimeout:  recoveryTimeout,
	}
}

func (b *Breaker) CanExecute() bool {
	b.mu.Lock()
	defer b.mu.Unlock()

	switch b.state {
	case StateClosed:
		return true
	case StateOpen:
		if !b.lastFailureTime.IsZero() && time.Since(b.lastFailureTime) > b.recoveryTimeout {
			b.state = StateHalfOpen
			slog.Info("circuit breaker moving to half_open")
			return true
		}
		return false
	case StateHalfOpen:
		return true
	}
	return false
}

func (b *Breaker) RecordSuccess() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.failureCount = 0
	b.lastSuccessTime = time.Now()
	if b.state != StateClosed {
		slog.Info("circuit breaker CLOSED - operations restored")
		b.state = StateClosed
	}
}

func (b *Breaker) RecordFailure() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.failureCount++
	b.lastFailureTime = time.Now()

	if b.state == StateHalfOpen {
		slog.Warn("circuit breaker back to OPEN after recovery failure")
		b.state = StateOpen
		return
	}
	if b.state == StateClosed && b.failureCount >= b.failureThreshold {
		slog.Warn("circuit breaker OPENED", "failures", b.failureCount, "threshold", b.failureThreshold)
		b.state = StateOpen
	}
}

func (b *Breaker) ForceReset() {
	b.mu.Lock()
	defer b.mu.Unlock()
	b.state = StateClosed
	b.failureCount = 0
}

func (b *Breaker) IsOpen() bool {
	b.mu.Lock()
	defer b.mu.Unlock()
	return b.state == StateOpen
}

func (b *Breaker) Status() map[string]any {
	b.mu.Lock()
	defer b.mu.Unlock()
	var lastFailure, lastSuccess string
	if !b.lastFailureTime.IsZero() {
		lastFailure = b.lastFailureTime.Format(time.RFC3339)
	}
	if !b.lastSuccessTime.IsZero() {
		lastSuccess = b.lastSuccessTime.Format(time.RFC3339)
	}
	return map[string]any{
		"state":             b.state.String(),
		"failure_count":     b.failureCount,
		"failure_threshold": b.failureThreshold,
		"recovery_timeout":  b.recoveryTimeout.Seconds(),
		"last_failure_time": lastFailure,
		"last_success_time": lastSuccess,
	}
}

// Manager holds named circuit breakers for different operation types.
type Manager struct {
	mu       sync.RWMutex
	breakers map[string]*Breaker
}

func NewManager(generalThreshold int, generalRecovery time.Duration, replThreshold int, replRecovery time.Duration) *Manager {
	m := &Manager{breakers: make(map[string]*Breaker)}
	m.breakers["general"] = New(generalThreshold, generalRecovery)
	m.breakers["replacement"] = New(replThreshold, replRecovery)
	return m
}

func (m *Manager) CanProvision(op string) bool {
	m.mu.RLock()
	b, ok := m.breakers[op]
	if !ok {
		b = m.breakers["general"]
	}
	m.mu.RUnlock()
	return b.CanExecute()
}

func (m *Manager) RecordSuccess(op string) {
	m.mu.RLock()
	b, ok := m.breakers[op]
	if !ok {
		op = "general"
		b = m.breakers["general"]
	}
	m.mu.RUnlock()
	b.RecordSuccess()
	m.emitCBMetric(op, b)
}

func (m *Manager) RecordFailure(op string) {
	m.mu.RLock()
	b, ok := m.breakers[op]
	if !ok {
		op = "general"
		b = m.breakers["general"]
	}
	m.mu.RUnlock()
	b.RecordFailure()
	m.emitCBMetric(op, b)
}

func (m *Manager) ForceReset(op string) {
	m.mu.RLock()
	defer m.mu.RUnlock()
	if op == "" {
		for name, b := range m.breakers {
			b.ForceReset()
			m.emitCBMetric(name, b)
		}
		return
	}
	if b, ok := m.breakers[op]; ok {
		b.ForceReset()
		m.emitCBMetric(op, b)
	}
}

func (m *Manager) emitCBMetric(name string, b *Breaker) {
	v := 0.0
	if b.IsOpen() {
		v = 1.0
	}
	metrics.CPCircuitBreakerOpen.WithLabelValues(name).Set(v)
}

func (m *Manager) GetStatus() map[string]any {
	m.mu.RLock()
	defer m.mu.RUnlock()
	out := make(map[string]any, len(m.breakers))
	for name, b := range m.breakers {
		out[name] = b.Status()
	}
	return out
}
