package state

import (
	"sync"
	"time"

	"github.com/acestream/controlplane/internal/config"
)

// Store is the in-memory control plane state protected by a RWMutex.
// All exported methods are safe for concurrent use.
type Store struct {
	mu sync.RWMutex

	engines    map[string]*Engine    // containerID -> Engine
	vpnNodes   map[string]*VPNNode   // containerName -> VPNNode
	intents    []*ScalingIntent
	desiredReplicas int

	// per-VPN pending engine counter for burst-safe allocation
	vpnPending map[string]int

	// per-engine stream counts (written from Python via Redis)
	streamCounts map[string]int
	// per-engine monitor session counts (written from Python via Redis)
	monitorCounts map[string]int

	// forwarded engine pending flags (before they appear in engines map)
	forwardedPending map[string]bool // vpnContainer -> pending

	// lookahead autoscaling layer
	lookaheadLayer *int

	// grace period tracking: containerID -> time it first became empty
	emptyAt map[string]time.Time

	// target engine config for rolling updates
	targetConfigHash       string
	targetConfigGeneration int
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
	}
	s.desiredReplicas = config.C.MinReplicas
	return s
}

// ─── Engines ────────────────────────────────────────────────────────────────

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

// ─── VPN Pending (burst-safe allocation counter) ────────────────────────────

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

// ─── Forwarded Pending (election guard) ─────────────────────────────────────

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

// ─── VPN Nodes ──────────────────────────────────────────────────────────────

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
		// transition healthy → unhealthy: record onset
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

// SetVPNNodeDraining transitions a node to the "draining" lifecycle state and
// records when draining began. Returns false if already draining.
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

// ListDynamicVPNNodes returns all VPN nodes that were dynamically provisioned.
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

// ListNotReadyVPNNodes returns dynamic VPN nodes that are unhealthy and not yet draining.
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

// CountActiveDynamicVPNNodes returns the number of dynamic VPN nodes that are
// not draining (i.e. actively serving or being provisioned).
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

// ─── Scaling ─────────────────────────────────────────────────────────────────

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

// ─── Stream counts (fed from Python via Redis) ───────────────────────────────

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

// ─── Grace period tracking ───────────────────────────────────────────────────

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

// ─── Lookahead layer ─────────────────────────────────────────────────────────

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

// ─── Target config ───────────────────────────────────────────────────────────

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
