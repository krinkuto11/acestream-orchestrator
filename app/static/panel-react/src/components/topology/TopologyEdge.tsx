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
              padding: '2px 8px',
              borderRadius: '6px',
              fontSize: '10px',
              fontWeight: 800,
              letterSpacing: '0.02em',
              transition: 'all 0.3s ease',
              ...(isFailover ? {
                background: '#92400e',
                borderColor: '#fbbf24',
                color: '#fef3c7',
                border: '1.5px solid #fbbf24',
                boxShadow: '0 0 12px rgba(245,158,11,0.4)',
              } : isActive ? {
                background: '#065f46',
                borderColor: '#34d399',
                color: '#d1fae5',
                border: '1.5px solid #34d399',
                boxShadow: '0 0 12px rgba(16,185,129,0.45)',
              } : data?.labelPosition === 'near-source' ? {
                background: '#0c4a6e',
                borderColor: '#38bdf8',
                color: '#e0f2fe',
                border: '1.5px solid rgba(56,189,248,0.5)',
                boxShadow: '0 1px 4px rgba(0,0,0,0.3)',
              } : {
                background: '#334155',
                borderColor: '#64748b',
                color: '#e2e8f0',
                border: '1.5px solid #64748b',
                boxShadow: '0 1px 4px rgba(0,0,0,0.3)',
              }),
            }}
          >
            {bandwidth.toFixed(1)}
            {data?.uploadMbps !== undefined && (
              <>
                <span style={{ margin: '0 4px', opacity: 0.5 }}>|</span>
                <span style={{ fontSize: '8px', verticalAlign: 'middle', marginRight: '2px', opacity: 0.8 }}>↑</span>
                {data.uploadMbps.toFixed(1)}
              </>
            )}
            <span style={{ fontSize: '8px', fontWeight: 500, opacity: 0.85, marginLeft: '4px' }}>Mbps</span>
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
