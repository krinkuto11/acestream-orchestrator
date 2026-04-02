import { create } from 'zustand'
import { MarkerType, type Edge, type Node } from 'reactflow'
import type {
  EngineState,
  OrchestratorStatusResponse,
  StreamState,
  VpnStatusPayload,
} from '@/types/orchestrator'

export type TunnelId = 'vpn1' | 'vpn2'
export type TopologyNodeKind = 'vpn' | 'engine' | 'proxy' | 'client'
export type TopologyNodeHealth = 'healthy' | 'degraded' | 'down'

export interface TopologyNodeData {
  kind: TopologyNodeKind
  title: string
  subtitle: string
  health: TopologyNodeHealth
  bandwidthMbps: number
  uploadMbps?: number
  proxyIngressMbps?: number
  streamCount: number
  vpnTunnel?: TunnelId
  failoverActive?: boolean
  metadata?: Record<string, string | number | boolean | null>
}

export interface TopologySummary {
  totalBandwidthMbps: number
  activeEngines: number
  activeStreams: number
  activeClients: number
  failoverEngines: number
  vpnDown: TunnelId[]
}

export interface TopologyInputSnapshot {
  engines?: EngineState[]
  streams?: StreamState[]
  vpnStatus?: VpnStatusPayload | null
  orchestratorStatus?: OrchestratorStatusResponse | null
}

interface TopologyState {
  nodes: Node<TopologyNodeData>[]
  edges: Edge[]
  summary: TopologySummary
  selectedNodeId: string | null
  lastUpdated: string | null
  isMockMode: boolean
  initializeMock: () => void
  hydrateFromBackend: (snapshot: TopologyInputSnapshot) => void
  simulateTick: () => void
  setSelectedNode: (nodeId: string | null) => void
}

const BASE_SUMMARY: TopologySummary = {
  totalBandwidthMbps: 0,
  activeEngines: 0,
  activeStreams: 0,
  activeClients: 0,
  failoverEngines: 0,
  vpnDown: [],
}

const clamp = (value: number, min: number, max: number): number => {
  if (value < min) return min
  if (value > max) return max
  return value
}

const randomBetween = (min: number, max: number): number => {
  return Math.random() * (max - min) + min
}

const jitter = (value: number, ratio = 0.16, floor = 0): number => {
  const delta = value * ratio
  return Math.max(floor, value + randomBetween(-delta, delta))
}

const INTERPOLATION_ALPHA = 0.35
const INTERPOLATION_EPSILON = 0.05
const FLOW_DEADBAND_Mbps = 0.35

type NodeFlowTarget = {
  bandwidthMbps: number
  uploadMbps?: number
  proxyIngressMbps?: number
}

type EdgeFlowTarget = {
  bandwidthMbps: number
  uploadMbps?: number
}

const nodeInterpolationTargets = new Map<string, NodeFlowTarget>()
const edgeInterpolationTargets = new Map<string, EdgeFlowTarget>()

const interpolateNumber = (current: number, target: number, alpha = INTERPOLATION_ALPHA): number => {
  const next = current + (target - current) * alpha
  return Math.abs(target - next) <= INTERPOLATION_EPSILON ? target : next
}

const applyDeadband = (
  current: number | undefined,
  target: number | undefined,
  threshold = FLOW_DEADBAND_Mbps,
): number | undefined => {
  if (typeof target !== 'number') return undefined
  if (typeof current !== 'number') return target
  return Math.abs(target - current) < threshold ? current : target
}


const formatCompactId = (id: string): string => {
  if (!id) return 'unknown'
  return id.length > 12 ? id.slice(0, 12) : id
}

const inferTunnelFromEngine = (
  engine: EngineState,
  index: number,
  vpnStatus?: VpnStatusPayload | null,
): TunnelId => {
  const raw = String(engine.vpn_container || '').trim()
  const vpnName = raw.toLowerCase()

  const vpn1Candidates = [
    vpnStatus?.vpn1?.container_name,
    vpnStatus?.vpn1?.container,
  ]
    .filter((value): value is string => Boolean(value))
    .map((value) => value.trim().toLowerCase())

  const vpn2Candidates = [
    vpnStatus?.vpn2?.container_name,
    vpnStatus?.vpn2?.container,
  ]
    .filter((value): value is string => Boolean(value))
    .map((value) => value.trim().toLowerCase())

  // Prefer explicit backend identity over heuristic naming.
  if (vpn1Candidates.includes(vpnName)) return 'vpn1'
  if (vpn2Candidates.includes(vpnName)) return 'vpn2'

  // Fallback heuristics for legacy/custom names.
  if (vpnName.includes('secondary') || vpnName.includes('backup') || vpnName.includes('vpn2')) {
    return 'vpn2'
  }
  if (vpnName.includes('primary') || vpnName.includes('main') || vpnName.includes('vpn1')) {
    return 'vpn1'
  }

  return index % 2 === 0 ? 'vpn1' : 'vpn2'
}

