import { create } from 'zustand'
import { MarkerType, type Edge, type Node } from 'reactflow'
import type {
  EngineState,
  OrchestratorStatusResponse,
  StreamState,
  VpnStatusPayload,
} from '@/types/orchestrator'

export type TunnelId = string
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
  lifecycle?: 'active' | 'draining'
  forwarded?: boolean
  failoverActive?: boolean
  metadata?: Record<string, string | number | boolean | null>
}

export interface TopologySummary {
  totalBandwidthMbps: number
  activeEngines: number
  activeStreams: number
  activeClients: number
  failoverEngines: number
  vpnDown: string[]
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
const EDGE_FLOW_ACTIVATE_THRESHOLD_Mbps = 0.12
const EDGE_FLOW_DEACTIVATE_THRESHOLD_Mbps = 0.05

type NodeFlowTarget = {
  bandwidthMbps: number
  uploadMbps?: number
  proxyIngressMbps?: number
}

type EdgeFlowTarget = {
  bandwidthMbps: number
  uploadMbps?: number
}

const shouldBypassEdgeSmoothing = (
  sourceKind: TopologyNodeKind | undefined,
  targetKind: TopologyNodeKind | undefined,
): boolean => {
  return (
    (sourceKind === 'vpn' && targetKind === 'engine') ||
    (sourceKind === 'engine' && targetKind === 'proxy') ||
    (sourceKind === 'proxy' && targetKind === 'client')
  )
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

const shouldBypassNodeSmoothing = (kind: TopologyNodeKind | undefined): boolean => {
  return kind === 'vpn' || kind === 'proxy'
}

const resolveEdgeFlowActive = (
  previousActive: boolean,
  currentTotalMbps: number,
): boolean => {
  if (previousActive) {
    return currentTotalMbps > EDGE_FLOW_DEACTIVATE_THRESHOLD_Mbps
  }
  return currentTotalMbps > EDGE_FLOW_ACTIVATE_THRESHOLD_Mbps
}


const formatCompactId = (id: string): string => {
  if (!id) return 'unknown'
  return id.length > 12 ? id.slice(0, 12) : id
}

type VpnNodeDescriptor = {
  id: string
  title: string
  subtitle: string
  connected: boolean
  lifecycle: 'active' | 'draining'
  publicIp: string | null
  provider: string | null
  country: string | null
}

const normalizeLifecycle = (value: unknown): 'active' | 'draining' => {
  return String(value || '').trim().toLowerCase() === 'draining' ? 'draining' : 'active'
}

const isTruthyConnection = (value: unknown): boolean => {
  if (typeof value === 'boolean') return value
  const normalized = String(value || '').trim().toLowerCase()
  return normalized === 'true' || normalized === 'running' || normalized === 'healthy' || normalized === 'ready'
}

const stableTunnelFromEngineIdentity = (engine: EngineState, tunnelIds: string[]): TunnelId => {
  const fallbackIds = tunnelIds.length > 0 ? tunnelIds : ['vpn1', 'vpn2']
  const identity = `${engine.container_id || ''}:${engine.container_name || ''}`
  let hash = 0
  for (let i = 0; i < identity.length; i += 1) {
    hash = ((hash * 31) + identity.charCodeAt(i)) >>> 0
  }
  return fallbackIds[hash % fallbackIds.length]
}

const inferTunnelFromEngine = (
  engine: EngineState,
  index: number,
  knownTunnelIds: string[],
  vpnStatus?: VpnStatusPayload | null,
): TunnelId => {
  const raw = String(engine.vpn_container || '').trim()
  const vpnName = raw.toLowerCase()

  const exactMatch = knownTunnelIds.find((id) => id.toLowerCase() === vpnName)
  if (exactMatch) return exactMatch

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
  if (vpn1Candidates.includes(vpnName) && knownTunnelIds.length > 0) return knownTunnelIds[0]
  if (vpn2Candidates.includes(vpnName) && knownTunnelIds.length > 1) return knownTunnelIds[1]

  // Fallback heuristics for legacy/custom names.
  if ((vpnName.includes('secondary') || vpnName.includes('backup') || vpnName.includes('vpn2')) && knownTunnelIds.length > 1) {
    return knownTunnelIds[1]
  }
  if ((vpnName.includes('primary') || vpnName.includes('main') || vpnName.includes('vpn1')) && knownTunnelIds.length > 0) {
    return knownTunnelIds[0]
  }

  // Use index as entropy to spread unassigned engines in larger dynamic pools.
  const stable = stableTunnelFromEngineIdentity(engine, knownTunnelIds)
  if (knownTunnelIds.length === 0) return stable
  const stableIndex = knownTunnelIds.indexOf(stable)
  if (stableIndex === -1) return knownTunnelIds[index % knownTunnelIds.length]
  return knownTunnelIds[(stableIndex + index) % knownTunnelIds.length]
}

const extractVpnNodes = (
  vpnStatus: VpnStatusPayload | null | undefined,
  orchestratorStatus: OrchestratorStatusResponse | null | undefined,
  engines: EngineState[],
  isMockMode: boolean,
): VpnNodeDescriptor[] => {
  const nodes: VpnNodeDescriptor[] = []
  const seen = new Set<string>()

  const hasVpnIdentity = (rawNode: Record<string, unknown>): boolean => {
    const identity = rawNode.container_name || rawNode.container || rawNode.name || rawNode.id
    return Boolean(String(identity || '').trim())
  }

  const hasMeaningfulSignals = (rawNode: Record<string, unknown>): boolean => {
    const signalKeys = [
      'connected',
      'healthy',
      'condition',
      'status',
      'lifecycle',
      'public_ip',
      'provider',
      'country',
    ]

    return signalKeys.some((key) => {
      const value = rawNode[key]
      if (value == null) return false
      if (typeof value === 'string') return value.trim().length > 0
      return true
    })
  }

  const upsert = (rawNode: Record<string, unknown>, indexHint: number) => {
    const name = String(rawNode.container_name || rawNode.container || rawNode.name || rawNode.id || '').trim()
    if (!name || seen.has(name)) return

    seen.add(name)
    const connected = isTruthyConnection(rawNode.connected ?? rawNode.healthy ?? rawNode.condition ?? rawNode.status)
    const provider = rawNode.provider == null ? null : String(rawNode.provider)
    const country = rawNode.country == null ? null : String(rawNode.country)

    nodes.push({
      id: name,
      title: `VPN ${indexHint + 1}`,
      subtitle: name,
      connected,
      lifecycle: normalizeLifecycle(rawNode.lifecycle),
      publicIp: rawNode.public_ip == null ? null : String(rawNode.public_ip),
      provider,
      country,
    })
  }

  const vpnStatusAny = (vpnStatus || {}) as Record<string, unknown>
  const orchestratorAny = (orchestratorStatus || {}) as Record<string, unknown>

  const explicitNodeLists = [
    vpnStatusAny.vpn_nodes,
    vpnStatusAny.nodes,
    (orchestratorAny.vpn as Record<string, unknown> | undefined)?.nodes,
    orchestratorAny.vpn_nodes,
  ]

  for (const source of explicitNodeLists) {
    if (!Array.isArray(source)) continue
    source.forEach((rawNode, idx) => {
      if (rawNode && typeof rawNode === 'object') {
        upsert(rawNode as Record<string, unknown>, nodes.length + idx)
      }
    })
  }

  if (nodes.length === 0 && vpnStatus && vpnStatus.mode !== 'disabled') {
    const legacyNodes = [vpnStatus.vpn1, vpnStatus.vpn2].filter(Boolean)
    legacyNodes.forEach((legacyNode, idx) => {
      const legacyRaw = legacyNode as Record<string, unknown>
      if (!hasVpnIdentity(legacyRaw) && !hasMeaningfulSignals(legacyRaw)) {
        return
      }

      upsert(
        {
          container_name: legacyNode?.container_name || legacyNode?.container,
          connected: legacyNode?.connected,
          lifecycle: (legacyNode as Record<string, unknown>)?.lifecycle,
          public_ip: legacyNode?.public_ip,
          provider: legacyNode?.provider,
          country: legacyNode?.country,
        },
        idx,
      )
    })
  }

  if (nodes.length === 0 && (!vpnStatus || vpnStatus.mode !== 'disabled')) {
    const vpnNames = Array.from(
      new Set(
        engines
          .map((engine) => String(engine.vpn_container || '').trim())
          .filter(Boolean),
      ),
    )
    vpnNames.forEach((name, idx) => {
      upsert({ container_name: name, connected: true, lifecycle: 'active' }, idx)
    })
  }

  if (nodes.length === 0 && isMockMode) {
    upsert({ container_name: 'vpn1', connected: false, lifecycle: 'active' }, 0)
    upsert({ container_name: 'vpn2', connected: true, lifecycle: 'active' }, 1)
  }

  return nodes
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
  const isMockMode = false

  const workingEngines: EngineState[] = engines || []

  const workingStreams: StreamState[] = streams || []

  const streamMap = new Map<string, StreamState[]>()
  for (const stream of workingStreams) {
    const entry = streamMap.get(stream.container_id) || []
    entry.push(stream)
    streamMap.set(stream.container_id, entry)
  }

  const nodes: Node<TopologyNodeData>[] = []
  const edges: Edge[] = []
  const internetNodeId = 'internet'
  const proxyNodeId = 'proxy-core'
  const vpnStatusAny = (vpnStatus || {}) as Record<string, unknown>
  const directPublicIp = String(vpnStatusAny.public_ip || '').trim()

  const failoverEngines: string[] = []
  const previousEdgeFlowById = new Map<string, boolean>()

  if (prevState) {
    prevState.edges.forEach((edge) => {
      previousEdgeFlowById.set(edge.id, edge.data?.flowActive === true)
    })
  }

  const vpnNodes = extractVpnNodes(vpnStatus, orchestratorStatus, workingEngines, isMockMode)
  const tunnelConnectivity: Record<string, boolean> = {}
  const vpnLifecycleByTunnel: Record<string, 'active' | 'draining'> = {}
  for (const node of vpnNodes) {
    tunnelConnectivity[node.id] = node.connected
    vpnLifecycleByTunnel[node.id] = node.lifecycle
  }

  const isVpnDisabledMode = vpnNodes.length === 0
  const tunnelOrder = isVpnDisabledMode ? [internetNodeId] : vpnNodes.map((node) => node.id)
  const vpnDown = Object.entries(tunnelConnectivity)
    .filter(([, connected]) => !connected)
    .map(([id]) => id)

  const previousEngineOrder = new Map<string, number>()
  if (prevState) {
    prevState.nodes
      .filter((node) => node.data?.kind === 'engine')
      .forEach((node, index) => {
        previousEngineOrder.set(node.id, index)
      })
  }

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
  })

