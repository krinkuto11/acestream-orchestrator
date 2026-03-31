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
                ? "border-amber-600 bg-amber-950 text-amber-100" 
                : bandwidth > 0.1 
                  ? "border-emerald-600 bg-emerald-950 text-emerald-50" 
                  : data?.labelPosition === 'near-source' 
                    ? "border-sky-800 bg-slate-900 text-sky-100" 
                    : "border-slate-800 bg-slate-900 text-slate-500"
            )}
          >
            {bandwidth.toFixed(1)} <span className="text-[8px] font-medium opacity-70">Mbps</span>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
