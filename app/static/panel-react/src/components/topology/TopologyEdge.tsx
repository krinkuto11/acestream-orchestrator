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
          }}
          className="nodrag nopan"
        >
          <div 
            className={cn(
              "px-2 py-0.5 rounded border shadow-sm text-[10px] font-bold transition-colors duration-300",
              isFailover 
                ? "border-amber-400 bg-amber-950 text-amber-50 shadow-[0_0_12px_rgba(245,158,11,0.35)]" 
                : bandwidth > 0.1 
                  ? "border-emerald-400 bg-emerald-950 text-emerald-50 shadow-[0_0_12px_rgba(16,185,129,0.4)]" 
                  : data?.labelPosition === 'near-source' 
                    ? "border-sky-500/50 bg-sky-900 text-sky-50 font-black px-1.5 shadow-sm" 
                    : "border-slate-500 bg-slate-800 text-slate-100 font-bold px-1.5 shadow-sm"
            )}
          >
            {bandwidth.toFixed(1)} <span className="text-[8px] font-medium opacity-80">Mbps</span>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
