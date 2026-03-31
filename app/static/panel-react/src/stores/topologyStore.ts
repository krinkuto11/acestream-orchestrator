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

const formatCompactId = (id: string): string => {
  if (!id) return 'unknown'
  return id.length > 12 ? id.slice(0, 12) : id
}

const inferTunnelFromEngine = (engine: EngineState, index: number): TunnelId => {
  const vpnName = String(engine.vpn_container || '').toLowerCase()
  if (vpnName.includes('2') || vpnName.includes('secondary')) {
    return 'vpn2'
  }
  if (vpnName.includes('1') || vpnName.includes('primary')) {
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

const buildSnapshot = ({
  engines,
  streams,
  vpnStatus,
  orchestratorStatus,
}: TopologyInputSnapshot): {
  nodes: Node<TopologyNodeData>[]
  edges: Edge[]
  summary: TopologySummary
  lastUpdated: string
  isMockMode: boolean
} => {
  const hasRealEngines = Boolean(engines && engines.length)
  const hasRealStreams = Boolean(streams && streams.length)
  const isMockMode = !hasRealEngines

  const workingEngines: EngineState[] = hasRealEngines
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

  const workingStreams: StreamState[] = hasRealStreams
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

  const tunnelConnectivity = deriveTunnelConnectivity(vpnStatus, hasRealEngines)
  const vpnDown = (Object.entries(tunnelConnectivity)
    .filter(([, connected]) => !connected)
    .map(([id]) => id)) as TunnelId[]

  const nodes: Node<TopologyNodeData>[] = []
  const edges: Edge[] = []

  const vpn1NodeId = 'vpn1'
  const vpn2NodeId = 'vpn2'
  const proxyNodeId = 'proxy-core'
  const clientNodeId = 'clients-edge'

  const failoverEngines: string[] = []

  nodes.push({
    id: vpn1NodeId,
    type: 'topologyNode',
    position: { x: 50, y: 150 },
    data: {
      kind: 'vpn',
      title: 'VPN Tunnel A',
      subtitle: vpnStatus?.vpn1?.container_name || 'gluetun-primary',
      health: tunnelConnectivity.vpn1 ? 'healthy' : 'down',
      bandwidthMbps: randomBetween(120, 210),
      streamCount: 0,
      metadata: {
        connected: tunnelConnectivity.vpn1,
        publicIp: vpnStatus?.vpn1?.public_ip || '185.102.112.44',
        provider: vpnStatus?.vpn1?.provider || 'ProtonVPN',
      },
    },
  })

  nodes.push({
    id: vpn2NodeId,
    type: 'topologyNode',
    position: { x: 50, y: 380 },
    data: {
      kind: 'vpn',
      title: 'VPN Tunnel B',
      subtitle: vpnStatus?.vpn2?.container_name || 'gluetun-secondary',
      health: tunnelConnectivity.vpn2 ? 'healthy' : 'down',
      bandwidthMbps: randomBetween(90, 180),
      streamCount: 0,
      failoverActive: !tunnelConnectivity.vpn1 && tunnelConnectivity.vpn2,
      metadata: {
        connected: tunnelConnectivity.vpn2,
        publicIp: vpnStatus?.vpn2?.public_ip || '79.127.210.63',
        provider: vpnStatus?.vpn2?.provider || 'Mullvad',
      },
    },
  })

  const columns = 4
  const startX = 350
  const startY = 80
  const spacingX = 300
  const spacingY = 220
  workingEngines.forEach((engine, index) => {
    const engineStreams = streamMap.get(engine.container_id) || []
    const assignedTunnel = inferTunnelFromEngine(engine, index)
    const tunnelHealthy = tunnelConnectivity[assignedTunnel]
    const backupTunnel = assignedTunnel === 'vpn1' ? 'vpn2' : 'vpn1'
    const backupHealthy = tunnelConnectivity[backupTunnel]

    const failoverActive = !tunnelHealthy && backupHealthy
    const sourceTunnel = failoverActive ? backupTunnel : assignedTunnel

    const streamCount = engineStreams.length
    const measuredMbps = engineStreams.reduce((sum, stream) => sum + toMbps(stream.speed_down), 0)
    const bandwidthMbps = measuredMbps > 0 ? measuredMbps : randomBetween(8, 72)

    let health: TopologyNodeHealth = 'healthy'
    if (engine.health_status === 'unhealthy' || (!tunnelHealthy && !backupHealthy)) {
      health = 'down'
    } else if (engine.health_status === 'unknown' || failoverActive) {
      health = 'degraded'
    }

    if (failoverActive) {
      failoverEngines.push(engine.container_id)
    }

    const row = Math.floor(index / columns)
    const col = index % columns

    nodes.push({
      id: engine.container_id,
      type: 'topologyNode',
      position: { x: startX + col * spacingX, y: startY + row * spacingY },
      data: {
        kind: 'engine',
        title: engine.container_name || formatCompactId(engine.container_id),
        subtitle: `${engine.host}:${engine.port}`,
        health,
        streamCount,
        bandwidthMbps,
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

    edges.push({
      id: `${sourceTunnel}->${engine.container_id}`,
      type: 'smoothstep',
      source: sourceTunnel,
      target: engine.container_id,
      animated: true,
      markerEnd: { type: MarkerType.ArrowClosed },
      data: {
        bandwidthMbps,
      },
      label: `${bandwidthMbps.toFixed(1)} Mbps`,
      labelStyle: {
        fontSize: 10,
        fill: failoverActive ? '#f59e0b' : '#94a3b8',
        fontWeight: 600,
      },
      style: {
        stroke: failoverActive ? '#f59e0b' : '#64748b',
        strokeWidth: clamp(1.6 + bandwidthMbps / 55, 1.6, 5.8),
        strokeDasharray: failoverActive ? '8 5' : undefined,
      },
    })

    edges.push({
      id: `${engine.container_id}->${proxyNodeId}`,
      type: 'smoothstep',
      source: engine.container_id,
      target: proxyNodeId,
      animated: true,
      markerEnd: { type: MarkerType.ArrowClosed },
      data: {
        bandwidthMbps,
      },
      label: `${bandwidthMbps.toFixed(1)} Mbps`,
      labelStyle: {
        fontSize: 10,
        fill: '#60a5fa',
      },
      style: {
        stroke: '#60a5fa',
        strokeWidth: clamp(1.8 + bandwidthMbps / 48, 1.8, 6.4),
      },
    })
  })

  const totalBandwidthMbps = workingEngines.reduce((sum, engine) => {
    const found = nodes.find((node) => node.id === engine.container_id)
    return sum + (found?.data?.bandwidthMbps || 0)
  }, 0)

  const activeStreams = orchestratorStatus?.streams?.active ?? workingStreams.length
  const activeClients = Math.max(activeStreams * 3, 9)

  nodes.push({
    id: proxyNodeId,
    type: 'topologyNode',
    position: { x: 1700, y: 240 },
    data: {
      kind: 'proxy',
      title: 'Mux and Proxy Core',
      subtitle: '/ace and /hls pipeline',
      health: orchestratorStatus?.status === 'healthy' ? 'healthy' : 'degraded',
      streamCount: activeStreams,
      bandwidthMbps: totalBandwidthMbps,
      metadata: {
        successRate: orchestratorStatus?.status === 'healthy' ? '99.6%' : '96.8%',
        ttfbP95Ms: failoverEngines.length > 0 ? 860 : 410,
      },
    },
  })

  nodes.push({
    id: clientNodeId,
    type: 'topologyNode',
    position: { x: 2100, y: 240 },
    data: {
      kind: 'client',
      title: 'Client Edge',
      subtitle: 'CDN and Player Sessions',
      health: 'healthy',
      streamCount: activeClients,
      bandwidthMbps: clamp(totalBandwidthMbps * 0.86, 32, 520),
      metadata: {
        activeClients,
        hlsSessions: Math.round(activeClients * 0.66),
        tsSessions: Math.round(activeClients * 0.34),
      },
    },
  })

  edges.push({
    id: `${proxyNodeId}->${clientNodeId}`,
    type: 'smoothstep',
    source: proxyNodeId,
    target: clientNodeId,
    animated: true,
    markerEnd: { type: MarkerType.ArrowClosed },
    label: `${Math.max(totalBandwidthMbps, 1).toFixed(1)} Mbps`,
    labelStyle: {
      fontSize: 10,
      fill: '#22c55e',
      fontWeight: 700,
    },
    style: {
      stroke: '#22c55e',
      strokeWidth: clamp(2.4 + totalBandwidthMbps / 40, 2.4, 9),
    },
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
    const next = buildSnapshot(snapshot)
    set((state) => ({
      nodes: next.nodes,
      edges: next.edges,
      summary: next.summary,
      lastUpdated: next.lastUpdated,
      isMockMode: next.isMockMode,
      selectedNodeId: state.selectedNodeId,
    }))
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
      const nextBandwidth = sourceNode?.data?.bandwidthMbps || 0
      const failover = edge.style?.strokeDasharray != null

      return {
        ...edge,
        label: `${nextBandwidth.toFixed(1)} Mbps`,
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

    set((prev) => ({
      nodes: nextNodes,
      edges: nextEdges,
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
