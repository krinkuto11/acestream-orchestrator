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
	VPNContainer  string            `json:"vpn_container,omitempty"`
	HealthStatus  HealthStatus      `json:"health_status"`
	P2PPort       int               `json:"p2p_port,omitempty"`
	FirstSeen     time.Time         `json:"first_seen"`
	LastSeen      time.Time         `json:"last_seen"`
	Draining      bool              `json:"draining"`
	DrainReason   string            `json:"drain_reason,omitempty"`
	LastAssignedAt time.Time         `json:"last_assigned_at,omitempty"`

	// Runtime aggregates — populated by the management API, not persisted.
	TotalPeers         int        `json:"total_peers"`
	TotalSpeedDown     int        `json:"total_speed_down"`
	TotalSpeedUp       int        `json:"total_speed_up"`
	StreamCount        int        `json:"stream_count"`
	MonitorStreamCount int        `json:"monitor_stream_count"`
	MonitorSpeedDown   int        `json:"monitor_speed_down"`
	MonitorSpeedUp     int        `json:"monitor_speed_up"`
	LastHealthCheck    *time.Time `json:"last_health_check,omitempty"`
	LastStreamUsage    *time.Time `json:"last_stream_usage,omitempty"`
	EngineVariant      string     `json:"engine_variant,omitempty"`
	Platform           string     `json:"platform,omitempty"`
	Version            string     `json:"version,omitempty"`
	ForwardedPort      *int       `json:"forwarded_port,omitempty"`
	CPUPercent         float64    `json:"cpu_percent"`
	MemoryUsage        int64      `json:"memory_usage"`
	MemoryPercent      float64    `json:"memory_percent"`
	Streams            []string   `json:"streams"`
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
	// HealthySince records the moment this node first became healthy in its
	// current lifecycle. Used to enforce a rebalance stabilization window so
	// density correction doesn't fire immediately after cold-boot.
	HealthySince *time.Time `json:"healthy_since,omitempty"`
	// ControlHost is the resolved IP address of the container's Gluetun API.
	// It is preferred over ContainerName for cross-Docker-network connectivity.
	ControlHost string `json:"control_host,omitempty"`
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

// LivePosData mirrors Python's LivePosData for live stream position tracking.
type LivePosData struct {
	Pos          any `json:"pos,omitempty"`
	LiveFirst    any `json:"live_first,omitempty"`
	LiveLast     any `json:"live_last,omitempty"`
	FirstTs      any `json:"first_ts,omitempty"`
	LastTs       any `json:"last_ts,omitempty"`
	BufferPieces any `json:"buffer_pieces,omitempty"`
}

// StatSnapshot is a point-in-time stats sample stored in the ring buffer.
type StatSnapshot struct {
	Ts                time.Time    `json:"ts"`
	Peers             *int         `json:"peers,omitempty"`
	SpeedDown         *int         `json:"speed_down,omitempty"`
	SpeedUp           *int         `json:"speed_up,omitempty"`
	Downloaded        *int         `json:"downloaded,omitempty"`
	Uploaded          *int         `json:"uploaded,omitempty"`
	Status            string       `json:"status,omitempty"`
	Bitrate           *int         `json:"bitrate,omitempty"`
	Livepos           *LivePosData `json:"livepos,omitempty"`
	ProxyBufferPieces *int         `json:"proxy_buffer_pieces,omitempty"`
}