  // Keep engine placement stable across updates to prevent pipes appearing to jump between engines.
  engineStats.sort((a, b) => {
    const aPrev = previousEngineOrder.get(a.engine.container_id)
    const bPrev = previousEngineOrder.get(b.engine.container_id)

    if (aPrev !== undefined && bPrev !== undefined) return aPrev - bPrev
    if (aPrev !== undefined) return -1
    if (bPrev !== undefined) return 1

    const aName = a.engine.container_name || a.engine.container_id || ''
    const bName = b.engine.container_name || b.engine.container_id || ''
    return aName.localeCompare(bName)
  })

  const isVpnClusterMode = !isVpnDisabledMode
  const knownTunnelIds = isVpnDisabledMode ? [] : tunnelOrder
  const engineStatsWithTunnel = engineStats.map((entry, index) => ({
    ...entry,
    assignedTunnel: isVpnDisabledMode
      ? internetNodeId
      : inferTunnelFromEngine(entry.engine, index, knownTunnelIds, vpnStatus),
  }))

  const normalizedEngineStats = engineStatsWithTunnel.map((entry, index) => {
    if (isVpnDisabledMode) return entry
    const assignedTunnel = knownTunnelIds.includes(entry.assignedTunnel)
      ? entry.assignedTunnel
      : knownTunnelIds[index % knownTunnelIds.length]
    return {
      ...entry,
      assignedTunnel,
    }
  })

