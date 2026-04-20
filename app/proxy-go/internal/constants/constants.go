package constants

// Redis key prefix
const RedisKeyPrefix = "ace_proxy"

// Redis TTLs
const (
	RedisTTLDefault = 3600 // 1 hour
	RedisTTLShort   = 60   // 1 minute
	RedisTTLMedium  = 300  // 5 minutes
)

// Stream states
const (
	StateInitializing    = "initializing"
	StateConnecting      = "connecting"
	StateWaitingClients  = "waiting_for_clients"
	StateActive          = "active"
	StateError           = "error"
	StateStopping        = "stopping"
	StateStopped         = "stopped"
	StateBuffering       = "buffering"
)

// Event types
const (
	EventStreamStop         = "stream_stop"
	EventStreamStopped      = "stream_stopped"
	EventClientConnected    = "client_connected"
	EventClientDisconnected = "client_disconnected"
	EventClientStop         = "client_stop"
)

// Stream metadata fields
const (
	FieldContentID         = "content_id"
	FieldPlaybackURL       = "playback_url"
	FieldStatURL           = "stat_url"
	FieldCommandURL        = "command_url"
	FieldPlaybackSessionID = "playback_session_id"
	FieldState             = "state"
	FieldOwner             = "owner"
	FieldEngineID          = "engine_id"
	FieldEngineHost        = "engine_host"
	FieldEnginePort        = "engine_port"
	FieldEngineForwarded   = "engine_forwarded"
	FieldErrorMessage      = "error_message"
	FieldErrorTime         = "error_time"
	FieldStateChangedAt    = "state_changed_at"
	FieldInitTime          = "init_time"
	FieldBufferChunks      = "buffer_chunks"
	FieldTotalBytes        = "total_bytes"
	FieldIsLive            = "is_live"
	FieldIsEncrypted       = "is_encrypted"
	FieldBitrate           = "bitrate"
)

// Client metadata fields
const (
	ClientFieldConnectedAt     = "connected_at"
	ClientFieldLastActive      = "last_active"
	ClientFieldBytesSent       = "bytes_sent"
	ClientFieldSecondsBehind   = "buffer_seconds_behind"
	ClientFieldAvgRateKBps     = "avg_rate_KBps"
	ClientFieldCurrentRateKBps = "current_rate_KBps"
	ClientFieldIPAddress       = "ip_address"
	ClientFieldUserAgent       = "user_agent"
	ClientFieldWorkerID        = "worker_id"
	ClientFieldChunksSent      = "chunks_sent"
	ClientFieldStatsUpdatedAt  = "stats_updated_at"
)

// TS packet constants
const (
	TSPacketSize = 188
	TSSyncByte   = 0x47
	NullPIDHigh  = 0x1F
	NullPIDLow   = 0xFF
)

// HTTP streaming
const VLCUserAgent = "VLC/3.0.21 LibVLC/3.0.21"

// Proxy control modes
const (
	ProxyModeHTTP = "http"
	ProxyModeAPI  = "api"
)

// Stream modes
const (
	StreamModeTS  = "TS"
	StreamModeHLS = "HLS"
)

// Pacing
const (
	PacingBurstChunks   = 3
	FatKeepalivePackets = 50
)

// Max clients per stream
const MaxClientsPerStream = 100
