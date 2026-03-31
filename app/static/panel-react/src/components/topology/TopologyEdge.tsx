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

  // Position label cleanly in the horizontal corridors
  let finalLabelX = labelX
  let finalLabelY = labelY
  const deltaX = targetX - sourceX

  if (data?.labelPosition === 'near-target') {
    // Keep closer to target side but detached from node body/handle.
    finalLabelX = sourceX + deltaX * 0.68
    finalLabelY = labelY - 14
  } else if (data?.labelPosition === 'near-source') {
    // Keep closer to source side while still inside the edge corridor.
    finalLabelX = sourceX + deltaX * 0.32
    finalLabelY = labelY - 14
  }

  const isFailover = style.strokeDasharray != null
  const bandwidth = (data?.bandwidthMbps || 0) + (data?.uploadMbps || 0)
  const isActive = bandwidth > 0.1
  
  // The store dictates the base stroke color. 
  // We ensure it's visible and high-contrast.
  const finalStyle = {
    ...style,
    stroke: isActive ? '#22c55e' : '#64748b',
    strokeWidth: (style.strokeWidth as number || 2.2) * 1.2, // Slightly thicker for better visibility
    strokeOpacity: isActive ? 1 : 0.4,
    transition: 'all 0.3s ease',
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
            zIndex: 80,
          }}
          className="nodrag nopan"
        >
          {/* Professional, clean label styling. No neon glow, just solid readable UI. */}
          <div 
            className={cn(
              "px-2 py-0.5 rounded-md border text-[11px] font-semibold transition-colors duration-300 shadow-md flex items-center gap-2",
              isFailover 
                ? "border-amber-400 bg-[#020617] text-amber-400" 
                : isActive 
                  ? "border-emerald-400 bg-[#020617] text-emerald-400" 
                  : "border-slate-600 bg-[#020617] text-slate-400"
            )}
          >
            {data?.uploadMbps !== undefined ? (
              <div className="flex gap-2">
                <div className="flex items-center gap-1">
                  <span className="text-[10px] text-slate-500">↓</span>
                  <span className="tabular-nums font-bold">{data.bandwidthMbps.toFixed(1)}</span>
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-[10px] text-slate-500">↑</span>
                  <span className="tabular-nums font-bold">{data.uploadMbps.toFixed(1)}</span>
                </div>
              </div>
            ) : (
              <span className="tabular-nums font-bold">{data.bandwidthMbps.toFixed(1)}</span>
            )}
            <span className="text-[10px] text-slate-500 font-medium ml-0.5 uppercase">Mbps</span>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
