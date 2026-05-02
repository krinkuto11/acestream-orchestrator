import React, { useEffect, useMemo, useRef, useState } from 'react'
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

  let finalLabelX = labelX
  let finalLabelY = labelY
  const deltaX = targetX - sourceX

  if (data?.labelPosition === 'near-target') {
    finalLabelX = sourceX + deltaX * 0.68
    finalLabelY = targetY
  } else if (data?.labelPosition === 'near-source') {
    finalLabelX = sourceX + deltaX * 0.32
    finalLabelY = sourceY
  }

  const isFailover = style.strokeDasharray != null
  const isMonitoringRoute = data?.monitoringActive === true
  const isDrainingRoute = data?.drainingRoute === true
  const isPrebuffering = data?.isPrebuffering === true

  const rawBandwidth = (data?.bandwidthMbps || 0) + (data?.uploadMbps || 0)
  const flowActive = data?.flowActive === undefined ? rawBandwidth > 0.05 : data.flowActive === true
  const [isMounted, setIsMounted] = useState(false)
  const [shouldAnimateFlowChange, setShouldAnimateFlowChange] = useState(false)
  const previousFlowActiveRef = useRef(flowActive)

  useEffect(() => {
    setIsMounted(true)
  }, [])

  useEffect(() => {
    if (!isMounted) {
      previousFlowActiveRef.current = flowActive
      return
    }
    if (previousFlowActiveRef.current === flowActive) return
    previousFlowActiveRef.current = flowActive
    setShouldAnimateFlowChange(true)
    const timer = setTimeout(() => setShouldAnimateFlowChange(false), 520)
    return () => clearTimeout(timer)
  }, [flowActive, isMounted])
  
  const pathLength = useMemo(() => {
    return Math.abs(targetX - sourceX) + Math.abs(targetY - sourceY) + 100
  }, [targetX, sourceX, targetY, sourceY])

  const baseStrokeWidth = (style.strokeWidth as number || 2) * 1.2
  const safeMaskId = `mask-sweep-${id.replace(/[^a-zA-Z0-9_-]/g, '')}`

  const maskStyle = useMemo(() => ({
    strokeDasharray: pathLength,
    strokeDashoffset: flowActive ? 0 : pathLength,
    transition: (isMounted && shouldAnimateFlowChange) ? 'stroke-dashoffset 0.5s ease-in-out' : 'none',
  }), [flowActive, isMounted, pathLength, shouldAnimateFlowChange])

  // Track style (background pipe)
  const trackStyle = {
    ...style,
    stroke: 'var(--line)',
    strokeWidth: baseStrokeWidth,
    strokeOpacity: 0.3,
    strokeDasharray: (isDrainingRoute || isFailover) ? '6 4' : style.strokeDasharray,
  }

  // Fill style (active traffic)
  const fillStyle = {
    ...style,
    stroke: isPrebuffering 
      ? 'var(--acc-amber)' 
      : (isFailover || isMonitoringRoute || isDrainingRoute) ? 'var(--acc-amber)' : 'var(--acc-green)',
    strokeWidth: baseStrokeWidth,
    strokeOpacity: 1,
    strokeDasharray: '6 4',
    mask: `url(#${safeMaskId})`,
  }

  return (
    <>
      <defs>
        <mask id={safeMaskId}>
          <path
            d={edgePath}
            fill="none"
            stroke="white"
            strokeWidth={baseStrokeWidth * 3}
            strokeLinecap="round"
            strokeLinejoin="round"
            style={maskStyle}
          />
        </mask>
      </defs>
      <BaseEdge path={edgePath} markerEnd={markerEnd} style={trackStyle} />
      <BaseEdge path={edgePath} style={fillStyle} />
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
          <div 
            style={{
              padding: '2px 6px',
              borderRadius: 2,
              border: `1px solid ${isPrebuffering ? 'var(--acc-amber)' : (isFailover || isMonitoringRoute || isDrainingRoute) ? 'var(--acc-amber)' : flowActive ? 'var(--acc-green)' : 'var(--line)'}`,
              background: 'var(--bg-1)',
              color: isPrebuffering ? 'var(--acc-amber)' : (isFailover || isMonitoringRoute || isDrainingRoute) ? 'var(--acc-amber)' : flowActive ? 'var(--fg-1)' : 'var(--fg-3)',
              fontSize: 10,
              fontWeight: 700,
              display: 'flex',
              alignItems: 'center',
              gap: 4,
              boxShadow: '0 2px 8px rgba(0,0,0,0.4)',
              fontFamily: 'var(--font-mono)',
            }}
          >
            {data?.uploadMbps !== undefined ? (
              <div style={{ display: 'flex', gap: 6 }}>
                <span>↓{data.bandwidthMbps.toFixed(1)}</span>
                <span>↑{data.uploadMbps.toFixed(1)}</span>
              </div>
            ) : (
              <span>{isPrebuffering ? 'PREBUFFER' : data.bandwidthMbps.toFixed(1)}</span>
            )}
            {!isPrebuffering && <span style={{ fontSize: 8, opacity: 0.6 }}>Mb/s</span>}
          </div>
        </div>
      </EdgeLabelRenderer>
    </>
  )
}
