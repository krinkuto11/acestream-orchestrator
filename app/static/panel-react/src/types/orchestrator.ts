import { z } from 'zod'

export const proxyControlModeSchema = z.enum(['http', 'api'])
export type ProxyControlMode = z.infer<typeof proxyControlModeSchema>

export const streamKeyTypeSchema = z.enum([
  'content_id',
  'infohash',
  'torrent_url',
  'direct_url',
  'raw_data',
  'url',
  'magnet',
])
export type StreamKeyType = z.infer<typeof streamKeyTypeSchema>

export const engineHealthStatusSchema = z.enum(['healthy', 'unhealthy', 'unknown'])
export type EngineHealthStatus = z.infer<typeof engineHealthStatusSchema>

export const streamLifecycleStatusSchema = z.enum(['started', 'ended', 'pending_failover'])
export type StreamLifecycleStatus = z.infer<typeof streamLifecycleStatusSchema>

export const proxyRuntimeStateSchema = z.enum([
  'initializing',
  'connecting',
  'waiting_for_clients',
  'active',
  'error',
  'stopping',
  'stopped',
  'buffering',
])
export type ProxyRuntimeState = z.infer<typeof proxyRuntimeStateSchema>

export const proxyEventTypeSchema = z.enum([
  'stream_switch',
  'stream_switched',
  'stream_stop',
  'stream_stopped',
  'client_connected',
  'client_disconnected',
  'client_stop',
])
export type ProxyEventType = z.infer<typeof proxyEventTypeSchema>

export const engineAddressSchema = z.object({
  host: z.string(),
  port: z.number(),
})
export type EngineAddress = z.infer<typeof engineAddressSchema>

export const streamKeySchema = z.object({
  key_type: streamKeyTypeSchema,
  key: z.string(),
  file_indexes: z.string().default('0'),
  seekback: z.number().default(0),
  live_delay: z.number().default(0),
  control_mode: proxyControlModeSchema.nullish(),
})
export type StreamKey = z.infer<typeof streamKeySchema>

export const sessionInfoSchema = z.object({
  playback_session_id: z.string(),
  stat_url: z.string().nullish(),
  command_url: z.string().nullish(),
  is_live: z.number(),
})
export type SessionInfo = z.infer<typeof sessionInfoSchema>

export const livePosDataSchema = z.object({
  pos: z.string().nullish(),
  live_first: z.string().nullish(),
  live_last: z.string().nullish(),
  first_ts: z.string().nullish(),
  last_ts: z.string().nullish(),
  buffer_pieces: z.string().nullish(),
})
export type LivePosData = z.infer<typeof livePosDataSchema>

export const engineStateSchema = z
  .object({
    container_id: z.string(),
    container_name: z.string().nullish(),
    host: z.string(),
    port: z.number(),
    api_port: z.number().nullish(),
    labels: z.record(z.string(), z.string()).default({}),
    forwarded: z.boolean().default(false),
    first_seen: z.string(),
    last_seen: z.string(),
    streams: z.array(z.string()).default([]),
    health_status: engineHealthStatusSchema.default('unknown'),
    last_health_check: z.string().nullish(),
    last_stream_usage: z.string().nullish(),
    vpn_container: z.string().nullish(),
    engine_variant: z.string().nullish(),
    platform: z.string().nullish(),
    version: z.string().nullish(),
    forwarded_port: z.number().nullish(),
    restart_count: z.number().optional(),
    total_peers: z.number().optional(),
    total_speed_down: z.number().optional(),
    total_speed_up: z.number().optional(),
    stream_count: z.number().optional(),
    monitor_stream_count: z.number().optional(),
  })
  .passthrough()
export type EngineState = z.infer<typeof engineStateSchema>

