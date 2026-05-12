import { useCallback, useEffect, useMemo, useRef } from 'react'
import ReactFlow, { Background, Controls, MarkerType, ReactFlowProvider, useReactFlow } from 'reactflow'
import 'reactflow/dist/style.css'
import { TopologyNode } from '@/components/topology/TopologyNode'
import { TopologyEdge } from '@/components/topology/TopologyEdge'
import { useTopologyStore, formatThroughputDual } from '@/stores/topologyStore'

const nodeTypes = {
  topologyNode: TopologyNode,
}

const edgeTypes = {
  topologyEdge: TopologyEdge,
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
    hydrateFromBackend,
    simulateTick,
    setSelectedNode,
  } = useTopologyStore((state) => state)

  const containerRef = useRef(null)
  const fitDebounceRef = useRef(null)

  const debouncedFitView = useCallback(() => {
    if (fitDebounceRef.current) clearTimeout(fitDebounceRef.current)
    fitDebounceRef.current = setTimeout(() => {
      fitView({ padding: 0.05, duration: 400 })
    }, 50)
  }, [fitView])

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
    hydrateFromBackend({
      engines,
      streams,
      vpnStatus,
      orchestratorStatus,
    })
  }, [engines, streams, vpnStatus, orchestratorStatus, hydrateFromBackend])

  useEffect(() => {
    const interval = window.setInterval(() => {
      simulateTick()
    }, 500)
    return () => window.clearInterval(interval)
  }, [simulateTick])

  useEffect(() => {
    if (nodes.length > 0) {
      const timer1 = setTimeout(() => fitView({ padding: 0.1, duration: 800 }), 150)
      return () => clearTimeout(timer1)
    }
  }, [nodes.length, fitView])

  const selectedNode = useMemo(
    () => nodes.find((node) => node.id === selectedNodeId) || null,
    [nodes, selectedNodeId],
  )

  return (
    <div ref={containerRef} style={{
      position: 'relative',
      width: '100%',
      height: embedded ? '740px' : '100%',
      background: 'var(--bg-0)',
      border: '1px solid var(--line)',
      overflow: 'hidden',
      display: 'flex',
      flexDirection: 'column',
    }}>
      {/* Topology Header */}
      <div style={{
        position: 'absolute',
        left: 20,
        top: 20,
        zIndex: 20,
        pointerEvents: 'none',
      }}>
        <div className="label" style={{ color: 'var(--fg-3)', marginBottom: 4 }}>NETWORK · TOPOLOGY</div>
        <h1 style={{
          fontFamily: 'var(--font-display)',
          fontSize: 24,
          fontWeight: 700,
          color: 'var(--fg-0)',
          margin: 0,
          letterSpacing: '-0.02em',
        }}>Routing Mesh</h1>
        <div style={{ fontSize: 12, color: 'var(--fg-2)', marginTop: 4 }}>
          {nodes.length} nodes · {summary.activeStreams} streams · {summary.vpnHealthy.length} VPNs
        </div>
      </div>

      {/* React Flow Canvas */}
      <div style={{ flex: 1, width: '100%' }}>
        <ReactFlow
          nodes={nodes}
          edges={edges}
          nodeTypes={nodeTypes}
          edgeTypes={edgeTypes}
          fitView
          fitViewOptions={{ padding: 0.1 }}
          minZoom={0.1}
          maxZoom={2}
          onNodeClick={(_, node) => setSelectedNode(node.id)}
          onPaneClick={() => setSelectedNode(null)}
          proOptions={{ hideAttribution: true }}
          defaultEdgeOptions={{
            markerEnd: { type: MarkerType.ArrowClosed, color: 'var(--line)' },
          }}
          nodesDraggable={false}
          elementsSelectable
        >
          <Controls 
            showInteractive={false}
            style={{ 
              background: 'var(--bg-1)', 
              border: '1px solid var(--line)',
              boxShadow: '0 4px 12px rgba(0,0,0,0.5)',
              borderRadius: 0,
            }}
          />
          <Background gap={32} size={1} color="var(--line-soft)" />
        </ReactFlow>
      </div>

      {/* Inspector Panel */}
      {selectedNode && (
        <div className="bracketed" style={{
          position: 'absolute',
          right: 20,
          top: 20,
          bottom: 20,
          width: 320,
          zIndex: 30,
          background: 'var(--bg-1)',
          border: '1px solid var(--line)',
          display: 'flex',
          flexDirection: 'column',
          boxShadow: '0 0 40px rgba(0,0,0,0.4)',
        }}>
          {/* Panel Header */}
          <div style={{
            padding: '12px 14px',
            borderBottom: '1px solid var(--line)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'space-between',
            background: 'var(--bg-2)',
          }}>
            <span className="label">NODE · INSPECTOR</span>
            <button 
              onClick={() => setSelectedNode(null)}
              style={{
                background: 'transparent', border: 0, color: 'var(--fg-3)', cursor: 'pointer', fontSize: 16,
              }}
            >✕</button>
          </div>

          <div style={{ padding: 14, flex: 1, overflowY: 'auto' }}>
            {/* Main Info */}
            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 10, color: 'var(--fg-3)', letterSpacing: 1, marginBottom: 4 }}>IDENTIFIER</div>
              <div style={{ fontSize: 18, fontWeight: 700, color: 'var(--fg-0)', fontFamily: 'var(--font-display)' }}>
                {selectedNode.data?.title}
              </div>
              <div style={{ fontSize: 11, color: 'var(--acc-cyan)', fontFamily: 'var(--font-mono)', marginTop: 2 }}>
                {selectedNode.data?.subtitle}
              </div>
              
              <div style={{ marginTop: 12, display: 'flex', gap: 6 }}>
                <span className={`tag tag-${selectedNode.data?.health === 'healthy' ? 'green' : selectedNode.data?.health === 'down' ? 'red' : 'amber'}`}>
                  <span className="dot" /> {selectedNode.data?.health?.toUpperCase()}
                </span>
                {selectedNode.data?.failoverActive && (
                  <span className="tag tag-magenta">FAILOVER ACTIVE</span>
                )}
                {selectedNode.data?.lifecycle === 'draining' && (
                  <span className="tag tag-amber">DRAINING</span>
                )}
              </div>
            </div>

            {/* Metrics */}
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 20 }}>
              <div style={{ background: 'var(--bg-0)', border: '1px solid var(--line-soft)', padding: 10 }}>
                <div style={{ fontSize: 9, color: 'var(--fg-3)', marginBottom: 4 }}>THROUGHPUT</div>
                <div style={{ fontSize: 14, fontWeight: 700, color: 'var(--acc-green)', fontVariantNumeric: 'tabular-nums' }}>
                  {formatThroughputDual(selectedNode.data?.bandwidthKbps)}
                </div>
              </div>
              <div style={{ background: 'var(--bg-0)', border: '1px solid var(--line-soft)', padding: 10 }}>
                <div style={{ fontSize: 9, color: 'var(--fg-3)', marginBottom: 4 }}>STREAMS</div>
                <div style={{ fontSize: 20, fontWeight: 700, color: 'var(--fg-0)', fontVariantNumeric: 'tabular-nums' }}>
                  {selectedNode.data?.streamCount}
                </div>
              </div>
            </div>

            {/* Metadata Table */}
            <div>
              <div className="label" style={{ marginBottom: 8 }}>METADATA</div>
              <div style={{ 
                background: 'var(--bg-0)', 
                border: '1px solid var(--line-soft)',
                fontSize: 11,
                fontFamily: 'var(--font-mono)',
              }}>
                {Object.entries(selectedNode.data?.metadata || {}).map(([key, value]) => {
                  if (value === null || value === undefined) return null;
                  let displayValue = String(value);
                  if (key === 'targetBitrate' && typeof value === 'number' && value > 0) {
                    displayValue = `${(value / 1e6).toFixed(1)} Mbps`;
                  }
                  return (
                    <div key={key} style={{ 
                      display: 'flex', 
                      justifyContent: 'space-between', 
                      padding: '6px 8px',
                      borderBottom: '1px solid var(--line-soft)',
                    }}>
                      <span style={{ color: 'var(--fg-3)' }}>{key}</span>
                      <span style={{ color: 'var(--fg-1)', textAlign: 'right' }}>{displayValue}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
