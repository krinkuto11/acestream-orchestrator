package state

import "time"

// StreamState mirrors the Python StreamState model.
type StreamState struct {
	ContentID   string    `json:"content_id"`
	EngineID    string    `json:"engine_id"`    // container ID of the owning engine
	EngineName  string    `json:"engine_name"`
	StartedAt   time.Time `json:"started_at"`
	LastActivity time.Time `json:"last_activity"`
	// Live stats (updated by proxy events)
	ActiveClients int `json:"active_clients"`
}

// EngineState is the orchestrator-side view of a control plane engine.
// Populated from Redis by the bridge, not authoritative.
type EngineState struct {
	ContainerID   string `json:"container_id"`
	ContainerName string `json:"container_name"`
	Host          string `json:"host"`
	Port          int    `json:"port"`
	APIPort       int    `json:"api_port"`
	Forwarded     bool   `json:"forwarded"`
	VPNContainer  string `json:"vpn_container"`
	HealthStatus  string `json:"health_status"`
	Draining      bool   `json:"draining"`
	DrainReason   string `json:"drain_reason,omitempty"`
}

// VPNNodeState is the orchestrator-side view of a VPN node.
type VPNNodeState struct {
	ContainerName           string `json:"container_name"`
	ContainerID             string `json:"container_id"`
	Status                  string `json:"status"`
	Healthy                 bool   `json:"healthy"`
	Provider                string `json:"provider"`
	Protocol                string `json:"protocol,omitempty"`
	Lifecycle               string `json:"lifecycle"`
	ManagedDynamic          bool   `json:"managed_dynamic"`
	PortForwardingSupported bool   `json:"port_forwarding_supported"`
}

// StreamStartedEvent is posted by the proxy when it begins serving a stream.
type StreamStartedEvent struct {
	ContentID  string `json:"content_id"`
	EngineID   string `json:"engine_id"`
	EngineName string `json:"engine_name"`
}

// StreamEndedEvent is posted by the proxy when it stops serving a stream.
type StreamEndedEvent struct {
	ContentID string `json:"content_id"`
	EngineID  string `json:"engine_id"`
}