const deriveTunnelConnectivity = (
  vpnStatus: VpnStatusPayload | null | undefined,
  hasRealData: boolean,
): Record<TunnelId, boolean> => {
  if (!vpnStatus) {
    if (hasRealData) {
      return { vpn1: true, vpn2: true }
    }
    // Demo mode intentionally starts with VPN1 down to showcase failover behavior.
    return { vpn1: false, vpn2: true }
  }

  if (vpnStatus.mode === 'disabled') {
    return { vpn1: true, vpn2: true }
  }

  if (vpnStatus.mode === 'single') {
    return { vpn1: Boolean(vpnStatus.vpn1?.connected), vpn2: true }
  }

  return {
    vpn1: Boolean(vpnStatus.vpn1?.connected),
    vpn2: Boolean(vpnStatus.vpn2?.connected),
  }
}

const toMbps = (speedMaybe: number | null | undefined): number => {
  if (speedMaybe == null || Number.isNaN(speedMaybe)) return 0
  return (speedMaybe * 8) / 1000
}

// EMA smoothing to prevent pipes from flashing to 0 during burst waits
const smoothBandwidth = (current: number, previous: number | undefined, isActive: boolean): number => {
  const prev = previous || 0

  if (!isActive) return 0 // Instantly kill the pipe if the connection is dead
  if (prev === 0) return current // Instantly jump up on first byte

  if (current === 0) {
    // If waiting for a burst, decay the speed by 15% per tick instead of dropping to 0
    const decayed = prev * 0.85
    return decayed < 0.1 ? 0 : decayed
  }

  // Standard EMA (30% new, 70% old) for fluid, non-jittery active speeds
  return current * 0.3 + prev * 0.7
}

