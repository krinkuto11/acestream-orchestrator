import React, { useState, useEffect, useCallback } from 'react'
import { Badge } from '@/components/ui/badge'

/**
 * Helper function to calculate percentage position within the live window
 * @param {number} value - The value to calculate percentage for
 * @param {number} liveFirst - Start of the live window (left end)
 * @param {number} liveLast - End of the live window (right end / LIVE point)
 * @returns {number} Percentage (0-100)
 */
function calculatePercentage(value, liveFirst, liveLast) {
  if (value == null) return 0
  if (liveLast == null || liveFirst == null) return 0
  
  const totalRange = liveLast - liveFirst
  if (totalRange <= 0) return 0
  
  const position = value - liveFirst
  return Math.max(0, Math.min(100, (position / totalRange) * 100))
}

/**
 * Format timestamp (in seconds) to HH:MM:SS or MM:SS
 */
function formatTimestamp(seconds) {
  if (!seconds && seconds !== 0) return '--:--'
  
  const hours = Math.floor(seconds / 3600)
  const minutes = Math.floor((seconds % 3600) / 60)
  const secs = Math.floor(seconds % 60)
  
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }
  return `${minutes}:${String(secs).padStart(2, '0')}`
}

/**
 * YouTube-like progress bar for live streams
 * 
 * DESIGN ALIGNED WITH LEAKY BUCKET ARCHITECTURE:
 * - Engine Swarm area (Gray): Data the internal engine has downloaded from P2P.
 * - Proxy Inventory (Indigo): Data sitting in Redis waiting to be pulsed to the client.
 * - Viewer Playhead (Red): The actual timestamp the user is currently watching.
 * 
 * Data fields:
 * - pos: The proxy's read head from the engine (The leading edge of the proxy buffer).
 * - runway: How many seconds of video are in the proxy's buffer.
 * - viewerPos: pos - runway (The trailing edge of the proxy buffer / viewer's location).
 */
function StreamProgressBar({ streamId, orchUrl, apiKey, clientRunway, clientRunwayMax }) {
  const [liveposData, setLiveposData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchLivepos = useCallback(async () => {
    if (!streamId) return

    try {
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }

      const response = await fetch(
        `${orchUrl}/api/v1/streams/${encodeURIComponent(streamId)}/livepos`,
        { headers }
      )

      if (response.ok) {
        const data = await response.json()
        setLiveposData(data)
        setError(null)
      } else {
        setError(`HTTP ${response.status}`)
      }
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [streamId, orchUrl, apiKey])

  useEffect(() => {
    fetchLivepos()
    // Poll every 5 seconds for dynamic updates
    const interval = setInterval(fetchLivepos, 5000)
    return () => clearInterval(interval)
  }, [fetchLivepos])

  if (loading) {
    return (
      <div className="text-xs text-muted-foreground">
        Loading...
      </div>
    )
  }

  if (error) {
    return (
      <div className="text-xs text-muted-foreground">
        Progress unavailable
      </div>
    )
  }

  if (!liveposData?.has_livepos) {
    // Stream doesn't have livepos data (might be VOD or not live)
    return (
      <div className="text-xs text-muted-foreground">
        {liveposData?.is_live ? 'Live (no position data)' : 'Not available'}
      </div>
    )
  }

  const { livepos, is_live } = liveposData
  const { pos, live_first, live_last, first_ts, last_ts, buffer_pieces } = livepos

  // Use live_first and live_last as the bar boundaries
  const barStart = live_first ?? first_ts ?? 0
  const barEnd = live_last ?? last_ts ?? 0

  if (barEnd <= barStart) {
    return (
      <div className="text-xs text-muted-foreground">
        Waiting for stream data...
      </div>
    )
  }

  // --- MATH ALIGNMENT ---
  // The 'pos' is the PROXY'S READ HEAD from the engine.
  // The 'runway' is how much data the proxy has cached in Redis.
  // The 'viewerPos' is what the user is actually watching.
  const runway = Number(clientRunway ?? 0)
  const runwayMax = Number(clientRunwayMax ?? runway)
  
  const viewerPos = pos - runway
  const viewerPosMax = pos - runwayMax
  
  // Percentages for the bar
  const enginePosPercent = calculatePercentage(pos, barStart, barEnd)
  const viewerPosPercent = calculatePercentage(viewerPos, barStart, barEnd)
  const viewerPosMaxPercent = calculatePercentage(viewerPosMax, barStart, barEnd)
  const swarmEndPercent = calculatePercentage(last_ts ?? barEnd, barStart, barEnd)
  
  // Widths
  const proxyBufferWidth = Math.max(0.5, enginePosPercent - viewerPosPercent)
  const viewerRangeWidth = Math.max(0, viewerPosPercent - viewerPosMaxPercent)
  const swarmBufferWidth = Math.max(0, swarmEndPercent - enginePosPercent)
  
  // Real viewer lag (Live Edge - Viewer Playhead)
  const viewerLagSeconds = Math.max(0, Math.floor(barEnd - viewerPos))

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        {is_live && (
          <Badge variant="destructive" className="text-xs px-1.5 py-0.5">
            LIVE
          </Badge>
        )}
        <div className="flex-1 text-xs text-muted-foreground">
          {viewerLagSeconds > 0 ? (
            <>{viewerLagSeconds}s behind live</>
          ) : (
            <>At live edge</>
          )}
          <span className="mx-1 text-muted-foreground/30">|</span>
          <span className="text-[10px] opacity-70">
            Proxy Cache: {runway.toFixed(1)}s
          </span>
        </div>
        {buffer_pieces != null && (
          <div className="text-xs text-muted-foreground">
            {buffer_pieces} pcs
          </div>
        )}
      </div>
      
      {/* Three-stage progress bar */}
      <div className="relative h-3 w-full bg-muted/50 rounded-sm overflow-hidden group">
        
        {/* layer 1: Swarm Availability (Gray) - What's in engine cache */}
        <div
          className="absolute h-full bg-slate-400/40 dark:bg-slate-500/40 transition-all duration-300"
          style={{ 
            left: `${enginePosPercent}%`,
            width: `${swarmBufferWidth}%` 
          }}
          title="Engine Swarm Cache"
        />

        {/* layer 2: Proxy Inventory (Indigo/Violet) - What's in Redis */}
        <div
          className="absolute h-full bg-indigo-500/80 dark:bg-indigo-600/80 transition-all duration-300"
          style={{ 
            left: `${viewerPosPercent}%`,
            width: `${proxyBufferWidth}%` 
          }}
          title="Proxy Buffer (Inventory)"
        />

        {/* layer 3: Viewer Range (if multiple clients) */}
        {viewerRangeWidth > 0 && (
          <div
            className="absolute h-full bg-indigo-400/40 transition-all duration-300"
            style={{ 
              left: `${viewerPosMaxPercent}%`,
              width: `${viewerRangeWidth}%` 
            }}
          />
        )}
        
        {/* layer 4: View Progress (Red) - Consumed content */}
        <div
          className="absolute h-full bg-red-600/40 dark:bg-red-500/40 transition-all duration-300"
          style={{ width: `${viewerPosPercent}%` }}
        />
        
        {/* layer 5: Viewer Playhead indicator */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-red-600 dark:bg-red-500 rounded-full shadow-lg transition-all duration-300"
          style={{ left: `${viewerPosPercent}%`, marginLeft: '-6px' }}
        />
      </div>
    </div>
  )
}

export default StreamProgressBar
