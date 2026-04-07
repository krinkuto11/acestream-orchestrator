import React, { useState, useEffect } from 'react'
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
    finalLabelY = targetY
  } else if (data?.labelPosition === 'near-source') {
    // Keep closer to source side while still inside the edge corridor.
    finalLabelX = sourceX + deltaX * 0.32
    finalLabelY = sourceY
  }

  const isFailover = style.strokeDasharray != null
  const isMonitoringRoute = data?.monitoringActive === true
  const isDrainingRoute = data?.drainingRoute === true
  
  const rawBandwidth = (data?.bandwidthMbps || 0) + (data?.uploadMbps || 0)
  const [isActive, setIsActive] = useState(rawBandwidth > 0.1)
  const [isMounted, setIsMounted] = useState(false)

  // Set mounted state exclusively to suppress CSS animations on first render
  useEffect(() => {
    setIsMounted(true)
  }, [])

  // Debounce the inactive state to smooth out network bursts and polling glitches
  useEffect(() => {
    if (rawBandwidth > 0.05) {
      setIsActive(true)
    } else {
      const timer = setTimeout(() => setIsActive(false), 2500)
      return () => clearTimeout(timer)
    }
  }, [rawBandwidth])
  
  // Estimate length of the step path for drawing animation
  const pathLength = Math.abs(targetX - sourceX) + Math.abs(targetY - sourceY) + 100
  const baseStrokeWidth = (style.strokeWidth as number || 2.2) * 1.2
  const safeMaskId = `mask-sweep-${id.replace(/[^a-zA-Z0-9_-]/g, '')}`

  // Background empty pipe
  const trackStyle = {
    ...style,
    stroke: '#64748b',
    strokeWidth: baseStrokeWidth,
    strokeOpacity: 0.3,
    strokeDasharray: (isDrainingRoute || isFailover) ? '8 5' : style.strokeDasharray,
  }

  // Foreground colored dashed pipe, revealed by the sweeping mask
  const fillStyle = {
    ...style,
    stroke: (isFailover || isMonitoringRoute || isDrainingRoute) ? '#f59e0b' : '#22c55e',
    strokeWidth: baseStrokeWidth,
    strokeOpacity: 1,
    strokeDasharray: '8 6', // Always dashed
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
            strokeWidth={baseStrokeWidth * 3} // Ensure the mask fully envelopes the path
            strokeLinecap="round"
            strokeLinejoin="round"
            style={{
              strokeDasharray: pathLength,
              strokeDashoffset: isActive ? 0 : pathLength,
              transition: isMounted ? 'stroke-dashoffset 0.5s ease-in-out' : 'none',
            }}
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
          {/* Professional, clean label styling. No neon glow, just solid readable UI. */}
          <div 
            className={cn(
              "px-2 py-0.5 rounded-md border text-[11px] font-semibold transition-colors duration-300 shadow-md flex items-center gap-2",
              (isFailover || isMonitoringRoute || isDrainingRoute)
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
