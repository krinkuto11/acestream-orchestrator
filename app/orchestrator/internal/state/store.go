package state

import (
	"sync"
	"time"
)

// Store is the unified in-memory state for both planes.
// Control-plane owns engine/VPN lifecycle; proxy plane owns stream state.
// All exported methods are safe for concurrent use.
type Store struct {
	mu sync.RWMutex

	// ─── Control-plane state ───────────────────────────────────────────────────
	engines         map[string]*Engine  // containerID -> Engine
	vpnNodes        map[string]*VPNNode // containerName -> VPNNode
	intents         []*ScalingIntent
	desiredReplicas int

	vpnPending       map[string]int  // per-VPN pending engine counter
	streamCounts     map[string]int  // containerID -> active stream count
	monitorCounts    map[string]int  // containerID -> monitor session count
	forwardedPending map[string]bool // vpnContainer -> pending flag
	lookaheadLayer   *int
	emptyAt          map[string]time.Time // containerID -> time first became empty
	targetConfigHash       string
	targetConfigGeneration int

	// ─── Proxy-plane state ────────────────────────────────────────────────────
	streams map[string]*StreamState // contentID -> StreamState
}

var Global = newStore()

func newStore() *Store {
	s := &Store{
		engines:          make(map[string]*Engine),
		vpnNodes:         make(map[string]*VPNNode),
		vpnPending:       make(map[string]int),
		streamCounts:     make(map[string]int),
		monitorCounts:    make(map[string]int),
		forwardedPending: make(map[string]bool),
		emptyAt:          make(map[string]time.Time),
		streams:          make(map[string]*StreamState),
	}
	return s
}

// ─── Engines ─────────────────────────────────────────────────────────────────

func (s *Store) AddEngine(e *Engine) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, exists := s.engines[e.ContainerID]; !exists {
		e.FirstSeen = time.Now().UTC()
	}
	e.LastSeen = time.Now().UTC()
	s.engines[e.ContainerID] = e
}

func (s *Store) GetEngine(id string) (*Engine, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	e, ok := s.engines[id]
	return e, ok
}

func (s *Store) RemoveEngine(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.engines, id)
	delete(s.emptyAt, id)
	delete(s.streamCounts, id)
}

func (s *Store) ListEngines() []*Engine {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*Engine, 0, len(s.engines))
	for _, e := range s.engines {
		out = append(out, e)
	}
	return out
}

func (s *Store) ListEngineNames() []string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]string, 0, len(s.engines)+len(s.intents))
	for _, e := range s.engines {
		out = append(out, e.ContainerName)
	}
	for _, it := range s.intents {
		if it.Action == "create" {
			if name, ok := it.Details["container_name"].(string); ok {
				out = append(out, name)
			}
		}
	}
	return out
}

func (s *Store) UpdateEngineHealth(id string, status HealthStatus) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if e, ok := s.engines[id]; ok {
		e.HealthStatus = status
		e.LastSeen = time.Now().UTC()
	}
}

func (s *Store) UpdateEngineLastSeen(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if e, ok := s.engines[id]; ok {
		e.LastSeen = time.Now().UTC()
	}
}

func (s *Store) UpdateEngineHost(id, host string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if e, ok := s.engines[id]; ok {
		e.Host = host
	}
}

func (s *Store) MarkEngineDraining(id, reason string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	e, ok := s.engines[id]
	if !ok || e.Draining {
		return false
	}
	e.Draining = true
	e.DrainReason = reason
	return true
}

func (s *Store) IsEngineDraining(id string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	e, ok := s.engines[id]
	return ok && e.Draining
}

func (s *Store) GetEnginesByVPN(vpn string) []*Engine {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var out []*Engine
	for _, e := range s.engines {
		if e.VPNContainer == vpn {
			out = append(out, e)
		}
	}
	return out
}