// StreamState tracks a live stream being served by the proxy.
// Field names and JSON tags match the Python StreamState schema exactly so
// the React panel can consume both stacks without changes.
type StreamState struct {
	// Primary identifier — used as the map key and equals ContentID.
	ID        string `json:"id"`
	ContentID string `json:"content_id"` // backward-compat alias

	// Stream key fields (from StreamStartedEvent.Stream)
	KeyType     string `json:"key_type"`
	Key         string `json:"key"`
	FileIndexes string `json:"file_indexes"`
	Seekback    int    `json:"seekback"`
	LiveDelay   int    `json:"live_delay"`
	ControlMode string `json:"control_mode,omitempty"`
	StreamMode  string `json:"stream_mode,omitempty"`

	// Engine reference (Python names)
	ContainerID   string `json:"container_id"`
	ContainerName string `json:"container_name,omitempty"`

	// Internal engine IDs (Go names — kept for backward compat)
	EngineID   string `json:"engine_id"`
	EngineName string `json:"engine_name"`

	// Session info (from StreamStartedEvent.Session)
	PlaybackSessionID string `json:"playback_session_id"`
	StatURL           string `json:"stat_url"`
	CommandURL        string `json:"command_url"`
	IsLive            bool   `json:"is_live"`
	Bitrate           *int   `json:"bitrate,omitempty"`

	// Lifecycle
	StartedAt    time.Time  `json:"started_at"`
	EndedAt      *time.Time `json:"ended_at,omitempty"`
	LastActivity time.Time  `json:"last_activity"`
	Status       string     `json:"status"` // started | ended | pending_failover
	Paused       bool       `json:"paused"`

	// Latest stats (updated by AppendStat / UpdateStreamStats)
	Peers             *int `json:"peers,omitempty"`
	SpeedDown         *int `json:"speed_down,omitempty"`
	SpeedUp           *int `json:"speed_up,omitempty"`
	Downloaded        *int `json:"downloaded,omitempty"`
	Uploaded          *int `json:"uploaded,omitempty"`

	// Live position and proxy buffer
	Livepos           *LivePosData `json:"livepos,omitempty"`
	ProxyBufferPieces *int         `json:"proxy_buffer_pieces,omitempty"`

	// Connected clients (populated by the telemetry / client-tracker layer)
	ActiveClients int              `json:"active_clients"`
	Clients       []map[string]any `json:"clients"`
}

// ─── Event types ──────────────────────────────────────────────────────────────

// EngineAddress is a host:port pair used in stream-started events.
type EngineAddress struct {
	Host string `json:"host"`
	Port int    `json:"port"`
}

// StreamKeyPayload carries the stream input descriptor posted by the proxy.
type StreamKeyPayload struct {
	KeyType     string `json:"key_type"`
	Key         string `json:"key"`
	FileIndexes string `json:"file_indexes"`
	Seekback    int    `json:"seekback"`
	LiveDelay   int    `json:"live_delay"`
	ControlMode string `json:"control_mode,omitempty"`
	StreamMode  string `json:"stream_mode,omitempty"`
}

// SessionInfo carries playback session metadata posted by the proxy.
type SessionInfo struct {
	PlaybackSessionID string `json:"playback_session_id"`
	StatURL           string `json:"stat_url,omitempty"`
	CommandURL        string `json:"command_url,omitempty"`
	IsLive            int    `json:"is_live"` // 0/1 integer as Python sends it
	Bitrate           *int   `json:"bitrate,omitempty"`
}

// StreamStartedEvent is posted by the proxy when it begins serving a stream.
// Supports both the simple internal format (ContentID+EngineID) and the full
// Python-compatible nested format (Engine+Stream+Session objects).
type StreamStartedEvent struct {
	// Simple fields used by the internal proxy stateSink.
	ContentID  string `json:"content_id"`
	EngineID   string `json:"engine_id"`
	EngineName string `json:"engine_name"`

	// Python-compatible nested fields from POST /events/stream_started.
	ContainerID string            `json:"container_id,omitempty"`
	Engine      *EngineAddress    `json:"engine,omitempty"`
	Stream      *StreamKeyPayload `json:"stream,omitempty"`
	Session     *SessionInfo      `json:"session,omitempty"`
	Labels      map[string]string `json:"labels,omitempty"`
}

// StreamEndedEvent is posted by the proxy when it stops serving a stream.
type StreamEndedEvent struct {
	ContentID   string `json:"content_id"`
	// Python sends stream_id (UUID) and container_id; honour both for compat.
	StreamID    string `json:"stream_id,omitempty"`
	ContainerID string `json:"container_id,omitempty"`
	Reason      string `json:"reason,omitempty"`
}