  const NUM_COLUMNS = isVpnClusterMode
    ? Math.max(1, Math.min(2, Math.ceil(engineStats.length / 8)))
    : Math.max(1, Math.min(3, Math.ceil(engineStats.length / 6)))
  const COLUMN_SPACING_X = 340
  const STAGGERED_ROW_SPACING_Y = 140
  const CLUSTER_GAP_Y = 220

  const engineStartX = 350
  const engineStartY = 80

  const enginesPerTunnel = normalizedEngineStats.reduce(
    (acc, item) => {
      acc[item.assignedTunnel] = (acc[item.assignedTunnel] || 0) + 1
      return acc
    },
    {} as Record<string, number>,
  )

  const tunnelLayout = new Map<string, { startY: number; centerY: number; cols: number; rows: number; height: number }>()
  let tunnelCursorY = engineStartY

  for (const tunnelId of tunnelOrder) {
    const tunnelEngineCount = Math.max(0, Number(enginesPerTunnel[tunnelId] || 0))
    const height = Math.max(0, tunnelEngineCount - 1) * STAGGERED_ROW_SPACING_Y

    tunnelLayout.set(tunnelId, {
      startY: tunnelCursorY,
      centerY: tunnelCursorY + (height / 2),
      cols: NUM_COLUMNS,
      rows: tunnelEngineCount,
      height,
    })

    tunnelCursorY += height + (isVpnClusterMode ? CLUSTER_GAP_Y : 0)
  }

