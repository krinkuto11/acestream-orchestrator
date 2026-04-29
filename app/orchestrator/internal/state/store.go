package state

import (
	"fmt"
	"sort"
	"sync"
	"time"
)

const maxStatHistory = 720 // matches Python's default stats_history_max

// Store is the unified in-memory state for both planes.
// Control-plane owns engine/VPN lifecycle; proxy plane owns stream state.
// All exported methods are safe for concurrent use.
type Store struct {
	mu sync.RWMutex

	// engineReadyCh is replaced (old closed) every time a new engine registers.
	// Proxy goroutines waiting for capacity block on this channel and wake
	// instantly when any engine becomes available — no polling required.
	engineReadyCh chan struct{}
	engineReadyMu sync.Mutex

	// ─── Control-plane state ───────────────────────────────────────────────────
	engines         map[string]*Engine  // containerID -> Engine
	vpnNodes        map[string]*VPNNode // containerName -> VPNNode
	intents         []*ScalingIntent
	desiredReplicas int

	vpnPending       map[string]int  // per-VPN pending engine counter
	enginePending    map[string]int  // containerID -> in-flight stream reservations (claimed but not yet started)
	streamCounts     map[string]int  // containerID -> active stream count
	monitorCounts    map[string]int  // containerID -> monitor session count
	forwardedPending map[string]bool // vpnContainer -> pending flag
	lookaheadLayer   *int
	emptyAt          map[string]time.Time // containerID -> time first became empty
	targetConfigHash       string
	targetConfigGeneration int

	// ─── Proxy-plane state ────────────────────────────────────────────────────
	streams map[string]*StreamState   // contentID -> StreamState
	stats   map[string][]*StatSnapshot // contentID -> ring-buffer of snapshots
}

var Global = newStore()

func newStore() *Store {
	s := &Store{
		engines:          make(map[string]*Engine),
		vpnNodes:         make(map[string]*VPNNode),
		vpnPending:       make(map[string]int),
		enginePending:    make(map[string]int),
		streamCounts:     make(map[string]int),
		monitorCounts:    make(map[string]int),
		forwardedPending: make(map[string]bool),
		emptyAt:          make(map[string]time.Time),
		streams:          make(map[string]*StreamState),
		stats:            make(map[string][]*StatSnapshot),
		engineReadyCh:    make(chan struct{}),
	}
	return s
}

// ─── Engines ─────────────────────────────────────────────────────────────────

func (s *Store) AddEngine(e *Engine) {
	s.mu.Lock()
	if _, exists := s.engines[e.ContainerID]; !exists {
		e.FirstSeen = time.Now().UTC()
	}
	e.LastSeen = time.Now().UTC()
	s.engines[e.ContainerID] = e
	if e.Forwarded && e.VPNContainer != "" {
		delete(s.forwardedPending, e.VPNContainer)
	}
	s.mu.Unlock()
	// Wake any proxy goroutines blocked on SelectAndClaimEngine. Closing the
	// current channel broadcasts to all waiters; a fresh channel is installed
	// for the next round of waiters.
	s.NotifyEngineReady()
}

// NotifyEngineReady wakes all goroutines waiting in EngineReadyCh by closing
// the current channel and installing a new one for future waiters.
func (s *Store) NotifyEngineReady() {
	s.engineReadyMu.Lock()
	old := s.engineReadyCh
	s.engineReadyCh = make(chan struct{})
	s.engineReadyMu.Unlock()
	close(old)
}

// EngineReadyCh returns the current broadcast channel. Callers should capture
// it before calling SelectAndClaimEngine so they cannot miss a notification
// that fires between the failed select and the wait.
func (s *Store) EngineReadyCh() <-chan struct{} {
	s.engineReadyMu.Lock()
	defer s.engineReadyMu.Unlock()
	return s.engineReadyCh
}

// BumpDesiredReplicas atomically increments desiredReplicas by n, clamped to
// maxVal, and returns the new value.
func (s *Store) BumpDesiredReplicas(n, maxVal int) int {
	s.mu.Lock()
	defer s.mu.Unlock()
	target := s.desiredReplicas + n
	if target > maxVal {
		target = maxVal
	}
	s.desiredReplicas = target
	return target
}

func (s *Store) GetEngine(id string) (*Engine, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	e, ok := s.engines[id]
	return e, ok
}

