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
 * Design based on AceStream wiki:
 * - Left end of bar = live_first (past)
 * - Right end of bar = live_last (LIVE edge)
 * - Gray buffer bar = from pos to last_ts
 * - Red playhead = current position (pos)
 * 
 * Data fields:
 * - pos: current playback position
 * - live_first: start of the live window
 * - live_last: end of the live window (LIVE point)
 * - first_ts: oldest available chunk timestamp
 * - last_ts: newest available chunk timestamp (usually same as live_last)
 * - buffer_pieces: number of buffered pieces
 */
function StreamProgressBar({ streamId, orchUrl, apiKey }) {
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
        `${orchUrl}/streams/${encodeURIComponent(streamId)}/livepos`,
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
  // Fallback to first_ts/last_ts if live window not available
  const barStart = live_first ?? first_ts ?? 0
  const barEnd = live_last ?? last_ts ?? 0

  if (barEnd <= barStart) {
    // Invalid or no data
    return (
      <div className="text-xs text-muted-foreground">
        Waiting for stream data...
      </div>
    )
  }

  // Calculate red playhead position (current pos)
  const playheadPercent = calculatePercentage(pos, barStart, barEnd)
  
  // Calculate gray buffer bar span (from pos to last_ts)
  const bufferStart = pos
  const bufferEnd = last_ts ?? barEnd
  const bufferStartPercent = calculatePercentage(bufferStart, barStart, barEnd)
  const bufferEndPercent = calculatePercentage(bufferEnd, barStart, barEnd)
  
  // Buffer width is the span from current position to last_ts
  const bufferWidth = Math.max(0, bufferEndPercent - bufferStartPercent)
  
  // Calculate seconds behind live (live_last - pos)
  const secondsBehindLive = Math.max(0, Math.floor((live_last ?? last_ts ?? 0) - (pos ?? 0)))

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        {is_live && (
          <Badge variant="destructive" className="text-xs px-1.5 py-0.5">
            LIVE
          </Badge>
        )}
        <div className="flex-1 text-xs text-muted-foreground">
          {secondsBehindLive > 0 ? (
            <>{secondsBehindLive} second{secondsBehindLive !== 1 ? 's' : ''} behind live</>
          ) : (
            <>At live edge</>
          )}
        </div>
        {buffer_pieces != null && (
          <div className="text-xs text-muted-foreground">
            Buffer: {buffer_pieces} pieces
          </div>
        )}
      </div>
      
      {/* YouTube-style progress bar container */}
      <div className="relative h-3 w-full bg-muted/50 rounded-sm overflow-hidden cursor-pointer hover:h-3.5 transition-all duration-200 group">
        {/* Gray buffered content bar - from pos to last_ts */}
        <div
          className="absolute h-full bg-gray-400/70 dark:bg-gray-500/70 transition-all duration-300"
          style={{ 
            left: `${bufferStartPercent}%`,
            width: `${bufferWidth}%` 
          }}
        />
        
        {/* Red playhead indicator (current position) */}
        <div
          className="absolute h-full bg-red-600 dark:bg-red-500 transition-all duration-300"
          style={{ width: `${playheadPercent}%` }}
        />
        
        {/* Playhead scrubber (visible on hover) */}
        <div
          className="absolute top-1/2 -translate-y-1/2 w-3 h-3 bg-red-600 dark:bg-red-500 rounded-full opacity-0 group-hover:opacity-100 transition-opacity duration-200 shadow-lg"
          style={{ left: `${playheadPercent}%`, marginLeft: '-6px' }}
        />
      </div>
    </div>
  )
}

export default StreamProgressBar
