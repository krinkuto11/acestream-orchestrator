import React, { useState, useEffect, useCallback } from 'react'
import { Badge } from '@/components/ui/badge'

/**
 * YouTube-like progress bar for live streams
 * Shows current position, buffer, and seekable range
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
    // Poll every 5 seconds
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
  const { pos, last_ts, first_ts, buffer_pieces } = livepos

  // Calculate percentages for the progress bar
  const totalRange = last_ts - first_ts
  const currentPos = pos - first_ts
  const currentPercent = totalRange > 0 ? (currentPos / totalRange) * 100 : 0
  
  // Buffer is relative to current position
  // buffer_pieces indicates how much is buffered ahead
  const bufferAhead = buffer_pieces || 0
  const bufferPercent = Math.min(currentPercent + 10, 100) // Show ~10% buffer ahead visually

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        {is_live && (
          <Badge variant="destructive" className="text-xs px-1.5 py-0">
            LIVE
          </Badge>
        )}
        <div className="flex-1 text-xs text-muted-foreground">
          {formatTimestamp(pos)} / {formatTimestamp(last_ts)}
        </div>
      </div>
      
      {/* Progress bar container */}
      <div className="relative h-1.5 w-full bg-muted rounded-full overflow-hidden">
        {/* Buffered range (lighter) */}
        <div
          className="absolute h-full bg-primary/30 transition-all duration-300"
          style={{ width: `${bufferPercent}%` }}
        />
        
        {/* Current position (darker red, YouTube-like) */}
        <div
          className="absolute h-full bg-red-600 transition-all duration-300"
          style={{ width: `${currentPercent}%` }}
        />
      </div>
      
      {/* Seekable range info */}
      <div className="text-xs text-muted-foreground">
        Seekable: {formatTimestamp(first_ts)} - {formatTimestamp(last_ts)}
      </div>
    </div>
  )
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

export default StreamProgressBar
