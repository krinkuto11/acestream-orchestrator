import React from 'react'
import { BaseEdge, EdgeLabelRenderer, EdgeProps, getSmoothStepPath } from 'reactflow'
import { cn } from '@/lib/utils'

export function TopologyEdge({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  style = {},
  markerEnd,
  data,
}: EdgeProps) {
  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
    borderRadius: 16,
  })

  // Determine label position based on data.labelPosition
  // Fallback to default labelX, labelY if not specified
  let finalLabelX = labelX
  let finalLabelY = labelY

  if (data?.labelPosition === 'near-target') {
    // Specifically for VPN -> Engine, place on the horizontal segment before the engine
    finalLabelX = targetX - 55
    finalLabelY = targetY
  } else if (data?.labelPosition === 'near-source') {
    // Specifically for Engine -> Proxy, place on the horizontal segment after the engine
    finalLabelX = sourceX + 55
    finalLabelY = sourceY
  }

  const isFailover = style.strokeDasharray != null
  const bandwidth = data?.bandwidthMbps || 0
  const isActive = bandwidth > 0.1

  // Dynamic stroke color: Emerald when active, unless it's a failover path (amber)
  const finalStyle = {
    ...style,
    stroke: isActive 
      ? (isFailover ? '#f59e0b' : '#10b981') 
      : style.stroke,
    transition: 'stroke 0.3s ease',
  }

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={finalStyle} />
      <EdgeLabelRenderer>
        <div
          style={{
            position: 'absolute',
            transform: `translate(-50%, -50%) translate(${finalLabelX}px,${finalLabelY}px)`,
            pointerEvents: 'all',
            zIndex: 50,
          }}
          className="nodrag nopan"
        >
          <div 
            style={{
              padding: '4px 10px',
              borderRadius: '8px',
              fontSize: '11px',
              fontWeight: 900,
              letterSpacing: '0.01em',
              transition: 'all 0.3s ease',
              display: 'flex',
              flexDirection: 'column',
              alignItems: 'center',
              gap: '1px',
              minWidth: '45px',
              ...(isFailover ? {
                background: '#92400e',
                borderColor: '#fbbf24',
                color: '#fef3c7',
                border: '1.5px solid #fbbf24',
                boxShadow: '0 0 12px rgba(245,158,11,0.4)',
              } : isActive ? {
                background: '#064e3b',
                borderColor: '#10b981',
                color: '#ecfdf5',
                border: '1.5px solid rgba(16,185,129,0.6)',
                boxShadow: '0 2px 10px rgba(0,0,0,0.4)',
              } : data?.labelPosition === 'near-source' ? {
                background: '#082f49',
                borderColor: '#0ea5e9',
                color: '#f0f9ff',
                border: '1.5px solid rgba(14,165,233,0.5)',
                boxShadow: '0 2px 10px rgba(0,0,0,0.4)',
              } : {
                background: '#1e293b',
                borderColor: '#475569',
                color: '#f1f5f9',
                border: '1.5px solid rgba(71,85,105,0.6)',
                boxShadow: '0 2px 8px rgba(0,0,0,0.3)',
              }),
            }}
          >
            {data?.uploadMbps !== undefined ? (
              // VPN 3-Cell Stacked Layout
              <div className="flex flex-col gap-1 w-full">
                <div className="flex items-center justify-between gap-3 px-1.5 py-0.5 bg-emerald-500/10 rounded-sm border border-emerald-500/20">
                  <span className="text-[10px] text-emerald-400 font-bold">↓</span>
                  <span className="text-[13px] font-black tabular-nums">{bandwidth.toFixed(1)}</span>
                </div>
                <div className="flex items-center justify-between gap-3 px-1.5 py-0.5 bg-rose-500/10 rounded-sm border border-rose-500/20">
                  <span className="text-[10px] text-rose-400 font-bold">↑</span>
                  <span className="text-[13px] font-black tabular-nums">{data.uploadMbps.toFixed(1)}</span>
                </div>
                <div className="text-[9px] font-black text-white/90 text-center tracking-[0.2em] uppercase pt-0.5">
                  Mbps
                </div>
              </div>
            ) : (
              // Engine/Client 2-Cell Horizontal Layout
              <div className="flex items-center h-7 overflow-hidden rounded-[4px] border border-white/20 shadow-sm bg-black/40">
                <div className="flex items-center px-2 h-full bg-slate-100/10">
                  <span className="text-[13px] font-black text-white tabular-nums leading-none">
                    {bandwidth.toFixed(1)}
                  </span>
                </div>
                <div className="flex items-center px-1.5 h-full border-l border-white/20 bg-white/5">
                  <span className="text-[10px] font-bold text-white/80 lowercase leading-none">
                    Mbps
                  </span>
                </div>
              </div>
            )}
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