export const streamStateSchema = z
  .object({
    id: z.string(),
    key_type: streamKeyTypeSchema,
    key: z.string(),
    file_indexes: z.string().default('0'),
    seekback: z.number().default(0),
    live_delay: z.number().default(0),
    control_mode: proxyControlModeSchema.nullish(),
    container_id: z.string(),
    container_name: z.string().nullish(),
    playback_session_id: z.string(),
    stat_url: z.string().nullish(),
    command_url: z.string().nullish(),
    is_live: z.boolean(),
    started_at: z.string(),
    ended_at: z.string().nullish(),
    status: streamLifecycleStatusSchema.default('started'),
    paused: z.boolean().default(false),
    peers: z.number().nullish(),
    speed_down: z.number().nullish(),
    speed_up: z.number().nullish(),
    downloaded: z.number().nullish(),
    uploaded: z.number().nullish(),
    livepos: livePosDataSchema.nullish(),
  })
  .passthrough()
export type StreamState = z.infer<typeof streamStateSchema>

export const streamStatSnapshotSchema = z
  .object({
    ts: z.string(),
    peers: z.number().nullish(),
    speed_down: z.number().nullish(),
    speed_up: z.number().nullish(),
    downloaded: z.number().nullish(),
    uploaded: z.number().nullish(),
    status: z.string().nullish(),
    livepos: livePosDataSchema.nullish(),
  })
  .passthrough()
export type StreamStatSnapshot = z.infer<typeof streamStatSnapshotSchema>

export const provisioningBlockedReasonSchema = z.object({
  code: z.enum(['circuit_breaker', 'vpn_disconnected', 'max_capacity', 'general_error']),
  message: z.string(),
  recovery_eta_seconds: z.number().nullish(),
  can_retry: z.boolean().default(false),
  should_wait: z.boolean().default(false),
})
export type ProvisioningBlockedReason = z.infer<typeof provisioningBlockedReasonSchema>

const vpnTunnelStatusSchema = z
  .object({
    enabled: z.boolean(),
    status: z.string(),
    container_name: z.string().nullish(),
    container: z.string().nullish(),
    health: z.string().nullish(),
    connected: z.boolean(),
    forwarded_port: z.number().nullish(),
    public_ip: z.string().nullish(),
    provider: z.string().nullish(),
    country: z.string().nullish(),
    city: z.string().nullish(),
    region: z.string().nullish(),
    load: z.number().nullish(),
    last_check: z.string().nullish(),
    last_check_at: z.string().nullish(),
    error: z.string().nullish(),
  })
  .passthrough()

const emergencyModeSchema = z
  .object({
    active: z.boolean().optional(),
    failed_vpn: z.string().nullish(),
    backup_vpn: z.string().nullish(),
    activated_at: z.string().nullish(),
  })
  .passthrough()

export const vpnStatusSchema = z.discriminatedUnion('mode', [
    z.object({
      mode: z.literal('disabled'),
      enabled: z.literal(false),
      status: z.string(),
      container_name: z.null(),
      container: z.null(),
      health: z.string(),
      connected: z.literal(false),
      forwarded_port: z.null(),
      last_check: z.null(),
      last_check_at: z.null(),
      vpn1: z.null(),
      vpn2: z.null(),
      emergency_mode: emergencyModeSchema.nullish(),
    }).passthrough(),
    z
      .object({
        mode: z.literal('single'),
        enabled: z.boolean(),
        status: z.string(),
        container_name: z.string().nullish(),
        container: z.string().nullish(),
        health: z.string().nullish(),
        connected: z.boolean(),
        forwarded_port: z.number().nullish(),
        last_check: z.string().nullish(),
        last_check_at: z.string().nullish(),
        vpn1: vpnTunnelStatusSchema,
        vpn2: z.null(),
        emergency_mode: emergencyModeSchema.nullish(),
      })
      .merge(vpnTunnelStatusSchema.partial())
      .passthrough(),
    z.object({
      mode: z.literal('redundant'),
      enabled: z.boolean(),
      status: z.string(),
      container_name: z.string().nullish(),
      container: z.string().nullish(),
      health: z.string().nullish(),
      connected: z.boolean(),
      forwarded_port: z.number().nullish(),
      last_check: z.string().nullish(),
      last_check_at: z.string().nullish(),
      vpn1: vpnTunnelStatusSchema,
      vpn2: vpnTunnelStatusSchema.nullish(),
      emergency_mode: emergencyModeSchema.nullish(),
    }).passthrough(),
  ])
