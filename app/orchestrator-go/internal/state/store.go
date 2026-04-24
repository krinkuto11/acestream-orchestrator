package state

import (
	"sync"
	"time"
)

// Store is the in-memory orchestrator state store.
// Stream state is the authoritative copy; engine/VPN state is a read-through
// cache populated from Redis by the bridge.
type Store struct {
	mu      sync.RWMutex
	streams map[string]*StreamState  // keyed by content_id
	engines map[string]*EngineState  // keyed by container_id
	vpns    map[string]*VPNNodeState // keyed by container_name

	// Per-engine active stream counts (used for publishing to Redis).
	streamCounts  map[string]int // container_id -> count
	monitorCounts map[string]int // container_id -> count
}

// Global is the singleton in-memory store.
var Global = &Store{
	streams:       make(map[string]*StreamState),
	engines:       make(map[string]*EngineState),
	vpns:          make(map[string]*VPNNodeState),
	streamCounts:  make(map[string]int),
	monitorCounts: make(map[string]int),
}

// ── Stream operations ─────────────────────────────────────────────────────────

func (s *Store) OnStreamStarted(ev StreamStartedEvent) *StreamState {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now().UTC()
	st := &StreamState{
		ContentID:     ev.ContentID,
		EngineID:      ev.EngineID,
		EngineName:    ev.EngineName,
		StartedAt:     now,
		LastActivity:  now,
		ActiveClients: 0,
	}
	s.streams[ev.ContentID] = st
	s.streamCounts[ev.EngineID]++
	return st
}

func (s *Store) OnStreamEnded(ev StreamEndedEvent) {
	s.mu.Lock()
	defer s.mu.Unlock()
	st, ok := s.streams[ev.ContentID]
	if !ok {
		return
	}
	delete(s.streams, ev.ContentID)
	if st.EngineID != "" {
		if s.streamCounts[st.EngineID] > 0 {
			s.streamCounts[st.EngineID]--
		}
	}
}

func (s *Store) GetStream(contentID string) (*StreamState, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	st, ok := s.streams[contentID]
	if !ok {
		return nil, false
	}
	cp := *st
	return &cp, true
}

func (s *Store) ListStreams() []*StreamState {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*StreamState, 0, len(s.streams))
	for _, st := range s.streams {
		cp := *st
		out = append(out, &cp)
	}
	return out
}

func (s *Store) UpdateStreamActivity(contentID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if st, ok := s.streams[contentID]; ok {
		st.LastActivity = time.Now().UTC()
	}
}

// ── Engine operations ─────────────────────────────────────────────────────────

func (s *Store) UpsertEngine(e *EngineState) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.engines[e.ContainerID] = e
}

func (s *Store) RemoveEngine(containerID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.engines, containerID)
	delete(s.streamCounts, containerID)
	delete(s.monitorCounts, containerID)
}

func (s *Store) GetEngine(containerID string) (*EngineState, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	e, ok := s.engines[containerID]
	if !ok {
		return nil, false
	}
	cp := *e
	return &cp, true
}

func (s *Store) ListEngines() []*EngineState {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*EngineState, 0, len(s.engines))
	for _, e := range s.engines {
		cp := *e
		out = append(out, &cp)
	}
	return out
}

// ── VPN node operations ───────────────────────────────────────────────────────

func (s *Store) UpsertVPNNode(n *VPNNodeState) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.vpns[n.ContainerName] = n
}

func (s *Store) RemoveVPNNode(name string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.vpns, name)
}

func (s *Store) ListVPNNodes() []*VPNNodeState {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*VPNNodeState, 0, len(s.vpns))
	for _, n := range s.vpns {
		cp := *n
		out = append(out, &cp)
	}
	return out
}

// ── Count operations ──────────────────────────────────────────────────────────

func (s *Store) GetStreamCounts() map[string]int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make(map[string]int, len(s.streamCounts))
	for k, v := range s.streamCounts {
		out[k] = v
	}
	return out
}

func (s *Store) SetMonitorCount(engineID string, count int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.monitorCounts[engineID] = count
}

func (s *Store) GetMonitorCounts() map[string]int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make(map[string]int, len(s.monitorCounts))
	for k, v := range s.monitorCounts {
		out[k] = v
	}
	return out
}
