// Package engine provides engine selection for the proxy plane.
package engine

import (
	"fmt"
	"sort"
	"strings"

	"github.com/acestream/acestream/internal/config"
	"github.com/acestream/acestream/internal/persistence"
	"github.com/acestream/acestream/internal/state"
)

// Selection is the result of engine selection plus proxy settings.
type Selection struct {
	ContainerID string
	Host        string
	Port        int
	APIPort     int
	Forwarded   bool

	StreamMode       string
	ControlMode      string
	PrebufferSeconds int
	PacingMultiplier float64
	MaxStreams        int
}

// Select picks the least-loaded eligible engine from the unified state store.
func Select(st *state.Store, settings *persistence.SettingsStore) (*Selection, error) {
	engines := st.ListEngines()
	streams := st.ListStreams()
	monCounts := st.GetMonitorCounts()

	streamLoad := make(map[string]int, len(streams))
	for _, s := range streams {
		if s.EngineID != "" {
			streamLoad[s.EngineID]++
		}
	}

	cfg := config.C.Load()
	maxStreams := cfg.MaxStreamsPerEngine
	streamMode := cfg.StreamMode
	controlMode := cfg.ControlMode
	prebuffer := cfg.ProxyPrebufferSeconds
	pacing := cfg.PacingBitrateMultiplier

	if settings != nil {
		ps := settings.Get("proxy_settings")
		if v, ok := ps["max_streams_per_engine"].(float64); ok && v > 0 {
			maxStreams = int(v)
		}
		if v, ok := ps["stream_mode"].(string); ok && v != "" {
			streamMode = strings.ToUpper(v)
		}
		if v, ok := ps["control_mode"].(string); ok && v != "" {
			controlMode = strings.ToLower(v)
		}
		if v, ok := ps["proxy_prebuffer_seconds"].(float64); ok {
			prebuffer = int(v)
		}
		if v, ok := ps["pacing_bitrate_multiplier"].(float64); ok && v > 0 {
			pacing = v
		}
	}
	if maxStreams <= 0 {
		maxStreams = 3
	}

	type candidate struct {
		e    state.Engine
		load int
	}
	candidates := make([]candidate, 0, len(engines))
	for _, e := range engines {
		if e.Draining || e.HealthStatus == state.HealthUnhealthy {
			continue
		}
		load := streamLoad[e.ContainerID] + monCounts[e.ContainerID]
		if load >= maxStreams {
			continue
		}
		candidates = append(candidates, candidate{*e, load})
	}

	if len(candidates) == 0 {
		return nil, fmt.Errorf("no eligible engine (total=%d)", len(engines))
	}

	sort.Slice(candidates, func(i, j int) bool {
		if candidates[i].load != candidates[j].load {
			return candidates[i].load < candidates[j].load
		}
		return candidates[i].e.Forwarded && !candidates[j].e.Forwarded
	})

	sel := candidates[0].e
	apiPort := sel.APIPort
	if apiPort == 0 {
		apiPort = 62062
	}

	return &Selection{
		ContainerID:      sel.ContainerID,
		Host:             sel.Host,
		Port:             sel.Port,
		APIPort:          apiPort,
		Forwarded:        sel.Forwarded,
		StreamMode:       streamMode,
		ControlMode:      controlMode,
		PrebufferSeconds: prebuffer,
		PacingMultiplier: pacing,
		MaxStreams:        maxStreams,
	}, nil
}