const buildSnapshot = (
  { engines, streams, vpnStatus, orchestratorStatus }: TopologyInputSnapshot,
  prevState?: TopologyState
): {
  nodes: Node<TopologyNodeData>[]
  edges: Edge[]
  summary: TopologySummary
  lastUpdated: string
  isMockMode: boolean
} => {
  const isMockMode = !Boolean(engines && engines.length)

  const workingEngines: EngineState[] = !isMockMode
    ? engines || []
    : Array.from({ length: 8 }).map((_, idx) => {
        const tunnel = idx % 2 === 0 ? 'vpn1' : 'vpn2'
        return {
          container_id: `mock-engine-${idx + 1}`,
          container_name: `engine-${idx + 1}`,
          host: '127.0.0.1',
          port: 6878 + idx,
          labels: {},
          forwarded: false,
          first_seen: new Date(Date.now() - (idx + 1) * 120000).toISOString(),
          last_seen: new Date().toISOString(),
          streams: [],
          health_status: 'healthy',
          vpn_container: tunnel,
        }
      })

  const workingStreams: StreamState[] = !isMockMode
    ? streams || []
    : workingEngines.flatMap((engine, idx) => {
        const streamCount = idx % 3 === 0 ? 2 : 1
        return Array.from({ length: streamCount }).map((__, streamIdx) => ({
          id: `mock-stream-${idx + 1}-${streamIdx + 1}`,
          key_type: 'infohash',
          key: `3d2b7cfae9aa${idx}${streamIdx}`,
          file_indexes: '0',
          seekback: 0,
          live_delay: 0,
          container_id: engine.container_id,
          container_name: engine.container_name,
          playback_session_id: `session-${idx + 1}-${streamIdx + 1}`,
          stat_url: `http://127.0.0.1:${engine.port}/webui/api/service`,
          command_url: `http://127.0.0.1:${engine.port}/ace/getstream`,
          is_live: true,
          started_at: new Date(Date.now() - randomBetween(20000, 120000)).toISOString(),
          status: 'started',
          paused: false,
          peers: Math.round(randomBetween(12, 78)),
          speed_down: Math.round(randomBetween(12, 95) * 1024),
          speed_up: Math.round(randomBetween(2, 11) * 1024),
          downloaded: Math.round(randomBetween(12, 480) * 1024 * 1024),
          uploaded: Math.round(randomBetween(2, 50) * 1024 * 1024),
        }))
      })

  const streamMap = new Map<string, StreamState[]>()
  for (const stream of workingStreams) {
    const entry = streamMap.get(stream.container_id) || []
    entry.push(stream)
    streamMap.set(stream.container_id, entry)
  }

  const tunnelConnectivity = deriveTunnelConnectivity(vpnStatus, !isMockMode)
  const vpnDown = (Object.entries(tunnelConnectivity)
    .filter(([, connected]) => !connected)
    .map(([id]) => id)) as TunnelId[]

  const nodes: Node<TopologyNodeData>[] = []
  const edges: Edge[] = []

  const isVpnDisabledMode = vpnStatus?.mode === 'disabled'
  const vpn1NodeId = 'vpn1'
  const vpn2NodeId = 'vpn2'
  const internetNodeId = 'internet'
  const proxyNodeId = 'proxy-core'

  const failoverEngines: string[] = []

  // 1. Sort engines so active ones are prioritized at the top
  const engineStats = workingEngines.map((engine) => {
    const engineStreams = streamMap.get(engine.container_id) || []
    const streamMeasuredDownMbps = engineStreams.reduce((sum, stream) => sum + toMbps(stream.speed_down), 0)
    const streamMeasuredUpMbps = engineStreams.reduce((sum, stream) => sum + toMbps(stream.speed_up), 0)
    const reportedTotalDownMbps = toMbps(engine.total_speed_down)
    const reportedTotalUpMbps = toMbps(engine.total_speed_up)

    // Prefer backend aggregates when available: they include monitor-session STATUS traffic.
    const measuredDownMbps = Math.max(streamMeasuredDownMbps, reportedTotalDownMbps)
    const measuredUpMbps = Math.max(streamMeasuredUpMbps, reportedTotalUpMbps)
    return {
      engine,
      streamCount: engineStreams.length,
      measuredMbps: measuredDownMbps, // used for sorting
      streamMeasuredDownMbps,
      streamMeasuredUpMbps,
      measuredDownMbps,
      measuredUpMbps,
    }
  }).sort((a, b) => {
    if (b.streamCount !== a.streamCount) return b.streamCount - a.streamCount
    if (b.measuredMbps !== a.measuredMbps) return b.measuredMbps - a.measuredMbps
    return (a.engine.container_name || '').localeCompare(b.engine.container_name || '')
  })

  const isVpnClusterMode = Boolean(vpnStatus && vpnStatus.mode !== 'disabled')
  const engineStatsWithTunnel = engineStats.map((entry, index) => ({
    ...entry,
    assignedTunnel: vpnStatus?.mode === 'single' ? 'vpn1' : inferTunnelFromEngine(entry.engine, index, vpnStatus),
  }))

  // 2. Define Staggered Grid properties
  const NUM_COLUMNS = isVpnClusterMode
    ? Math.max(1, Math.min(2, Math.ceil(engineStats.length / 8)))
    : Math.max(1, Math.min(3, Math.ceil(engineStats.length / 6)))
  const COLUMN_SPACING_X = 340   // 210px node width + 130px gap for pipes and labels
  const STAGGERED_ROW_SPACING_Y = 140 // Enough vertical space for the node + a gap for horizontal pipes
  const CLUSTER_GAP_Y = 220 // The vertical space between the bottom of VPN1 and top of VPN2

  const engineStartX = 350
  const engineStartY = 80

  const enginesPerTunnel = engineStatsWithTunnel.reduce(
    (acc, item) => {
      acc[item.assignedTunnel] += 1
      return acc
    },
    { vpn1: 0, vpn2: 0 } as Record<TunnelId, number>,
  )

  // Calculate bounding boxes for the clusters
  const vpn1StartY = engineStartY
  const vpn1Height = Math.max(0, enginesPerTunnel.vpn1 - 1) * STAGGERED_ROW_SPACING_Y
  const vpn1CenterY = vpn1StartY + (vpn1Height / 2)

  // VPN2 starts below VPN1
  const vpn2StartY = vpn1StartY + vpn1Height + CLUSTER_GAP_Y
  const vpn2Height = Math.max(0, enginesPerTunnel.vpn2 - 1) * STAGGERED_ROW_SPACING_Y
  const vpn2CenterY = vpn2StartY + (vpn2Height / 2)

  const tunnelClusterStartY = {
    vpn1: vpn1StartY,
    vpn2: vpn2StartY,
  }

  // Center Y for downstream nodes (Proxy, Clients) spans the entire height.
  // In VPN-disabled mode we center against the single staggered engine corridor.
  const isSingleVpn = vpnStatus?.mode === 'single'
  const totalHeight = isVpnClusterMode
    ? (isSingleVpn ? vpn1Height : (vpn2StartY + vpn2Height) - engineStartY)
    : Math.max(0, engineStats.length - 1) * STAGGERED_ROW_SPACING_Y
  const centerY = engineStartY + (totalHeight / 2)

  const tunnelLocalIndex: Record<TunnelId, number> = {
    vpn1: 0,
    vpn2: 0,
  }

  if (isVpnDisabledMode) {
    nodes.push({
      id: internetNodeId,
      type: 'topologyNode',
      position: { x: -240, y: centerY },
      data: {
        kind: 'vpn',
        title: 'Internet',
        subtitle: 'Direct egress (VPN disabled)',
        health: 'healthy',
        bandwidthMbps: isMockMode ? randomBetween(180, 260) : 0,
        streamCount: 0,
        metadata: {
          connected: true,
          publicIp: 'Direct route',
          provider: 'WAN',
          country: null,
        },
      },
    })
  } else {
    nodes.push({
      id: vpn1NodeId,
      type: 'topologyNode',
      position: { x: -240, y: vpn1CenterY },
      data: {
        kind: 'vpn',
        title: 'VPN Tunnel A',
        subtitle: vpnStatus?.vpn1?.container_name || 'gluetun-primary',
        health: tunnelConnectivity.vpn1 ? 'healthy' : 'down',
        bandwidthMbps: isMockMode ? randomBetween(120, 210) : 0,
        streamCount: 0,
        metadata: {
          connected: tunnelConnectivity.vpn1,
          publicIp: vpnStatus?.vpn1?.public_ip || '185.102.112.44',
          provider: vpnStatus?.vpn1?.provider || 'ProtonVPN',
          country: vpnStatus?.vpn1?.country || null,
        },
      },
    })

    if (vpnStatus?.mode !== 'single') {
      nodes.push({
        id: vpn2NodeId,
        type: 'topologyNode',
        position: { x: -240, y: vpn2CenterY },
        data: {
          kind: 'vpn',
          title: 'VPN Tunnel B',
          subtitle: vpnStatus?.vpn2?.container_name || 'gluetun-secondary',
          health: tunnelConnectivity.vpn2 ? 'healthy' : 'down',
          bandwidthMbps: isMockMode ? randomBetween(90, 180) : 0,
          streamCount: 0,
          failoverActive: !tunnelConnectivity.vpn1 && tunnelConnectivity.vpn2,
          metadata: {
            connected: tunnelConnectivity.vpn2,
            publicIp: vpnStatus?.vpn2?.public_ip || '79.127.210.63',
            provider: vpnStatus?.vpn2?.provider || 'Mullvad',
            country: vpnStatus?.vpn2?.country || null,
          },
        },
      })
    }
  }

  // 3. Process the nodes using the Zig-Zag Staggered Corridor pattern
  engineStatsWithTunnel.forEach(({ engine, streamCount, measuredMbps, streamMeasuredDownMbps, measuredDownMbps, measuredUpMbps, assignedTunnel }, index) => {
    const engineStreams = streamMap.get(engine.container_id) || []
    const monitorStreamCount = Math.max(
      0,
      Number(
        engine.monitor_stream_count ??
          ((engine.stream_count ?? engineStreams.length) - engineStreams.length),
      ),
    )
    const hasMonitoringSession = monitorStreamCount > 0

    let colIndex: number
    let currentX: number
    let currentY: number
    if (isVpnClusterMode) {
      const localIndex = tunnelLocalIndex[assignedTunnel]
      tunnelLocalIndex[assignedTunnel] += 1

      colIndex = localIndex % NUM_COLUMNS
      currentX = engineStartX + (colIndex * COLUMN_SPACING_X)
      // The magic trick: multiply by localIndex directly to guarantee unique Ys inside the cluster
      currentY = tunnelClusterStartY[assignedTunnel] + (localIndex * STAGGERED_ROW_SPACING_Y)
    } else {
      // Determine column (0, 1, 2, 0, 1, 2...)
      colIndex = index % NUM_COLUMNS
      // Calculate positions — every index gets a unique Y, creating dedicated horizontal pipe corridors
      currentX = engineStartX + (colIndex * COLUMN_SPACING_X)
      currentY = engineStartY + (index * STAGGERED_ROW_SPACING_Y)
    }

    const tunnelHealthy = tunnelConnectivity[assignedTunnel]
    const backupTunnel = assignedTunnel === 'vpn1' ? 'vpn2' : 'vpn1'
    const backupHealthy = tunnelConnectivity[backupTunnel]

    const failoverActive = !isVpnDisabledMode && !tunnelHealthy && backupHealthy
    const sourceNodeId: TunnelId | 'internet' = isVpnDisabledMode
      ? internetNodeId
      : (failoverActive ? backupTunnel : assignedTunnel)

    // VPN → Engine: P2P download speed.
    // Keep the route visibly active during monitor-only sessions even if Ace reports 0 throughput.
    const monitoringFloorMbps = hasMonitoringSession && measuredDownMbps <= 0
      ? Math.max(0.3, monitorStreamCount * 0.3)
      : 0
    const bandwidthMbps = measuredDownMbps > 0
      ? measuredDownMbps
      : (monitoringFloorMbps > 0 ? monitoringFloorMbps : (isMockMode ? randomBetween(8, 72) : 0))
    
    // Engine → Proxy: Actual per-engine ingress
    const engineIngressBps = orchestratorStatus?.proxy?.engine_ingress_bps?.[engine.container_id] ?? 0
    const proxyIngressMbps = engineIngressBps > 0 
      ? (engineIngressBps * 8) / 1_000_000 
      : (streamCount > 0 ? streamMeasuredDownMbps * 0.98 : (isMockMode ? randomBetween(2, 18) : 0))
    
    let health: TopologyNodeHealth = 'healthy'
    if (engine.health_status === 'unhealthy' || (!tunnelHealthy && !backupHealthy)) {
      health = 'down'
    } else if (engine.health_status === 'unknown' || failoverActive) {
      health = 'degraded'
    }

    if (failoverActive) {
      failoverEngines.push(engine.container_id)
    }

    nodes.push({
      id: engine.container_id,
      type: 'topologyNode',
      position: { x: currentX, y: currentY },
      data: {
        kind: 'engine',
        title: engine.container_name || formatCompactId(engine.container_id),
        subtitle: `${engine.host}:${engine.port}`,
        health,
        streamCount,
        bandwidthMbps,
        uploadMbps: measuredUpMbps > 0 ? measuredUpMbps : (isMockMode ? randomBetween(2, 12) : 0),
        proxyIngressMbps: proxyIngressMbps,
        vpnTunnel: isVpnDisabledMode ? undefined : (sourceNodeId as TunnelId),
        failoverActive,
        metadata: {
          assignedTunnel,
          activeTunnel: sourceNodeId,
          peers: engineStreams.reduce((sum, stream) => sum + (stream.peers || 0), 0),
          monitorStreamCount,
          variant: engine.engine_variant || 'default',
          forwarded: engine.forwarded,
          forwardedPort: engine.forwarded_port || null,
        },
      },
    })

    // VPN → Engine edge: shows both download (P2P ingress) and upload (P2P seeding) bandwidth
    const edgeUploadBw = measuredUpMbps > 0 ? measuredUpMbps : (isMockMode ? randomBetween(2, 12) : 0)
    edges.push({
      id: `${sourceNodeId}->${engine.container_id}`,
      type: 'topologyEdge',
      source: sourceNodeId,
      target: engine.container_id,
      animated: true,
      markerEnd: { type: MarkerType.ArrowClosed },
      data: {
        bandwidthMbps: bandwidthMbps,
        uploadMbps: edgeUploadBw,
        labelPosition: 'near-target',
        monitoringActive: hasMonitoringSession,
      },
      style: {
        stroke: (failoverActive || hasMonitoringSession) ? '#f59e0b' : '#64748b',
        strokeWidth: clamp(1.6 + bandwidthMbps / 55, 1.6, 5.8),
        strokeDasharray: failoverActive ? '8 5' : undefined,
      },
    })

    // Engine → Proxy edge: shows upload bandwidth (proxy ingress)
    edges.push({
      id: `${engine.container_id}->${proxyNodeId}`,
      type: 'topologyEdge',
      source: engine.container_id,
      target: proxyNodeId,
      animated: true,
      markerEnd: { type: MarkerType.ArrowClosed },
      data: {
        bandwidthMbps: proxyIngressMbps,
        labelPosition: 'near-source',
      },
      style: {
        stroke: '#60a5fa',
        strokeWidth: clamp(1.8 + proxyIngressMbps / 48, 1.8, 6.4),
      },
    })
  })

  if (!isMockMode && !isVpnDisabledMode) {
    const vpn1Node = nodes.find(n => n.id === vpn1NodeId)
    const vpn2Node = nodes.find(n => n.id === vpn2NodeId)
    if (vpn1Node) {
      const vpn1Engines = nodes.filter(n => n.data.kind === 'engine' && n.data.vpnTunnel === 'vpn1')
      vpn1Node.data.bandwidthMbps = vpn1Engines.reduce((s, n) => s + (n.data.bandwidthMbps || 0), 0)
      vpn1Node.data.uploadMbps = vpn1Engines.reduce((s, n) => s + (n.data.uploadMbps || 0), 0)
    }
    if (vpn2Node) {
      const vpn2Engines = nodes.filter(n => n.data.kind === 'engine' && n.data.vpnTunnel === 'vpn2')
      vpn2Node.data.bandwidthMbps = vpn2Engines.reduce((s, n) => s + (n.data.bandwidthMbps || 0), 0)
      vpn2Node.data.uploadMbps = vpn2Engines.reduce((s, n) => s + (n.data.uploadMbps || 0), 0)
    }
  }

  const totalBandwidthMbps = engineStats.reduce((sum, { engine }) => {
    const found = nodes.find((node) => node.id === engine.container_id)
    return sum + (found?.data?.bandwidthMbps || 0)
  }, 0)

  // Proxy bandwidth should reflect proxy ingress only, not monitor-side engine traffic.
  const totalProxyIngressMbps = engineStats.reduce((sum, { engine }) => {
    const found = nodes.find((node) => node.id === engine.container_id)
    return sum + (found?.data?.proxyIngressMbps || 0)
  }, 0)
  const proxyNodeBandwidthMbps = orchestratorStatus?.proxy?.throughput?.ingress_mbps ?? totalProxyIngressMbps

  const activeStreams = orchestratorStatus?.streams?.active ?? workingStreams.length
  
  // 3. Client Nodes and Egress Pipelines
  const mockClients = [
    { id: 'mock-1', ip: '192.168.1.45', ua: 'VLC/3.0.18', type: 'TS', connected_at: Date.now() / 1000 - 3600, bps: 4500000, bytes_sent: 1.2 * 1024 * 1024 * 1024 },
    { id: 'mock-2', ip: '172.16.5.12', ua: 'AppleCoreMedia/1.0.0', type: 'HLS', connected_at: Date.now() / 1000 - 1800, bps: 2800000, bytes_sent: 450 * 1024 * 1024 },
    { id: 'mock-3', ip: '10.0.0.156', ua: 'ExoPlayerLib/2.18.5', type: 'TS', connected_at: Date.now() / 1000 - 600, bps: 6200000, bytes_sent: 2.8 * 1024 * 1024 * 1024 },
  ]
  const clientList = isMockMode ? mockClients : (orchestratorStatus?.proxy?.active_clients?.list || [])
  const activeClients = isMockMode ? clientList.length : (orchestratorStatus?.proxy?.active_clients?.total ?? clientList.length)

  // Dynamically position downstream nodes based on the number of engine columns
  const proxyNodeX = engineStartX + (NUM_COLUMNS * COLUMN_SPACING_X) + 160
  const clientNodeX = proxyNodeX + 460

  nodes.push({
    id: proxyNodeId,
    type: 'topologyNode',
    position: { x: proxyNodeX, y: centerY },
    data: {
      kind: 'proxy',
      title: 'Mux and Proxy Core',
      subtitle: '/ace and /hls pipeline',
      health: (orchestratorStatus?.status === 'healthy' || orchestratorStatus?.proxy?.request_window_1m?.success_rate_percent > 98) ? 'healthy' : 'degraded',
      streamCount: activeStreams,
      bandwidthMbps: proxyNodeBandwidthMbps,
      metadata: {
        successRate: orchestratorStatus?.proxy?.request_window_1m?.success_rate_percent 
          ? `${orchestratorStatus.proxy.request_window_1m.success_rate_percent}%`
          : (orchestratorStatus?.status === 'healthy' ? '99.8%' : '96.2%'),
        ttfbP95Ms: orchestratorStatus?.proxy?.ttfb?.p95_ms || (failoverEngines.length > 0 ? 860 : 410),
      },
    },
  })

  const clientSpacingY = 160
  const clientStartX = clientNodeX
  const clientTotalHeight = (clientList.length - 1) * clientSpacingY
  const clientStartY = centerY - clientTotalHeight / 2

  clientList.forEach((client: any, index: number) => {
    const cNodeId = `client-${client.id}`
    // Stagger nodes slightly for visual depth and to make them feel "alive"
    const nodeX = clientStartX + (index % 2 === 0 ? 0 : 45)
    const nodeY = clientStartY + (index * clientSpacingY)
    const rawClientBw = (client.bps * 8) / 1_000_000
    
    // Retrieve previous client node state
    const prevClientNode = prevState?.nodes.find(n => n.id === cNodeId)
    
    // Smooth it! (isActive is true simply because the client exists in the list)
    const clientBwMbps = smoothBandwidth(rawClientBw, prevClientNode?.data?.bandwidthMbps, true)

    nodes.push({
      id: cNodeId,
      type: 'topologyNode',
      position: { x: nodeX, y: nodeY },
      data: {
        kind: 'client',
        title: client.ip || 'Unknown IP',
        subtitle: client.ua || 'Generic Player',
        health: 'healthy',
        streamCount: 1,
        bandwidthMbps: clientBwMbps,
        metadata: {
          type: client.type,
          totalBytes: client.bytes_sent || 0,
          connectedAt: new Date(client.connected_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
        },
      },
    })

    edges.push({
      id: `${proxyNodeId}->${cNodeId}`,
      type: 'topologyEdge',
      source: proxyNodeId,
      target: cNodeId,
      animated: true,
      markerEnd: { type: MarkerType.ArrowClosed },
      data: {
        bandwidthMbps: clientBwMbps,
        protocol: client.type,
      },
      style: {
        stroke: '#22c55e',
        strokeWidth: clamp(2.2 + clientBwMbps / 35, 2.2, 8.5),
      },
    })
  })

  // 4. Final layering and store state update
  // Sort edges so active pipes render on top of inactive ones
  edges.sort((a, b) => {
    const aActive = (((a.data?.bandwidthMbps || 0) + (a.data?.uploadMbps || 0)) > 0.1) ? 1 : 0
    const bActive = (((b.data?.bandwidthMbps || 0) + (b.data?.uploadMbps || 0)) > 0.1) ? 1 : 0
    if (aActive !== bActive) {
      return aActive - bActive
    }
    const aBw = (a.data?.bandwidthMbps || 0) + (a.data?.uploadMbps || 0)
    const bBw = (b.data?.bandwidthMbps || 0) + (b.data?.uploadMbps || 0)
    return aBw - bBw
  })

  // Ensure nodes are on top of edges by default
  nodes.forEach(n => {
    n.zIndex = 100
  })

  // Ensure edges have zIndex for ReactFlow's internal ordering
  edges.forEach((edge) => {
    const bw = (edge.data?.bandwidthMbps || 0) + (edge.data?.uploadMbps || 0)
    edge.zIndex = bw > 0.1 ? 50 : 5
  })

  const summary: TopologySummary = {
    totalBandwidthMbps,
    activeEngines: workingEngines.length,
    activeStreams,
    activeClients,
    failoverEngines: failoverEngines.length,
    vpnDown,
  }

  return {
    nodes,
    edges,
    summary,
    lastUpdated: new Date().toISOString(),
    isMockMode,
  }
}

export const useTopologyStore = create<TopologyState>((set, get) => ({
  nodes: [],
  edges: [],
  summary: BASE_SUMMARY,
  selectedNodeId: null,
  lastUpdated: null,
  isMockMode: true,

  initializeMock: () => {
    nodeInterpolationTargets.clear()
    edgeInterpolationTargets.clear()
    const snapshot = buildSnapshot({})
    set({
      nodes: snapshot.nodes,
      edges: snapshot.edges,
      summary: snapshot.summary,
      lastUpdated: snapshot.lastUpdated,
      isMockMode: snapshot.isMockMode,
      selectedNodeId: null,
    })
  },

  hydrateFromBackend: (snapshot) => {
    const currentState = get()
    const next = buildSnapshot(snapshot, currentState)
    
    set((state) => {
      const nextNodeIds = new Set(next.nodes.map((node) => node.id))
      for (const nodeId of nodeInterpolationTargets.keys()) {
        if (!nextNodeIds.has(nodeId)) {
          nodeInterpolationTargets.delete(nodeId)
        }
      }

      const stabilizedNodes = next.nodes.map((nextNode) => {
        const existingNode = state.nodes.find((n) => n.id === nextNode.id)
        const targetBandwidth = applyDeadband(existingNode?.data?.bandwidthMbps, nextNode.data?.bandwidthMbps)
        const targetUpload = applyDeadband(existingNode?.data?.uploadMbps, nextNode.data?.uploadMbps)
        const targetProxyIngress = applyDeadband(
          existingNode?.data?.proxyIngressMbps,
          nextNode.data?.proxyIngressMbps,
        )

        nodeInterpolationTargets.set(nextNode.id, {
          bandwidthMbps: targetBandwidth ?? 0,
          uploadMbps: targetUpload,
          proxyIngressMbps: targetProxyIngress,
        })

        if (!existingNode) return nextNode
        
        const dataChanged = JSON.stringify(existingNode.data) !== JSON.stringify(nextNode.data)
        const posChanged = 
          existingNode.position.x !== nextNode.position.x || 
          existingNode.position.y !== nextNode.position.y
          
        if (!dataChanged && !posChanged) return existingNode

        return {
          ...nextNode,
          data: {
            ...nextNode.data,
            bandwidthMbps: existingNode.data?.bandwidthMbps ?? nextNode.data.bandwidthMbps,
            uploadMbps: existingNode.data?.uploadMbps ?? nextNode.data.uploadMbps,
            proxyIngressMbps: existingNode.data?.proxyIngressMbps ?? nextNode.data.proxyIngressMbps,
          },
        }
      })

      const nextEdgeIds = new Set(next.edges.map((edge) => edge.id))
      for (const edgeId of edgeInterpolationTargets.keys()) {
        if (!nextEdgeIds.has(edgeId)) {
          edgeInterpolationTargets.delete(edgeId)
        }
      }

      const stabilizedEdges = next.edges.map((nextEdge) => {
        const existingEdge = state.edges.find((e) => e.id === nextEdge.id)
        const nextEdgeTargetBandwidth = Number(nextEdge.data?.bandwidthMbps ?? 0)
        const existingEdgeBandwidth =
          typeof existingEdge?.data?.bandwidthMbps === 'number'
            ? Number(existingEdge.data.bandwidthMbps)
            : undefined
        const nextEdgeTargetUpload =
          typeof nextEdge.data?.uploadMbps === 'number' ? nextEdge.data.uploadMbps : undefined
        const existingEdgeUpload =
          typeof existingEdge?.data?.uploadMbps === 'number' ? existingEdge.data.uploadMbps : undefined

        edgeInterpolationTargets.set(nextEdge.id, {
          bandwidthMbps: applyDeadband(existingEdgeBandwidth, nextEdgeTargetBandwidth) ?? 0,
          uploadMbps: applyDeadband(existingEdgeUpload, nextEdgeTargetUpload),
        })

        if (!existingEdge) return nextEdge
        
        const styleChanged = JSON.stringify(existingEdge.style) !== JSON.stringify(nextEdge.style)
        const labelChanged = existingEdge.label !== nextEdge.label
        
        if (!styleChanged && !labelChanged) return existingEdge

        const currentBandwidth = Number(existingEdge.data?.bandwidthMbps ?? nextEdge.data?.bandwidthMbps ?? 0)
        const currentUpload =
          typeof existingEdge.data?.uploadMbps === 'number'
            ? existingEdge.data.uploadMbps
            : nextEdge.data?.uploadMbps

        return {
          ...nextEdge,
          data: {
            ...nextEdge.data,
            bandwidthMbps: currentBandwidth,
            uploadMbps: currentUpload,
          },
          style: {
            ...nextEdge.style,
            strokeWidth: existingEdge.style?.strokeWidth ?? nextEdge.style?.strokeWidth,
          },
        }
      })

      return {
        nodes: stabilizedNodes,
        edges: stabilizedEdges,
        summary: next.summary,
        lastUpdated: next.lastUpdated,
        isMockMode: next.isMockMode,
        selectedNodeId: state.selectedNodeId,
      }
    })
  },

  simulateTick: () => {
    const state = get()

    if (!state.nodes.length || !state.edges.length) {
      return
    }

    const nextNodes = state.nodes.map((node) => {
      const data = node.data
      if (!data) {
        return node
      }

      const flowTarget = nodeInterpolationTargets.get(node.id)

      if (!flowTarget) {
        if (!state.isMockMode) {
          return node
        }

        if (data.kind === 'vpn') {
          return {
            ...node,
            data: {
              ...data,
              bandwidthMbps: jitter(data.bandwidthMbps, 0.08, 12),
            },
          }
        }

        if (data.kind === 'engine') {
          const floor = data.streamCount > 0 ? 1.5 : 0
          return {
            ...node,
            data: {
              ...data,
              bandwidthMbps: jitter(data.bandwidthMbps, 0.14, floor),
            },
          }
        }

        if (data.kind === 'proxy' || data.kind === 'client') {
          return {
            ...node,
            data: {
              ...data,
              bandwidthMbps: jitter(data.bandwidthMbps, 0.07, 4),
            },
          }
        }

        return node
      }

      const nextBandwidth = interpolateNumber(data.bandwidthMbps || 0, flowTarget.bandwidthMbps || 0)
      const nextUpload =
        typeof data.uploadMbps === 'number' || typeof flowTarget.uploadMbps === 'number'
          ? interpolateNumber(data.uploadMbps ?? 0, flowTarget.uploadMbps ?? 0)
          : undefined
      const nextProxyIngress =
        typeof data.proxyIngressMbps === 'number' || typeof flowTarget.proxyIngressMbps === 'number'
          ? interpolateNumber(data.proxyIngressMbps ?? 0, flowTarget.proxyIngressMbps ?? 0)
          : undefined

      return {
        ...node,
        data: {
          ...data,
          bandwidthMbps: nextBandwidth,
          ...(typeof nextUpload === 'number' ? { uploadMbps: nextUpload } : {}),
          ...(typeof nextProxyIngress === 'number' ? { proxyIngressMbps: nextProxyIngress } : {}),
        },
      }
    })

    const nodeMap = new Map(nextNodes.map((node) => [node.id, node]))

    const nextEdges = state.edges.map((edge) => {
      const flowTarget = edgeInterpolationTargets.get(edge.id)
      const sourceNode = nodeMap.get(edge.source)
      const targetNode = nodeMap.get(edge.target)
      
      let targetBandwidth = flowTarget?.bandwidthMbps ?? 0
      let targetUpload = flowTarget?.uploadMbps
      
      if (!flowTarget) {
        if (sourceNode?.data?.kind === 'vpn' && targetNode?.data?.kind === 'engine') {
          targetBandwidth = targetNode.data.bandwidthMbps || 0
          targetUpload = targetNode.data.uploadMbps
        } else if (sourceNode?.data?.kind === 'engine' && targetNode?.data?.kind === 'proxy') {
          targetBandwidth = sourceNode.data.proxyIngressMbps || 0
        } else {
          targetBandwidth = sourceNode?.data?.bandwidthMbps || 0
        }

        if (state.isMockMode) {
          const jitterVal = (Math.random() - 0.5) * (targetBandwidth * 0.05)
          targetBandwidth = Math.max(0, targetBandwidth + jitterVal)
          targetUpload =
            typeof targetUpload === 'number'
              ? Math.max(0, targetUpload + (Math.random() - 0.5) * (targetUpload * 0.05))
              : undefined
        }
      }

      const currentBandwidth = Number(edge.data?.bandwidthMbps ?? 0)
      const nextBandwidth = interpolateNumber(currentBandwidth, targetBandwidth)
      const nextUpload =
        typeof edge.data?.uploadMbps === 'number' || typeof targetUpload === 'number'
          ? interpolateNumber(edge.data?.uploadMbps ?? 0, targetUpload ?? 0)
          : undefined

      const failover = edge.style?.strokeDasharray !== undefined

      let strokeWidth = clamp(1.6 + nextBandwidth / (failover ? 58 : 46), 1.6, 8.6)
      if (sourceNode?.data?.kind === 'engine' && targetNode?.data?.kind === 'proxy') {
        strokeWidth = clamp(1.8 + nextBandwidth / 48, 1.8, 6.4)
      } else if (sourceNode?.data?.kind === 'proxy' && targetNode?.data?.kind === 'client') {
        strokeWidth = clamp(2.2 + nextBandwidth / 35, 2.2, 8.5)
      }

      return {
        ...edge,
        data: {
          ...edge.data,
          bandwidthMbps: nextBandwidth,
          ...(typeof nextUpload === 'number' ? { uploadMbps: nextUpload } : {}),
        },
        style: {
          ...edge.style,
          strokeWidth,
        },
      }
    })

    const totalBandwidthMbps = nextNodes
      .filter((node) => node.data?.kind === 'engine')
      .reduce((sum, node) => sum + (node.data?.bandwidthMbps || 0), 0)

    const failoverEngines = nextNodes.filter(
      (node) => node.data?.kind === 'engine' && node.data.failoverActive,
    ).length

    // Sort edges so active pipes overlap non-active pipes
    const sortedEdges = [...nextEdges].sort((a, b) => {
      const aActive = (((a.data?.bandwidthMbps || 0) + (a.data?.uploadMbps || 0)) > 0.1) ? 1 : 0
      const bActive = (((b.data?.bandwidthMbps || 0) + (b.data?.uploadMbps || 0)) > 0.1) ? 1 : 0
      if (aActive !== bActive) {
        return aActive - bActive
      }
      const aVal = (a.data?.bandwidthMbps || 0) + (a.data?.uploadMbps || 0)
      const bVal = (b.data?.bandwidthMbps || 0) + (b.data?.uploadMbps || 0)
      return aVal - bVal
    })

    sortedEdges.forEach((edge) => {
      const bw = (edge.data?.bandwidthMbps || 0) + (edge.data?.uploadMbps || 0)
      edge.zIndex = bw > 0.1 ? 50 : 5
    })

    // Layer nodes on top
    const layeredNodes = nextNodes.map(n => ({ ...n, zIndex: 100 }))

    set((prev) => ({
      nodes: layeredNodes,
      edges: sortedEdges,
      summary: {
        ...prev.summary,
        totalBandwidthMbps,
        failoverEngines,
      },
      lastUpdated: new Date().toISOString(),
    }))
  },

  setSelectedNode: (selectedNodeId) => {
    set({ selectedNodeId })
  },
}))
