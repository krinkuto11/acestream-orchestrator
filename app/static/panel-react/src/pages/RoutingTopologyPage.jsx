import { useCallback, useEffect, useMemo, useRef } from 'react'
import ReactFlow, { Background, Controls, MarkerType, ReactFlowProvider, useReactFlow } from 'reactflow'
import 'reactflow/dist/style.css'
import { Activity, AlertTriangle, Network, ShieldAlert, Users, X } from 'lucide-react'
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

export function RoutingTopologyPage(props) {
  return (
    <ReactFlowProvider>
      <RoutingTopologyInner {...props} />
    </ReactFlowProvider>
  )
}

function RoutingTopologyInner({ engines, streams, vpnStatus, orchestratorStatus, embedded = false }) {
  const { fitView } = useReactFlow()
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

  const containerRef = useRef(null)
  const fitDebounceRef = useRef(null)

  // Debounced fitView — called after the sidebar CSS transition finishes
  const debouncedFitView = useCallback(() => {
    if (fitDebounceRef.current) clearTimeout(fitDebounceRef.current)
    fitDebounceRef.current = setTimeout(() => {
      fitView({ padding: 0.12, duration: 400 })
    }, 50)
  }, [fitView])

  // Watch container size changes (triggered by sidebar expand/collapse)
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const observer = new ResizeObserver(debouncedFitView)
    observer.observe(el)
    return () => {
      observer.disconnect()
      if (fitDebounceRef.current) clearTimeout(fitDebounceRef.current)
    }
  }, [debouncedFitView])

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

  // Automatically fit view when nodes are first populated or change significantly
  useEffect(() => {
    if (nodes.length > 0) {
      const timer1 = setTimeout(() => fitView({ padding: 0.12, duration: 800 }), 150)
      const timer2 = setTimeout(() => fitView({ padding: 0.12, duration: 800 }), 1000)
      return () => {
        clearTimeout(timer1)
        clearTimeout(timer2)
      }
    }
  }, [nodes.length, fitView])

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
    <div ref={containerRef} className={cn(
      "relative w-full overflow-hidden rounded-xl border border-slate-800 bg-[#0f172a] shadow-inner flex flex-col",
      embedded ? "h-[740px]" : "h-screen"
    )}>
      {/* Title Header */}
      <div className="absolute left-6 top-6 z-20 pointer-events-none">
        <h1 className="text-2xl font-semibold tracking-tight text-slate-100 flex items-center gap-2">
          <Network className="h-6 w-6 text-slate-400" />
          Routing Topology
        </h1>
        <p className="text-sm font-medium text-slate-500 mt-1">Live streaming pipelines and proxy activity</p>
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
            className="!bg-slate-900 !border-slate-700 !shadow-lg" 
            style={{ fill: '#94a3b8' }} 
          />
          <Background gap={22} size={1} color="rgba(148,163,184,0.1)" />
        </ReactFlow>
      </div>

      {/* Professional Node Inspector Panel */}
      {selectedNode && (
        <Card className="absolute right-6 top-6 bottom-6 z-20 w-80 overflow-hidden border-slate-700/50 bg-[#0f172a]/95 shadow-2xl backdrop-blur-md animate-in slide-in-from-right duration-300">
          <CardHeader className="border-b border-slate-800 bg-slate-900/50 pb-4">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold tracking-tight text-slate-100">
                Resource Details
              </CardTitle>
              <button 
                onClick={() => setSelectedNode(null)} 
                className="rounded-full p-1 hover:bg-slate-800 text-slate-400 transition-colors"
                aria-label="Close"
              >
                <X className="h-4 w-4" />
              </button>
            </div>
          </CardHeader>

          <CardContent className="space-y-4 p-5">
            <div className="rounded-lg border border-slate-800 bg-slate-900/30 p-4">
              <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Resource Identifier</p>
              <p className="mt-1 text-base font-semibold text-slate-100 leading-tight">{selectedNode.data?.title}</p>
              <p className="mt-0.5 font-mono text-[11px] text-slate-400 line-clamp-1">{selectedNode.data?.subtitle}</p>
              
              <div className="mt-3 flex flex-wrap gap-2">
                <Badge 
                  variant={selectedNode.data?.health === 'down' ? 'destructive' : selectedNode.data?.health === 'degraded' ? 'warning' : 'outline'}
                  className={cn(
                    "font-semibold uppercase text-[9px] px-1.5 py-0",
                    selectedNode.data?.health === 'healthy' && "border-emerald-500/30 text-emerald-400 bg-emerald-500/5"
                  )}
                >
                  {selectedNode.data?.health}
                </Badge>
                {selectedNode.data?.failoverActive && (
                  <Badge variant="warning" className="uppercase font-semibold text-[9px] border-amber-500/40 bg-amber-500/10 text-amber-200">
                    Active Failover
                  </Badge>
                )}
              </div>
            </div>

            <div className="rounded-lg border border-slate-800 bg-slate-900/30 p-4">
              <div className="flex items-center gap-2 text-[10px] font-semibold uppercase tracking-wider text-slate-500 mb-3">
                <Activity className="h-3.5 w-3.5 text-blue-400" />
                Live Telemetry
              </div>
              <div className="grid grid-cols-2 gap-4">
                <div>
                  <p className="text-[10px] text-slate-500 uppercase font-medium">Throughput</p>
                  <p className="text-xl font-semibold text-emerald-400 tabular-nums">
                    {selectedNode.data?.bandwidthMbps.toFixed(1)} <span className="text-xs text-slate-500 font-normal ml-0.5">Mbps</span>
                  </p>
                </div>
                <div>
                  <p className="text-[10px] text-slate-500 uppercase font-medium">Active Streams</p>
                  <p className="text-xl font-semibold text-slate-100 tabular-nums">
                    {selectedNode.data?.streamCount}
                  </p>
                </div>
              </div>
            </div>

            <div className="rounded-lg border border-slate-800 bg-slate-900/30 overflow-hidden">
              <div className="bg-slate-900/60 px-4 py-2 border-b border-slate-800">
                <p className="text-[10px] font-semibold uppercase tracking-wider text-slate-500">Metadata Properties</p>
              </div>
              <div className="p-4 space-y-2 font-mono text-[11px] text-slate-300">
                {Object.entries(selectedNode.data?.metadata || {}).map(([key, value]) => (
                  <div key={key} className="flex items-center justify-between gap-4 border-b border-white/5 pb-1.5 last:border-b-0 last:pb-0">
                    <span className="text-slate-500">{key}</span>
                    <span className="font-semibold text-right text-slate-300 truncate">{String(value)}</span>
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
