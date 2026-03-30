import { useEffect, useMemo } from 'react'
import ReactFlow, { Background, Controls, MarkerType, MiniMap } from 'reactflow'
import 'reactflow/dist/style.css'
import { Activity, AlertTriangle, Clock3, Network, ShieldAlert, Users } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { TopologyNode } from '@/components/topology/TopologyNode'
import { useTopologyStore } from '@/stores/topologyStore'

const nodeTypes = {
  topologyNode: TopologyNode,
}

const formatLastUpdate = (iso) => {
  if (!iso) return 'sync pending'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return 'sync pending'
  return date.toLocaleTimeString([], { hour12: false })
}

export function RoutingTopologyPage({ engines, streams, vpnStatus, orchestratorStatus, embedded = false }) {
  const {
    nodes,
    edges,
    summary,
    selectedNodeId,
    lastUpdated,
    isMockMode,
    initializeMock,
    hydrateFromBackend,
    simulateTick,
    setSelectedNode,
  } = useTopologyStore((state) => state)

  useEffect(() => {
    const hasBackendData =
      Boolean(engines && engines.length > 0) ||
      Boolean(streams && streams.length > 0) ||
      Boolean(vpnStatus) ||
      Boolean(orchestratorStatus)

    if (hasBackendData) {
      hydrateFromBackend({
        engines,
        streams,
        vpnStatus,
        orchestratorStatus,
      })
      return
    }

    initializeMock()
  }, [engines, streams, vpnStatus, orchestratorStatus, hydrateFromBackend, initializeMock])

  useEffect(() => {
    const interval = window.setInterval(() => {
      simulateTick()
    }, 1000)

    return () => {
      window.clearInterval(interval)
    }
  }, [simulateTick])

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) || null,
    [nodes, selectedNodeId],
  )

  const hasEmergency =
    summary.vpnDown.length > 0 ||
    Boolean(
      orchestratorStatus?.provisioning?.circuit_breaker_state &&
        orchestratorStatus.provisioning.circuit_breaker_state !== 'closed',
    )

  const fallbackReason =
    orchestratorStatus?.provisioning?.blocked_reason_details?.message ||
    'Routing is currently in failover mode. Monitor tunnel health and stream delivery latency.'

  return (
    <div className="space-y-4">
      {!embedded && (
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-2xl font-semibold tracking-tight">Routing Topology</h2>
            <p className="text-sm text-muted-foreground">
              Live traffic graph for VPN tunnels, engines, proxy core, and client edge paths.
            </p>
          </div>

          <div className="flex items-center gap-2 text-xs">
            {isMockMode ? (
              <Badge variant="warning">Demo Signal</Badge>
            ) : (
              <Badge variant="success">Live Signal</Badge>
            )}
            <Badge variant={summary.vpnDown.length ? 'destructive' : 'success'}>
              {summary.vpnDown.length ? 'VPN Incident' : 'VPN Stable'}
            </Badge>
            <div className="rounded-md border border-border bg-card px-2 py-1 text-muted-foreground">
              Updated {formatLastUpdate(lastUpdated)}
            </div>
          </div>
        </div>
      )}

      {hasEmergency && (
        <Alert variant={summary.vpnDown.length ? 'destructive' : 'warning'}>
          <ShieldAlert className="h-4 w-4" />
          <AlertTitle>Active emergency routing</AlertTitle>
          <AlertDescription>
            {fallbackReason}
            {summary.vpnDown.length > 0 ? ` Down tunnel(s): ${summary.vpnDown.join(', ')}` : ''}
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
        <Card className="border-sky-500/30 bg-sky-500/5">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Global Egress</CardTitle>
          </CardHeader>
          <CardContent className="flex items-center justify-between">
            <p className="text-2xl font-bold">{summary.totalBandwidthMbps.toFixed(1)} Mbps</p>
            <Activity className="h-5 w-5 text-sky-400" />
          </CardContent>
        </Card>

        <Card className="border-cyan-500/30 bg-cyan-500/5">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Active Engines</CardTitle>
          </CardHeader>
          <CardContent className="flex items-center justify-between">
            <p className="text-2xl font-bold">{summary.activeEngines}</p>
            <Network className="h-5 w-5 text-cyan-400" />
          </CardContent>
        </Card>

        <Card className="border-emerald-500/30 bg-emerald-500/5">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Active Streams</CardTitle>
          </CardHeader>
          <CardContent className="flex items-center justify-between">
            <p className="text-2xl font-bold">{summary.activeStreams}</p>
            <Users className="h-5 w-5 text-emerald-400" />
          </CardContent>
        </Card>

        <Card className="border-amber-500/30 bg-amber-500/5">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium">Failover Paths</CardTitle>
          </CardHeader>
          <CardContent className="flex items-center justify-between">
            <p className="text-2xl font-bold">{summary.failoverEngines}</p>
            <AlertTriangle className="h-5 w-5 text-amber-400" />
          </CardContent>
        </Card>
      </div>

      <div className="grid gap-4 xl:grid-cols-[1fr_300px]">
        <Card className="overflow-hidden border-slate-700/60 bg-slate-950/70">
          <CardHeader className="border-b border-slate-700/60 pb-3">
            <CardTitle className="text-sm font-medium text-slate-200">
              VPN to Engine to Proxy flow map
            </CardTitle>
          </CardHeader>

          <CardContent className="p-0">
            <div className="h-[680px] w-full bg-[radial-gradient(circle_at_15%_10%,rgba(59,130,246,0.12),transparent_35%),radial-gradient(circle_at_85%_90%,rgba(16,185,129,0.14),transparent_40%),linear-gradient(180deg,rgba(15,23,42,0.75),rgba(2,6,23,0.9))]">
              <ReactFlow
                nodes={nodes}
                edges={edges}
                nodeTypes={nodeTypes}
                fitView
                fitViewOptions={{ padding: 0.2 }}
                minZoom={0.3}
                maxZoom={1.5}
                onNodeClick={(_, node) => setSelectedNode(node.id)}
                onPaneClick={() => setSelectedNode(null)}
                proOptions={{ hideAttribution: true }}
                defaultEdgeOptions={{
                  markerEnd: { type: MarkerType.ArrowClosed },
                }}
                nodesDraggable={false}
                elementsSelectable
              >
                <MiniMap
                  nodeStrokeWidth={2}
                  pannable
                  zoomable
                  className="!bg-slate-950/90"
                  nodeColor={(node) => {
                    const health = node.data?.health
                    if (health === 'down') return '#f43f5e'
                    if (health === 'degraded') return '#f59e0b'
                    return '#22c55e'
                  }}
                />
                <Controls className="!bg-slate-950 !text-slate-200" />
                <Background gap={22} size={1} color="rgba(148,163,184,0.2)" />
              </ReactFlow>
            </div>
          </CardContent>
        </Card>

        <Card className="border-slate-700/60 bg-slate-950/75">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-medium text-slate-200">Node inspector</CardTitle>
          </CardHeader>

          <CardContent className="space-y-3">
            {!selectedNode ? (
              <div className="space-y-2 rounded-md border border-slate-700/70 bg-slate-900/40 p-3 text-sm text-slate-300">
                <p>Select a node to inspect path details, tunnel assignment, and live metrics.</p>
                <Separator className="bg-slate-700" />
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <Clock3 className="h-3.5 w-3.5" />
                  <span>Click any VPN, engine, proxy, or client node</span>
                </div>
              </div>
            ) : (
              <>
                <div className="rounded-md border border-slate-700/70 bg-slate-900/45 p-3">
                  <p className="text-xs uppercase tracking-wide text-slate-400">Node</p>
                  <p className="text-base font-semibold text-slate-50">{selectedNode.data?.title}</p>
                  <p className="text-xs text-slate-400">{selectedNode.data?.subtitle}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <Badge variant={selectedNode.data?.health === 'down' ? 'destructive' : selectedNode.data?.health === 'degraded' ? 'warning' : 'success'}>
                      {selectedNode.data?.health}
                    </Badge>
                    {selectedNode.data?.failoverActive && <Badge variant="warning">Failover</Badge>}
                  </div>
                </div>

                <div className="rounded-md border border-slate-700/70 bg-slate-900/45 p-3">
                  <p className="text-xs uppercase tracking-wide text-slate-400">Live metrics</p>
                  <div className="mt-2 grid grid-cols-2 gap-2 text-sm">
                    <div>
                      <p className="text-slate-400">Bandwidth</p>
                      <p className="font-semibold text-slate-100">
                        {selectedNode.data?.bandwidthMbps.toFixed(1)} Mbps
                      </p>
                    </div>
                    <div>
                      <p className="text-slate-400">Streams</p>
                      <p className="font-semibold text-slate-100">{selectedNode.data?.streamCount}</p>
                    </div>
                  </div>
                </div>

                <div className="rounded-md border border-slate-700/70 bg-slate-900/45 p-3">
                  <p className="text-xs uppercase tracking-wide text-slate-400">Metadata</p>
                  <div className="mt-2 space-y-1 text-xs text-slate-200">
                    {Object.entries(selectedNode.data?.metadata || {}).map(([key, value]) => (
                      <div key={key} className="flex items-center justify-between gap-2">
                        <span className="text-slate-400">{key}</span>
                        <span className="font-medium text-right">{String(value)}</span>
                      </div>
                    ))}
                  </div>
                </div>
              </>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