  const tunnelSpan = Math.max(
    0,
    tunnelCursorY - engineStartY - (isVpnClusterMode ? CLUSTER_GAP_Y : 0),
  )
  const centerY = engineStartY + (tunnelSpan / 2)

  const tunnelLocalIndex: Record<string, number> = {}

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
        lifecycle: 'active',
        metadata: {
          connected: true,
          publicIp: directPublicIp || 'Unavailable',
          provider: 'WAN',
          country: null,
        },
      },
    })
  } else {
    vpnNodes.forEach((vpnNode, idx) => {
      const layout = tunnelLayout.get(vpnNode.id)
      const health: TopologyNodeHealth = !vpnNode.connected
        ? 'down'
        : vpnNode.lifecycle === 'draining'
          ? 'degraded'
          : 'healthy'

      nodes.push({
        id: vpnNode.id,
        type: 'topologyNode',
        position: { x: -240, y: layout?.centerY ?? (engineStartY + (idx * CLUSTER_GAP_Y)) },
        data: {
          kind: 'vpn',
          title: vpnNode.title,
          subtitle: vpnNode.subtitle,
          health,
          bandwidthMbps: isMockMode ? randomBetween(90, 210) : 0,
          streamCount: 0,
          lifecycle: vpnNode.lifecycle,
          metadata: {
            connected: vpnNode.connected,
            publicIp: vpnNode.publicIp,
            provider: vpnNode.provider,
            country: vpnNode.country,
            lifecycle: vpnNode.lifecycle,
          },
        },
      })
    })
  }

  // 3. Process engine nodes with stable staggered lanes
  normalizedEngineStats.forEach(({ engine, streamCount, streamMeasuredDownMbps, measuredDownMbps, measuredUpMbps, assignedTunnel }, index) => {
    const engineStreams = streamMap.get(engine.container_id) || []
    const monitorStreamCount = Math.max(
      0,
      Number(
        engine.monitor_stream_count ??
          ((engine.stream_count ?? engineStreams.length) - engineStreams.length),
      ),
    )
    const hasMonitoringSession = monitorStreamCount > 0

    const localIndex = tunnelLocalIndex[assignedTunnel] || 0
    tunnelLocalIndex[assignedTunnel] = localIndex + 1

    const cluster = tunnelLayout.get(assignedTunnel)
    const colIndex = localIndex % NUM_COLUMNS
    const currentX = engineStartX + (colIndex * COLUMN_SPACING_X)
    const currentY = (cluster?.startY ?? engineStartY) + (localIndex * STAGGERED_ROW_SPACING_Y)

    const tunnelHealthy = isVpnDisabledMode ? true : Boolean(tunnelConnectivity[assignedTunnel])
    const backupTunnel = !isVpnDisabledMode
      ? tunnelOrder.find((tunnelId) => tunnelId !== assignedTunnel && Boolean(tunnelConnectivity[tunnelId]))
      : undefined

    const failoverActive = !isVpnDisabledMode && !tunnelHealthy && Boolean(backupTunnel)
    const sourceNodeId: TunnelId | 'internet' = isVpnDisabledMode
      ? internetNodeId
      : (failoverActive ? (backupTunnel as string) : assignedTunnel)

    const engineLifecycle = normalizeLifecycle(
      (engine as Record<string, unknown>)?.lifecycle
      ?? (engine as Record<string, unknown>)?.vpn_lifecycle
      ?? (engine.labels || {})['acestream.lifecycle']
      ?? vpnLifecycleByTunnel[assignedTunnel],
    )

    // VPN → Engine: P2P download speed.
    // Keep the route visibly active during monitor-only sessions even if Ace reports 0 throughput.
    const monitoringFloorMbps = hasMonitoringSession && measuredDownMbps <= 0
      ? Math.max(0.3, monitorStreamCount * 0.3)
      : 0
    const bandwidthMbps = measuredDownMbps > 0
      ? measuredDownMbps
      : (monitoringFloorMbps > 0 ? monitoringFloorMbps : (isMockMode ? randomBetween(8, 72) : 0))
    
    // Engine → Proxy: actual per-engine ingress from proxy metrics when available.
    // If the per-engine ingress map exists, treat missing/zero as zero (authoritative)
    // so ended routes are deactivated promptly instead of falling back to stream stats.
    const ingressByEngine = orchestratorStatus?.proxy?.engine_ingress_bps
    const hasIngressMap = Boolean(ingressByEngine && typeof ingressByEngine === 'object')
    const engineIngressBps = hasIngressMap
      ? Number((ingressByEngine as Record<string, number | undefined>)[engine.container_id] ?? 0)
      : undefined
    const proxyIngressMbps = typeof engineIngressBps === 'number'
      ? (engineIngressBps > 0 ? (engineIngressBps * 8) / 1_000_000 : 0)
      : (streamCount > 0 ? streamMeasuredDownMbps * 0.98 : (isMockMode ? randomBetween(2, 18) : 0))
    
    let health: TopologyNodeHealth = 'healthy'
    if (engine.health_status === 'unhealthy' || (!tunnelHealthy && !backupTunnel)) {
      health = 'down'
    } else if (engine.health_status === 'unknown' || failoverActive || engineLifecycle === 'draining') {
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
        vpnTunnel: isVpnDisabledMode ? undefined : assignedTunnel,
        lifecycle: engineLifecycle,
        forwarded: Boolean(engine.forwarded),
        failoverActive,
        metadata: {
          assignedTunnel,
          activeTunnel: sourceNodeId,
          peers: engineStreams.reduce((sum, stream) => sum + (stream.peers || 0), 0),
          monitorStreamCount,
          lifecycle: engineLifecycle,
          variant: engine.engine_variant || 'default',
          forwarded: engine.forwarded,
          forwardedPort: engine.forwarded_port || null,
        },
      },
    })

    // VPN → Engine edge: shows both download (P2P ingress) and upload (P2P seeding) bandwidth
    const edgeUploadBw = measuredUpMbps > 0 ? measuredUpMbps : (isMockMode ? randomBetween(2, 12) : 0)
    const edgeIsDraining = engineLifecycle === 'draining' || vpnLifecycleByTunnel[sourceNodeId] === 'draining'
    const vpnEngineEdgeId = `${sourceNodeId}->${engine.container_id}`
    const vpnEngineFlowSignal = bandwidthMbps + edgeUploadBw
    const vpnEngineFlowActive = resolveEdgeFlowActive(
      previousEdgeFlowById.get(vpnEngineEdgeId) ?? false,
      vpnEngineFlowSignal,
    )
    edges.push({
      id: vpnEngineEdgeId,
      type: 'topologyEdge',
      source: sourceNodeId,
      target: engine.container_id,
      animated: false,
      markerEnd: { type: MarkerType.ArrowClosed },
      data: {
        bandwidthMbps: bandwidthMbps,
        uploadMbps: edgeUploadBw,
        labelPosition: 'near-target',
        monitoringActive: hasMonitoringSession,
        drainingRoute: edgeIsDraining,
        flowActive: vpnEngineFlowActive,
      },
      style: {
        stroke: (failoverActive || hasMonitoringSession || edgeIsDraining) ? '#f59e0b' : '#64748b',
        strokeWidth: clamp(1.6 + bandwidthMbps / 55, 1.6, 5.8),
        strokeDasharray: (failoverActive || edgeIsDraining) ? '8 5' : undefined,
      },
    })

    // Engine → Proxy edge: shows upload bandwidth (proxy ingress)
    const proxyRouteDraining = engineLifecycle === 'draining'
    const engineProxyEdgeId = `${engine.container_id}->${proxyNodeId}`
    const engineProxyFlowActive = resolveEdgeFlowActive(
      previousEdgeFlowById.get(engineProxyEdgeId) ?? false,
      proxyIngressMbps,
    )
    edges.push({
      id: engineProxyEdgeId,
      type: 'topologyEdge',
      source: engine.container_id,
      target: proxyNodeId,
      animated: false,
      markerEnd: { type: MarkerType.ArrowClosed },
      data: {
        bandwidthMbps: proxyIngressMbps,
        labelPosition: 'near-source',
        drainingRoute: proxyRouteDraining,
        flowActive: engineProxyFlowActive,
      },
      style: {
        stroke: proxyRouteDraining ? '#f59e0b' : '#60a5fa',
        strokeWidth: clamp(1.8 + proxyIngressMbps / 48, 1.8, 6.4),
        strokeDasharray: proxyRouteDraining ? '8 5' : undefined,
      },
    })
  })

  if (!isMockMode && !isVpnDisabledMode) {
    for (const tunnelId of tunnelOrder) {
      const vpnNode = nodes.find((node) => node.id === tunnelId)
      if (!vpnNode) continue
      const vpnEngines = nodes.filter((node) => {
        if (node.data.kind !== 'engine') return false
        return String(node.data.metadata?.assignedTunnel || '') === tunnelId
      })
      vpnNode.data.bandwidthMbps = vpnEngines.reduce((sum, node) => sum + (node.data.bandwidthMbps || 0), 0)
      vpnNode.data.uploadMbps = vpnEngines.reduce((sum, node) => sum + (node.data.uploadMbps || 0), 0)
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
  const clientList = orchestratorStatus?.proxy?.active_clients?.list || []
  const activeClients = orchestratorStatus?.proxy?.active_clients?.total ?? clientList.length

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
    
    // Treat zero-bitrate clients as inactive to avoid lingering proxy->client pipes.
    const clientIsActive = rawClientBw > 0.05
    const clientBwMbps = smoothBandwidth(rawClientBw, prevClientNode?.data?.bandwidthMbps, clientIsActive)

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
      animated: false,
      markerEnd: { type: MarkerType.ArrowClosed },
      data: {
        bandwidthMbps: clientBwMbps,
        protocol: client.type,
        flowActive: resolveEdgeFlowActive(
          previousEdgeFlowById.get(`${proxyNodeId}->${cNodeId}`) ?? false,
          rawClientBw,
        ),
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
  isMockMode: false,

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
        const bypassSmoothing = shouldBypassNodeSmoothing(nextNode.data?.kind)
        const targetBandwidth = bypassSmoothing
          ? (nextNode.data?.bandwidthMbps ?? 0)
          : applyDeadband(existingNode?.data?.bandwidthMbps, nextNode.data?.bandwidthMbps)
        const targetUpload = bypassSmoothing
          ? nextNode.data?.uploadMbps
          : applyDeadband(existingNode?.data?.uploadMbps, nextNode.data?.uploadMbps)
        const targetProxyIngress = bypassSmoothing
          ? nextNode.data?.proxyIngressMbps
          : applyDeadband(
            existingNode?.data?.proxyIngressMbps,
            nextNode.data?.proxyIngressMbps,
          )

        nodeInterpolationTargets.set(nextNode.id, {
          bandwidthMbps: targetBandwidth ?? 0,
          uploadMbps: targetUpload,
          proxyIngressMbps: targetProxyIngress,
        })

        if (bypassSmoothing) {
          return {
            ...nextNode,
            data: {
              ...nextNode.data,
              bandwidthMbps: targetBandwidth ?? 0,
              ...(typeof targetUpload === 'number' ? { uploadMbps: targetUpload } : {}),
              ...(typeof targetProxyIngress === 'number' ? { proxyIngressMbps: targetProxyIngress } : {}),
            },
          }
        }

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

      const nextNodeKinds = new Map(
        next.nodes.map((node) => [node.id, node.data?.kind as TopologyNodeKind | undefined]),
      )

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

        const sourceKind = nextNodeKinds.get(nextEdge.source)
        const targetKind = nextNodeKinds.get(nextEdge.target)
        const bypassSmoothing = shouldBypassEdgeSmoothing(sourceKind, targetKind)

        if (bypassSmoothing) {
          edgeInterpolationTargets.set(nextEdge.id, {
            bandwidthMbps: nextEdgeTargetBandwidth,
            uploadMbps: nextEdgeTargetUpload,
          })

          return {
            ...nextEdge,
            data: {
              ...nextEdge.data,
              bandwidthMbps: nextEdgeTargetBandwidth,
              ...(typeof nextEdgeTargetUpload === 'number' ? { uploadMbps: nextEdgeTargetUpload } : {}),
            },
          }
        }

        edgeInterpolationTargets.set(nextEdge.id, {
          bandwidthMbps: applyDeadband(existingEdgeBandwidth, nextEdgeTargetBandwidth) ?? 0,
          uploadMbps: applyDeadband(existingEdgeUpload, nextEdgeTargetUpload),
        })

        if (!existingEdge) return nextEdge
        
        const styleChanged = JSON.stringify(existingEdge.style) !== JSON.stringify(nextEdge.style)
        const labelChanged = existingEdge.label !== nextEdge.label
        const flowStateChanged = (existingEdge.data?.flowActive === true) !== (nextEdge.data?.flowActive === true)
        
        if (!styleChanged && !labelChanged && !flowStateChanged) return existingEdge

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

      const bypassSmoothing = shouldBypassNodeSmoothing(data.kind)
      const nextBandwidth = bypassSmoothing
        ? (flowTarget.bandwidthMbps || 0)
        : interpolateNumber(data.bandwidthMbps || 0, flowTarget.bandwidthMbps || 0)
      const nextUpload =
        typeof data.uploadMbps === 'number' || typeof flowTarget.uploadMbps === 'number'
          ? (bypassSmoothing
            ? (flowTarget.uploadMbps ?? 0)
            : interpolateNumber(data.uploadMbps ?? 0, flowTarget.uploadMbps ?? 0))
          : undefined
      const nextProxyIngress =
        typeof data.proxyIngressMbps === 'number' || typeof flowTarget.proxyIngressMbps === 'number'
          ? (bypassSmoothing
            ? (flowTarget.proxyIngressMbps ?? 0)
            : interpolateNumber(data.proxyIngressMbps ?? 0, flowTarget.proxyIngressMbps ?? 0))
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
        } else if (sourceNode?.data?.kind === 'proxy' && targetNode?.data?.kind === 'client') {
          targetBandwidth = targetNode?.data?.bandwidthMbps || 0
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
      const failover = edge.style?.strokeDasharray !== undefined

      const sourceKind = sourceNode?.data?.kind
      const targetKind = targetNode?.data?.kind
      const bypassSmoothing = shouldBypassEdgeSmoothing(sourceKind, targetKind)

      const nextBandwidth = bypassSmoothing
        ? targetBandwidth
        : interpolateNumber(currentBandwidth, targetBandwidth)
      const nextUpload =
        typeof edge.data?.uploadMbps === 'number' || typeof targetUpload === 'number'
          ? (bypassSmoothing
            ? (targetUpload ?? 0)
            : interpolateNumber(edge.data?.uploadMbps ?? 0, targetUpload ?? 0))
          : undefined

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
