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

  if (data?.labelPosition === 'near-target') {
    finalLabelX = targetX - 55
    finalLabelY = targetY
  } else if (data?.labelPosition === 'near-source') {
    finalLabelX = sourceX + 55
    finalLabelY = sourceY
  }

  const isFailover = style.strokeDasharray != null
  const bandwidth = data?.bandwidthMbps || 0
  const upload = data?.uploadMbps || 0
  
  // The store now dictates the stroke color and animation duration.
  // We just apply it cleanly, falling back to slate for idle lines.
  const finalStyle = {
    ...style,
    stroke: style.stroke || '#64748b', 
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
          {/* Professional, clean label styling. No neon glow, just solid readable UI. */}
          <div 
            className={cn(
              "px-2 py-0.5 rounded-md border text-[11px] font-medium transition-colors duration-300 shadow-sm flex items-center gap-2",
              isFailover 
                ? "border-amber-500/40 bg-slate-900 text-amber-400" 
                : bandwidth > 0.1 
                  ? "border-emerald-500/40 bg-slate-900 text-emerald-400" 
                  : "border-slate-700 bg-slate-900 text-slate-400"
            )}
          >
            {data?.uploadMbps !== undefined ? (
              <div className="flex gap-2">
                <div className="flex items-center gap-1">
                  <span className="text-[10px] text-slate-500">↓</span>
                  <span>{bandwidth.toFixed(1)}</span>
                </div>
                <div className="flex items-center gap-1">
                  <span className="text-[10px] text-slate-500">↑</span>
                  <span>{upload.toFixed(1)}</span>
                </div>
              </div>
            ) : (
              <span>{bandwidth.toFixed(1)}</span>
            )}
            <span className="text-[10px] text-slate-500 font-normal ml-0.5">Mbps</span>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