func (s *Store) HasForwardedEngineForVPN(vpn string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, e := range s.engines {
		if e.VPNContainer == vpn && e.Forwarded {
			return true
		}
	}
	return false
}

func (s *Store) HasAnyForwardedEngine() bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for _, e := range s.engines {
		if e.Forwarded {
			return true
		}
	}
	return false
}

// ─── VPN Pending ─────────────────────────────────────────────────────────────

func (s *Store) IncrVPNPending(vpn string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.vpnPending[vpn]++
}

func (s *Store) DecrVPNPending(vpn string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.vpnPending[vpn] > 0 {
		s.vpnPending[vpn]--
	}
}

func (s *Store) GetVPNPending(vpn string) int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.vpnPending[vpn]
}

// ─── Forwarded Pending ────────────────────────────────────────────────────────

func (s *Store) SetForwardedPending(vpn string, pending bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if pending {
		s.forwardedPending[vpn] = true
	} else {
		delete(s.forwardedPending, vpn)
	}
}

func (s *Store) IsForwardedPending(vpn string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.forwardedPending[vpn]
}

// ─── VPN Nodes ───────────────────────────────────────────────────────────────

func (s *Store) UpsertVPNNode(n *VPNNode) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if existing, ok := s.vpnNodes[n.ContainerName]; ok {
		n.FirstSeen = existing.FirstSeen
	} else {
		n.FirstSeen = time.Now().UTC()
	}
	n.LastSeen = time.Now().UTC()
	s.vpnNodes[n.ContainerName] = n
}

func (s *Store) GetVPNNode(name string) (*VPNNode, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	n, ok := s.vpnNodes[name]
	return n, ok
}

func (s *Store) RemoveVPNNode(name string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.vpnNodes, name)
}

func (s *Store) ListVPNNodes() []*VPNNode {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*VPNNode, 0, len(s.vpnNodes))
	for _, n := range s.vpnNodes {
		out = append(out, n)
	}
	return out
}

func (s *Store) SetVPNNodeCondition(name, condition string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if n, ok := s.vpnNodes[name]; ok {
		n.Condition = condition
		n.LastSeen = time.Now().UTC()
	}
}

func (s *Store) SetVPNNodeHealthy(name string, healthy bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	n, ok := s.vpnNodes[name]
	if !ok {
		return
	}
	prev := n.Healthy
	n.Healthy = healthy
	n.LastSeen = time.Now().UTC()
	if !healthy && prev {
		now := time.Now().UTC()
		n.UnhealthySince = &now
	} else if healthy {
		n.UnhealthySince = nil
	}
}

func (s *Store) IsVPNNodeDraining(name string) bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	n, ok := s.vpnNodes[name]
	return ok && n.Lifecycle == "draining"
}

func (s *Store) SetVPNNodeDraining(name string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	n, ok := s.vpnNodes[name]
	if !ok || n.Lifecycle == "draining" {
		return false
	}
	now := time.Now().UTC()
	n.Lifecycle = "draining"
	n.DrainingSince = &now
	return true
}

func (s *Store) SetVPNNodeLifecycle(name, lifecycle string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if n, ok := s.vpnNodes[name]; ok {
		n.Lifecycle = lifecycle
	}
}

func (s *Store) ListDrainingVPNNodes() []*VPNNode {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var out []*VPNNode
	for _, n := range s.vpnNodes {
		if n.Lifecycle == "draining" {
			out = append(out, n)
		}
	}
	return out
}

func (s *Store) ListDynamicVPNNodes() []*VPNNode {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var out []*VPNNode
	for _, n := range s.vpnNodes {
		if n.ManagedDynamic {
			out = append(out, n)
		}
	}
	return out
}

func (s *Store) ListNotReadyVPNNodes() []*VPNNode {
	s.mu.RLock()
	defer s.mu.RUnlock()
	var out []*VPNNode
	for _, n := range s.vpnNodes {
		if n.ManagedDynamic && !n.Healthy && n.Lifecycle != "draining" {
			out = append(out, n)
		}
	}
	return out
}

