// Package engine provides engine selection for the proxy plane.
package engine

import (
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

// Select picks the least-loaded eligible engine from the unified state store
// and atomically reserves a slot, preventing the TOCTOU race where concurrent
// requests all see the same engine load of zero before any stream is registered.
func Select(st *state.Store, settings *persistence.SettingsStore) (*Selection, error) {
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

	sel, err := st.SelectAndClaimEngine(maxStreams)
	if err != nil {
		return nil, err
	}

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
