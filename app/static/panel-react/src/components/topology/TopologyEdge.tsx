import React from 'react'
import { BaseEdge, EdgeLabelRenderer, EdgeProps, getSmoothStepPath } from 'reactflow'

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

  return (
    <>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={style} />
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
            className={`
              px-2 py-0.5 rounded border shadow-sm text-[10px] font-bold 
              ${data?.labelPosition === 'near-source' 
                ? 'border-sky-500/40 bg-sky-950/90 text-sky-300' 
                : 'border-slate-700 bg-slate-900/90 text-slate-300'}
              ${isFailover ? 'border-amber-500/50 text-amber-200' : ''}
            `}
          >
            {bandwidth.toFixed(1)} <span className="text-[8px] font-medium opacity-70">Mbps</span>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
