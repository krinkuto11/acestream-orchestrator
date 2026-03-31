import { useEffect, useMemo } from 'react'
import ReactFlow, { Background, Controls, MarkerType } from 'reactflow'
import 'reactflow/dist/style.css'
import { Activity, AlertTriangle, Clock3, Network, ShieldAlert, Users } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Separator } from '@/components/ui/separator'
import { TopologyNode } from '@/components/topology/TopologyNode'
import { TopologyEdge } from '@/components/topology/TopologyEdge'
import { useTopologyStore } from '@/stores/topologyStore'
import { cn } from '@/lib/utils'

const nodeTypes = {
  topologyNode: TopologyNode,
}

const edgeTypes = {
  topologyEdge: TopologyEdge,
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

  return (
    <div className={cn(
      "relative w-full overflow-hidden rounded-xl border border-slate-800 bg-slate-950 shadow-2xl transition-all duration-500 flex flex-col",
      embedded ? "h-[740px]" : "h-screen"
    )}>
      {/* Background Ambience Overlay */}
      <div className="absolute inset-0 z-0 bg-[radial-gradient(circle_at_15%_10%,rgba(59,130,246,0.12),transparent_35%),radial-gradient(circle_at_85%_90%,rgba(16,185,129,0.14),transparent_40%),linear-gradient(180deg,rgba(15,23,42,0.75),rgba(2,6,23,0.9))] opacity-40 pointer-events-none" />
      
      {/* HUD-style Header Overlay */}
      <div className="absolute left-6 top-6 z-10 pointer-events-none">
        <h2 className="text-xl font-black uppercase tracking-widest text-slate-100">
          Routing Topology
        </h2>
        <div className="mt-1 flex items-center gap-2 text-[10px]">
          {isMockMode ? (
            <Badge variant="warning" className="bg-amber-900/30 text-amber-300 font-bold border-amber-500/20 shadow-none">Simulation mode</Badge>
          ) : (
            <Badge variant="success" className="bg-emerald-900/30 text-emerald-300 font-bold border-emerald-500/20 shadow-none">Live core</Badge>
          )}
          <div className="rounded-md border border-white/10 bg-white/5 p-2">
            <p className="mb-0.5 text-[10px] uppercase text-slate-300 font-semibold tracking-tight">Sync</p>
            <p className="font-bold text-slate-100">{formatLastUpdate(lastUpdated)}</p>
          </div>
          {hasEmergency && (
            <Badge variant="destructive" className="bg-rose-900/40 text-rose-300 animate-pulse border-rose-500/30 shadow-none">
               Emergency routing active
            </Badge>
          )}
        </div>
      </div>

      <div className="h-full w-full">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.2 }}
          minZoom={0.2}
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
          <Controls 
            className="!bg-slate-900/80 !border-slate-700/50 !shadow-lg" 
            style={{ fill: '#94a3b8' }} 
          />
          <Background gap={22} size={1} color="rgba(148,163,184,0.15)" />
        </ReactFlow>
      </div>

      {/* Floating Node Inspector Panel */}
      {selectedNode && (
        <Card className="absolute right-6 top-6 bottom-6 z-20 w-80 overflow-hidden border-slate-700/60 bg-slate-950/90 shadow-2xl backdrop-blur-xl animate-in slide-in-from-right duration-500">
          <CardHeader className="border-b border-slate-700/60 bg-slate-900/40 pb-4">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-bold uppercase tracking-wider text-slate-100">
                Node Inspector
              </CardTitle>
              <button 
                onClick={() => setSelectedNode(null)} 
                className="rounded-full p-1 hover:bg-slate-800 text-slate-400"
              >
                <Clock3 className="h-4 w-4" /> {/* Swap with a proper close icon if needed, but keeping lucide imports */}
              </button>
            </div>
          </CardHeader>

          <CardContent className="space-y-4 p-5">
            <div className="rounded-lg border border-slate-700/70 bg-slate-900/45 p-4 shadow-inner">
              <p className="text-[10px] font-bold uppercase tracking-widest text-slate-400/80">Resource identifier</p>
              <p className="mt-1 text-lg font-bold text-slate-50">{selectedNode.data?.title}</p>
              <p className="text-xs font-mono text-slate-400">{selectedNode.data?.subtitle}</p>
              <div className="mt-3 flex flex-wrap gap-2">
                <Badge 
                  variant={selectedNode.data?.health === 'down' ? 'destructive' : selectedNode.data?.health === 'degraded' ? 'warning' : 'success'}
                  className="font-bold border-none shadow-none uppercase text-[10px]"
                >
                  {selectedNode.data?.health}
                </Badge>
                {selectedNode.data?.failoverActive && (
                  <Badge variant="warning" className="uppercase font-bold text-[10px] border-none shadow-none">Active Failover</Badge>
                )}
              </div>
            </div>

            <div className="rounded-lg border border-slate-700/70 bg-slate-900/45 p-4 shadow-inner">
              <div className="flex items-center gap-2 text-[10px] font-bold uppercase tracking-widest text-slate-400/80">
                <Activity className="h-3.5 w-3.5 text-emerald-400" />
                Live Telemetry
              </div>
              <div className="mt-3 grid grid-cols-2 gap-4">
                <div>
                  <p className="text-[10px] text-slate-400/70 uppercase tracking-tighter">Bitrate</p>
                  <p className="text-xl font-black text-emerald-400 drop-shadow-sm">
                    {selectedNode.data?.bandwidthMbps.toFixed(1)} <span className="text-xs text-emerald-600/80">Mbps</span>
                  </p>
                </div>
                <div>
                  <p className="text-[10px] text-slate-400/70 uppercase tracking-tighter font-semibold">Engaged Streams</p>
                  <p className="text-xl font-black text-slate-50">
                    {selectedNode.data?.streamCount}
                  </p>
                </div>
              </div>
            </div>

            <div className="rounded-lg border border-slate-700/70 bg-slate-900/45 p-0 overflow-hidden">
              <div className="bg-slate-900/80 px-4 py-2 border-b border-slate-700/70">
                <p className="text-[10px] font-bold uppercase tracking-widest text-slate-400/80">Internal Metadata</p>
              </div>
              <div className="p-4 space-y-1.5 font-mono text-[11px] text-slate-200">
                {Object.entries(selectedNode.data?.metadata || {}).map(([key, value]) => (
                  <div key={key} className="flex items-start justify-between gap-2 border-b border-white/5 pb-1 last:border-b-0">
                    <span className="text-slate-400 lowercase">{key}</span>
                    <span className="font-semibold text-right text-slate-300">{String(value)}</span>
                  </div>
                ))}
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
