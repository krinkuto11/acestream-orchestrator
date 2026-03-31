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

const smoothBandwidth = (current: number, previous: number | undefined, streamCount: number): number => {
  const prev = previous || 0

  // If there are no active streams at all, the pipe should immediately drain to 0
  if (streamCount === 0) return 0

  // If we didn't have a previous value, just use current
  if (prev === 0) return current

  // If we have streams but the instantaneous burst is 0 (waiting for next chunk),
  // decay the previous bandwidth smoothly by 15% instead of dropping instantly to 0.
  if (current === 0) {
    const decayed = prev * 0.85
    return decayed < 0.2 ? 0 : decayed
  }

  // If data is flowing normally, use an Exponential Moving Average (40% new, 60% old)
  // This smooths out wild spikes and creates a fluid visualization.
  return current * 0.4 + prev * 0.6
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

const buildSnapshot = (
  {
    engines,
    streams,
    vpnStatus,
    orchestratorStatus,
  }: TopologyInputSnapshot,
  prevState?: TopologyState,
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

  const vpn1NodeId = 'vpn1'
  const vpn2NodeId = 'vpn2'
  const proxyNodeId = 'proxy-core'

  const failoverEngines: string[] = []

  // 1. Sort engines so active ones are prioritized at the top
  const engineStats = workingEngines.map((engine) => {
    const engineStreams = streamMap.get(engine.container_id) || []
    const measuredDownMbps = engineStreams.reduce((sum, stream) => sum + toMbps(stream.speed_down), 0)
    const measuredUpMbps = engineStreams.reduce((sum, stream) => sum + toMbps(stream.speed_up), 0)
    return {
      engine,
      streamCount: engineStreams.length,
      measuredMbps: measuredDownMbps, // used for sorting
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

  // Center Y for downstream nodes (Proxy, Clients) spans the entire height
  const totalHeight = (vpn2StartY + vpn2Height) - engineStartY
  const centerY = engineStartY + (totalHeight / 2)

  const tunnelLocalIndex: Record<TunnelId, number> = {
    vpn1: 0,
    vpn2: 0,
  }

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

  // 3. Process the nodes using the Zig-Zag Staggered Corridor pattern
  engineStatsWithTunnel.forEach(({ engine, streamCount, measuredMbps, measuredUpMbps, assignedTunnel }, index) => {
    const engineStreams = streamMap.get(engine.container_id) || []

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

    const failoverActive = !tunnelHealthy && backupHealthy
    const sourceTunnel = failoverActive ? backupTunnel : assignedTunnel

    // VPN → Engine: smooth bursty traffic so active lanes do not flash to zero between chunks.
    const rawBandwidthMbps = measuredMbps > 0 ? measuredMbps : (isMockMode ? randomBetween(8, 72) : 0)
    const prevEngineNode = prevState?.nodes.find(n => n.id === engine.container_id)
    const bandwidthMbps = smoothBandwidth(rawBandwidthMbps, prevEngineNode?.data?.bandwidthMbps, streamCount)
    const downloadBwMbps = bandwidthMbps
    
    // Engine → Proxy: Actual per-engine ingress reported by proxy (bps -> Mbps)
    const engineIngressBps = orchestratorStatus?.proxy?.engine_ingress_bps?.[engine.container_id] ?? 0
    const uploadBwMbps = engineIngressBps > 0 
      ? (engineIngressBps * 8) / 1_000_000 
      : (streamCount > 0 ? downloadBwMbps * 0.98 : (isMockMode ? randomBetween(2, 18) : 0))
    
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
        proxyIngressMbps: uploadBwMbps,
        vpnTunnel: sourceTunnel,
        failoverActive,
        metadata: {
          assignedTunnel,
          activeTunnel: sourceTunnel,
          peers: engineStreams.reduce((sum, stream) => sum + (stream.peers || 0), 0),
          variant: engine.engine_variant || 'default',
          forwarded: engine.forwarded,
          forwardedPort: engine.forwarded_port || null,
        },
      },
    })

    // VPN → Engine edge: shows both download (P2P ingress) and upload (P2P seeding) bandwidth
    const edgeUploadBw = measuredUpMbps > 0 ? measuredUpMbps : (isMockMode ? randomBetween(2, 12) : 0)
    edges.push({
      id: `${sourceTunnel}->${engine.container_id}`,
      type: 'topologyEdge',
      source: sourceTunnel,
      target: engine.container_id,
      animated: true,
      markerEnd: { type: MarkerType.ArrowClosed },
      data: {
        bandwidthMbps: downloadBwMbps,
        uploadMbps: edgeUploadBw,
        labelPosition: 'near-target',
      },
      style: {
        stroke: failoverActive ? '#f59e0b' : '#64748b',
        strokeWidth: clamp(1.6 + downloadBwMbps / 55, 1.6, 5.8),
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
        bandwidthMbps: uploadBwMbps,
        labelPosition: 'near-source',
      },
      style: {
        stroke: '#60a5fa',
        strokeWidth: clamp(1.8 + uploadBwMbps / 48, 1.8, 6.4),
      },
    })
  })

  if (!isMockMode) {
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
      bandwidthMbps: totalBandwidthMbps,
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
    const clientBwMbps = (client.bps * 8) / 1_000_000

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
      // Stabilize nodes: reuse existing object if content is logically same
      const stabilizedNodes = next.nodes.map((nextNode) => {
        const existingNode = state.nodes.find((n) => n.id === nextNode.id)
        if (!existingNode) return nextNode
        
        // Compare data fields and position
        const dataChanged = JSON.stringify(existingNode.data) !== JSON.stringify(nextNode.data)
        const posChanged = 
          existingNode.position.x !== nextNode.position.x || 
          existingNode.position.y !== nextNode.position.y
          
        if (!dataChanged && !posChanged) return existingNode
        return nextNode
      })

      // Stabilize edges: reuse existing object if content is logically same
      const stabilizedEdges = next.edges.map((nextEdge) => {
        const existingEdge = state.edges.find((e) => e.id === nextEdge.id)
        if (!existingEdge) return nextEdge
        
        const styleChanged = JSON.stringify(existingEdge.style) !== JSON.stringify(nextEdge.style)
        const labelChanged = existingEdge.label !== nextEdge.label
        
        if (!styleChanged && !labelChanged) return existingEdge
        return nextEdge
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

    if (!state.isMockMode) {
      return
    }

    const nextNodes = state.nodes.map((node) => {
      const data = node.data
      if (!data) {
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
    })

    const nodeMap = new Map(nextNodes.map((node) => [node.id, node]))

    const nextEdges = state.edges.map((edge) => {
      const sourceNode = nodeMap.get(edge.source)
      const targetNode = nodeMap.get(edge.target)
      
      let baseBandwidth = 0
      
      // VPN -> Engine: use Engine's download speed (stored in bandwidthMbps)
      if (sourceNode?.data?.kind === 'vpn' && targetNode?.data?.kind === 'engine') {
        baseBandwidth = targetNode.data.bandwidthMbps || 0
      } 
      // Engine -> Proxy: use Engine's proxy ingress speed (stored in proxyIngressMbps)
      else if (sourceNode?.data?.kind === 'engine' && targetNode?.data?.kind === 'proxy') {
        baseBandwidth = sourceNode.data.proxyIngressMbps || 0
      }
      else {
        baseBandwidth = sourceNode?.data?.bandwidthMbps || 0
      }

      const jitterVal = (Math.random() - 0.5) * (baseBandwidth * 0.05)
      const nextBandwidth = Math.max(0, baseBandwidth + jitterVal)
      const failover = edge.style?.strokeDasharray !== undefined

      return {
        ...edge,
        data: {
          ...edge.data,
          bandwidthMbps: nextBandwidth,
        },
        style: {
          ...edge.style,
          strokeWidth: clamp(1.6 + nextBandwidth / (failover ? 58 : 46), 1.6, 8.6),
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