func (s *Store) RemoveEngine(id string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.engines[id]; !ok {
		return false
	}
	delete(s.engines, id)
	delete(s.emptyAt, id)
	delete(s.streamCounts, id)
	return true
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

func (s *Store) ListEnginesWithCounts() []*Engine {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make([]*Engine, 0, len(s.engines))
	for _, e := range s.engines {
		cp := *e
		cp.StreamCount = s.streamCounts[e.ContainerID]
		cp.MonitorStreamCount = s.monitorCounts[e.ContainerID]
		out = append(out, &cp)
	}
	sort.Slice(out, func(i, j int) bool {
		return out[i].ContainerName < out[j].ContainerName
	})
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

func (s *Store) UpdateEngineStats(id string, cpu float64, memUsage int64, memPercent float64) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if e, ok := s.engines[id]; ok {
		e.CPUPercent = cpu
		e.MemoryUsage = memUsage
		e.MemoryPercent = memPercent
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

// SelectAndClaimVPN atomically picks the least-loaded node from candidates that
// is below effectiveLimit (current engines + pending) and increments its pending
// counter. Returns the selected node name and its load before the increment, or
// ("", -1) if all candidates are at or above the limit.
// Holding a single lock for read + write eliminates the TOCTOU race that arises
// when selectVPNContainer and IncrVPNPending are called as separate operations.
func (s *Store) SelectAndClaimVPN(candidates []string, effectiveLimit int) (string, int) {
	s.mu.Lock()
	defer s.mu.Unlock()

	best := ""
	bestLoad := -1
	for _, name := range candidates {
		engineCount := 0
		for _, e := range s.engines {
			if e.VPNContainer == name {
				engineCount++
			}
		}
		total := engineCount + s.vpnPending[name]
		if effectiveLimit > 0 && total >= effectiveLimit {
			continue
		}
		if best == "" || total < bestLoad {
			best = name
			bestLoad = total
		}
	}
	if best != "" {
		s.vpnPending[best]++
	}
	return best, bestLoad
}

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

// TryClaimForwardedSlot atomically claims the forwarded-engine slot for a VPN
// node. It returns true only if no forwarded engine is already registered AND
// no other goroutine has already claimed the pending slot. This prevents the
// double-election race when two engines are scheduled concurrently for the same
// VPN node.
func (s *Store) TryClaimForwardedSlot(vpn string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	// Check existing registered engines
	for _, e := range s.engines {
		if e.VPNContainer == vpn && e.Forwarded {
			return false
		}
	}
	// Check pending flag
	if s.forwardedPending[vpn] {
		return false
	}
	s.forwardedPending[vpn] = true
	return true
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

func (s *Store) RemoveVPNNode(name string) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.vpnNodes[name]; !ok {
		return false
	}
	delete(s.vpnNodes, name)
	return true
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

// UpdateVPNNodeControlHost stores the resolved IP address for the container's
// Gluetun control API so callers can prefer IP over container-name DNS.
func (s *Store) UpdateVPNNodeControlHost(name, host string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if n, ok := s.vpnNodes[name]; ok && host != "" {
		n.ControlHost = host
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

// SelectAndClaimEngine atomically picks the least-loaded eligible engine and
// increments its pending counter so that concurrent callers see the reservation
// immediately — before OnStreamStarted is called. This eliminates the TOCTOU
// race where all concurrent requests see the same engine load of 0.
func (s *Store) SelectAndClaimEngine(maxStreams int) (*Engine, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	type candidate struct {
		e    Engine
		load int
	}
	var candidates []candidate
	for _, e := range s.engines {
		if e.Draining || e.HealthStatus == HealthUnhealthy {
			continue
		}
		load := s.streamCounts[e.ContainerID] + s.enginePending[e.ContainerID] + s.monitorCounts[e.ContainerID]
		if load >= maxStreams {
			continue
		}
		candidates = append(candidates, candidate{*e, load})
	}

	if len(candidates) == 0 {
		return nil, fmt.Errorf("no eligible engine (total=%d)", len(s.engines))
	}

	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].load != candidates[j].load {
			return candidates[i].load < candidates[j].load
		}
		return candidates[i].e.Forwarded && !candidates[j].e.Forwarded
	})

	sel := candidates[0].e
	s.enginePending[sel.ContainerID]++
	cp := sel
	return &cp, nil
}

// ReleaseEnginePending decrements the pending reservation for an engine.
// Call this when a reservation cannot be converted to an active stream
// (StartStream rejected, hub at capacity, or non-tracking HLS path).
func (s *Store) ReleaseEnginePending(containerID string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.enginePending[containerID] > 0 {
		s.enginePending[containerID]--
	}
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

	// Resolve the canonical content ID: prefer ev.ContentID, fall back to
	// ev.Stream.Key (Python-format nested event).
	contentID := ev.ContentID
	if contentID == "" && ev.Stream != nil {
		contentID = ev.Stream.Key
	}

	st := &StreamState{
		ID:           contentID,
		ContentID:    contentID,
		EngineID:     ev.EngineID,
		EngineName:   ev.EngineName,
		StartedAt:    now,
		LastActivity: now,
		Status:       "started",
		FileIndexes:  "0",
		Clients:      []map[string]any{},
	}

	// Populate from Python nested fields when present.
	if ev.ContainerID != "" {
		st.ContainerID = ev.ContainerID
		if st.EngineID == "" {
			st.EngineID = ev.ContainerID
		}
	}
	if ev.Stream != nil {
		st.KeyType = ev.Stream.KeyType
		st.Key = ev.Stream.Key
		if ev.Stream.FileIndexes != "" {
			st.FileIndexes = ev.Stream.FileIndexes
		}
		st.Seekback = ev.Stream.Seekback
		st.LiveDelay = ev.Stream.LiveDelay
		st.ControlMode = ev.Stream.ControlMode
		if contentID == "" {
			st.ID = ev.Stream.Key
			st.ContentID = ev.Stream.Key
		}
	}
	if st.Key == "" {
		st.Key = contentID
	}
	if st.KeyType == "" {
		st.KeyType = "content_id"
	}
	if ev.Session != nil {
		st.PlaybackSessionID = ev.Session.PlaybackSessionID
		st.StatURL = ev.Session.StatURL
		st.CommandURL = ev.Session.CommandURL
		st.IsLive = ev.Session.IsLive != 0
		st.Bitrate = ev.Session.Bitrate
	}

	// Resolve EngineName and ContainerName from the engine registry if missing.
	if st.EngineID != "" {
		if st.ContainerID == "" {
			st.ContainerID = st.EngineID
		}
		if e, ok := s.engines[st.EngineID]; ok {
			if st.EngineName == "" {
				st.EngineName = e.ContainerName
			}
			if st.ContainerName == "" {
				st.ContainerName = e.ContainerName
			}
		}
	}

	// Fallback for EngineName/ContainerName
	if st.EngineName == "" && st.EngineID != "" {
		st.EngineName = st.EngineID
	}
	if st.ContainerName == "" {
		st.ContainerName = st.EngineName
	}


	s.streams[st.ID] = st
	if st.EngineID != "" {
		s.streamCounts[st.EngineID]++
		if s.enginePending[st.EngineID] > 0 {
			s.enginePending[st.EngineID]-- // transfer from pending to committed
		}
	}
	return st
}

func (s *Store) OnStreamEnded(ev StreamEndedEvent) {
	s.mu.Lock()
	defer s.mu.Unlock()

	// Resolve ID: ev.ContentID is the primary key; ev.StreamID is a Python UUID
	// alias — try both.
	id := ev.ContentID
	st, ok := s.streams[id]
	if !ok && ev.StreamID != "" {
		for _, candidate := range s.streams {
			if candidate.ID == ev.StreamID {
				st = candidate
				id = candidate.ID
				ok = true
				break
			}
		}
	}
	if !ok {
		return
	}

	now := time.Now().UTC()
	st.Status = "ended"
	st.EndedAt = &now

	delete(s.streams, id)
	if st.EngineID != "" && s.streamCounts[st.EngineID] > 0 {
		s.streamCounts[st.EngineID]--
	}
	// Retain stats a bit longer by NOT deleting the stats ring buffer here;
	// GC can happen on a separate sweep if needed.
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

// UpdateStreamClients syncs the active client count and client list from the proxy plane.
func (s *Store) UpdateStreamClients(contentID string, activeClients int, clients []map[string]any) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if st, ok := s.streams[contentID]; ok {
		st.ActiveClients = activeClients
		if clients != nil {
			st.Clients = clients
		} else {
			st.Clients = []map[string]any{}
		}
		if activeClients > 0 {
			st.LastActivity = time.Now().UTC()
		}
	}
}

// ─── Stats ring-buffer ────────────────────────────────────────────────────────

// AppendStat adds a snapshot to the per-stream ring buffer and updates the
// latest stats on the StreamState itself.
func (s *Store) AppendStat(contentID string, snap *StatSnapshot) {
	s.mu.Lock()
	defer s.mu.Unlock()

	hist := s.stats[contentID]
	hist = append(hist, snap)
	if len(hist) > maxStatHistory {
		hist = hist[len(hist)-maxStatHistory:]
	}
	s.stats[contentID] = hist

	// Reflect latest values on the live stream state.
	if st, ok := s.streams[contentID]; ok {
		st.LastActivity = time.Now().UTC()
		if snap.Peers != nil {
			st.Peers = snap.Peers
		}
		if snap.SpeedDown != nil {
			st.SpeedDown = snap.SpeedDown
		}
		if snap.SpeedUp != nil {
			st.SpeedUp = snap.SpeedUp
		}
		if snap.Downloaded != nil {
			st.Downloaded = snap.Downloaded
		}
		if snap.Uploaded != nil {
			st.Uploaded = snap.Uploaded
		}
		if snap.Bitrate != nil {
			st.Bitrate = snap.Bitrate
		}
		if snap.Livepos != nil {
			st.Livepos = snap.Livepos
		}
		if snap.ProxyBufferPieces != nil {
			st.ProxyBufferPieces = snap.ProxyBufferPieces
		}
	}
}

// GetStats returns a copy of the stats ring buffer for the given stream.
func (s *Store) GetStats(contentID string) []*StatSnapshot {
	s.mu.RLock()
	defer s.mu.RUnlock()
	hist := s.stats[contentID]
	out := make([]*StatSnapshot, len(hist))
	copy(out, hist)
	return out
}

// PruneStats removes the stats buffer for streams that no longer exist.
func (s *Store) PruneStats() {
	s.mu.Lock()
	defer s.mu.Unlock()
	for id := range s.stats {
		if _, ok := s.streams[id]; !ok {
			delete(s.stats, id)
		}
	}
}