func (s *Store) CountActiveDynamicVPNNodes() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	n := 0
	for _, node := range s.vpnNodes {
		if node.ManagedDynamic && node.Lifecycle != "draining" {
			n++
		}
	}
	return n
}

// ─── Scaling ──────────────────────────────────────────────────────────────────

func (s *Store) GetDesiredReplicas() int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.desiredReplicas
}

func (s *Store) SetDesiredReplicas(n int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.desiredReplicas = n
}

func (s *Store) AddIntent(it *ScalingIntent) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.intents = append(s.intents, it)
}

func (s *Store) RemoveIntent(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	for i, it := range s.intents {
		if it.ID == id {
			s.intents = append(s.intents[:i], s.intents[i+1:]...)
			return
		}
	}
}

func (s *Store) ListIntents() []*ScalingIntent {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*ScalingIntent, len(s.intents))
	copy(out, s.intents)
	return out
}

// ─── Stream counts ────────────────────────────────────────────────────────────

func (s *Store) SetStreamCount(containerID string, count int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.streamCounts[containerID] = count
}

func (s *Store) GetStreamCount(containerID string) int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.streamCounts[containerID]
}

func (s *Store) GetAllStreamCounts() map[string]int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make(map[string]int, len(s.streamCounts))
	for k, v := range s.streamCounts {
		out[k] = v
	}
	return out
}

// GetStreamCounts is an alias for GetAllStreamCounts for proxy-plane compatibility.
func (s *Store) GetStreamCounts() map[string]int { return s.GetAllStreamCounts() }

func (s *Store) SetMonitorCount(containerID string, count int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if count <= 0 {
		delete(s.monitorCounts, containerID)
	} else {
		s.monitorCounts[containerID] = count
	}
}

func (s *Store) GetAllMonitorCounts() map[string]int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make(map[string]int, len(s.monitorCounts))
	for k, v := range s.monitorCounts {
		out[k] = v
	}
	return out
}

// GetMonitorCounts is an alias for GetAllMonitorCounts for proxy-plane compatibility.
func (s *Store) GetMonitorCounts() map[string]int { return s.GetAllMonitorCounts() }

// ─── Grace period ────────────────────────────────────────────────────────────

func (s *Store) RecordEmpty(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.emptyAt[id]; !ok {
		s.emptyAt[id] = time.Now().UTC()
	}
}

func (s *Store) ClearEmpty(id string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	delete(s.emptyAt, id)
}

func (s *Store) EmptySince(id string) (time.Time, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	t, ok := s.emptyAt[id]
	return t, ok
}

// ─── Lookahead layer ──────────────────────────────────────────────────────────

func (s *Store) SetLookaheadLayer(layer int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.lookaheadLayer = &layer
}

func (s *Store) GetLookaheadLayer() *int {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.lookaheadLayer
}

func (s *Store) ResetLookaheadLayer() {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.lookaheadLayer = nil
}

// ─── Target config ────────────────────────────────────────────────────────────

func (s *Store) SetTargetConfig(hash string, gen int) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.targetConfigHash = hash
	s.targetConfigGeneration = gen
}

func (s *Store) GetTargetConfig() (hash string, gen int) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.targetConfigHash, s.targetConfigGeneration
}

// ─── Stream operations (proxy plane) ─────────────────────────────────────────

func (s *Store) OnStreamStarted(ev StreamStartedEvent) *StreamState {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := time.Now().UTC()
	st := &StreamState{
		ID:           ev.ContentID,
		ContentID:    ev.ContentID,
		EngineID:     ev.EngineID,
		EngineName:   ev.EngineName,
		StartedAt:    now,
		LastActivity: now,
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
	if st.EngineID != "" && s.streamCounts[st.EngineID] > 0 {
		s.streamCounts[st.EngineID]--
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