export type VpnStatusPayload = z.infer<typeof vpnStatusSchema>

export const orchestratorStatusSchema = z
  .object({
    status: z.string(),
    engines: z.object({
      total: z.number(),
      running: z.number(),
      healthy: z.number(),
      unhealthy: z.number(),
    }),
    streams: z.object({
      active: z.number(),
      total: z.number(),
    }),
    capacity: z.object({
      total: z.number(),
      used: z.number(),
      available: z.number(),
      max_replicas: z.number(),
      min_replicas: z.number(),
    }),
    vpn: z.object({
      enabled: z.boolean(),
      connected: z.boolean(),
      health: z.string().nullish(),
      container: z.string().nullish(),
      forwarded_port: z.number().nullish(),
    }),
    provisioning: z.object({
      can_provision: z.boolean(),
      circuit_breaker_state: z.string().nullish(),
      last_failure: z.string().nullish(),
      blocked_reason: z.string().nullish(),
      blocked_reason_details: provisioningBlockedReasonSchema.nullish(),
    }),
    config: z.object({
      auto_delete: z.boolean(),
      grace_period_s: z.number(),
      engine_variant: z.string(),
      debug_mode: z.boolean(),
    }),
    proxy: z
      .object({
        engine_ingress_bps: z.record(z.string(), z.number()).nullish(),
        active_clients: z
          .object({
            total: z.number(),
            ts: z.number(),
            hls: z.number(),
            list: z
              .array(
                z.object({
                  id: z.string(),
                  stream_id: z.string(),
                  ip: z.string(),
                  ua: z.string(),
                  type: z.enum(['TS', 'HLS']),
                  bps: z.number(),
                  connected_at: z.number(),
                })
              )
              .nullish(),
          })
          .passthrough()
          .nullish(),
        request_window_1m: z
          .object({
            success_rate_percent: z.number(),
            total_requests_1m: z.number(),
          })
          .passthrough()
          .nullish(),
        ttfb: z
          .object({
            avg_ms: z.number(),
            p95_ms: z.number(),
          })
          .passthrough()
          .nullish(),
        throughput: z
          .object({
            ingress_mbps: z.number(),
            egress_mbps: z.number(),
            ingress_bps: z.number(),
            egress_bps: z.number(),
          })
          .passthrough()
          .nullish(),
      })
      .passthrough()
      .nullish(),
    north_star: z
      .object({
        proxy_active_clients: z.number(),
        global_active_streams: z.number(),
      })
      .passthrough()
      .nullish(),
    docker: z.record(z.string(), z.any()).nullish(),
    timestamp: z.string().or(z.number()),
  })
  .passthrough()
export type OrchestratorStatusResponse = z.infer<typeof orchestratorStatusSchema>

export const eventLogSchema = z
  .object({
    id: z.number(),
    timestamp: z.string(),
    event_type: z.enum(['engine', 'stream', 'vpn', 'health', 'system']),
    category: z.string(),
    message: z.string(),
    details: z.record(z.string(), z.unknown()).nullish(),
    container_id: z.string().nullish(),
    stream_id: z.string().nullish(),
  })
  .passthrough()
export type EventLog = z.infer<typeof eventLogSchema>

export const parseEngineListPayload = (payload: unknown): EngineState[] => {
  const itemsSchema = z.object({ items: z.array(engineStateSchema) })
  const arrayResult = z.array(engineStateSchema).safeParse(payload)
  if (arrayResult.success) {
    return arrayResult.data
  }

  const objectResult = itemsSchema.safeParse(payload)
  if (objectResult.success) {
    return objectResult.data.items
  }

  return []
}

export const parseStreamListPayload = (payload: unknown): StreamState[] => {
  const itemsSchema = z.object({ items: z.array(streamStateSchema) })
  const arrayResult = z.array(streamStateSchema).safeParse(payload)
  if (arrayResult.success) {
    return arrayResult.data
  }

  const objectResult = itemsSchema.safeParse(payload)
  if (objectResult.success) {
    return objectResult.data.items
  }

  return []
}
