package state

import "time"

// ─── Control-plane types (authoritative) ─────────────────────────────────────

type HealthStatus string

const (
	HealthUnknown   HealthStatus = "unknown"
	HealthHealthy   HealthStatus = "healthy"
	HealthUnhealthy HealthStatus = "unhealthy"
)

type Engine struct {
	ContainerID   string            `json:"container_id"`
	ContainerName string            `json:"container_name"`
	Host          string            `json:"host"`
	Port          int               `json:"port"`
	APIPort       int               `json:"api_port"`
	Labels        map[string]string `json:"labels"`
	Forwarded     bool              `json:"forwarded"`
	VPNContainer  string            `json:"vpn_container"`
	HealthStatus  HealthStatus      `json:"health_status"`
	P2PPort       int               `json:"p2p_port,omitempty"`
	FirstSeen     time.Time         `json:"first_seen"`
	LastSeen      time.Time         `json:"last_seen"`
	Draining      bool              `json:"draining"`
	DrainReason   string            `json:"drain_reason,omitempty"`
}

type VPNNode struct {
	ContainerName           string     `json:"container_name"`
	ContainerID             string     `json:"container_id"`
	Status                  string     `json:"status"`
	Healthy                 bool       `json:"healthy"`
	Condition               string     `json:"condition"`
	Provider                string     `json:"provider"`
	Protocol                string     `json:"protocol,omitempty"`
	CredentialID            string     `json:"credential_id,omitempty"`
	ManagedDynamic          bool       `json:"managed_dynamic"`
	AssignedHostname        string     `json:"assigned_hostname,omitempty"`
	PortForwardingSupported bool       `json:"port_forwarding_supported"`
	Lifecycle               string     `json:"lifecycle"`
	FirstSeen               time.Time  `json:"first_seen"`
	LastSeen                time.Time  `json:"last_seen"`
	DrainingSince           *time.Time `json:"draining_since,omitempty"`
	UnhealthySince          *time.Time `json:"unhealthy_since,omitempty"`
}

// EngineSpec is the complete, immutable plan for a new engine.
type EngineSpec struct {
	ContainerName      string
	Image              string
	Command            []string
	EnvVars            map[string]string
	Labels             map[string]string
	NetworkMode        string
	Ports              map[string]int // "containerPort/tcp" -> hostPort
	MemLimit           string
	VPNContainerID     string
	HostHTTPPort       int
	ContainerHTTPPort  int
	HostAPIPort        int
	ContainerAPIPort   int
	ContainerHTTPSPort int
	HostHTTPSPort      int
	Forwarded          bool
	P2PPort            int
}

// ScalingIntent tracks a pending create/terminate action.
type ScalingIntent struct {
	ID      string
	Action  string // "create" | "terminate"
	Status  string // "pending" | "applied" | "failed"
	Details map[string]any
}

// ─── Proxy-plane types ────────────────────────────────────────────────────────

// StreamState tracks a live stream being served by the proxy.
type StreamState struct {
	ID            string    `json:"id"`
	ContentID     string    `json:"content_id"`
	EngineID      string    `json:"engine_id"`
	EngineName    string    `json:"engine_name"`
	StartedAt     time.Time `json:"started_at"`
	LastActivity  time.Time `json:"last_activity"`
	ActiveClients int       `json:"active_clients"`
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
