import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { X, StopCircle, Trash2, ExternalLink, ChevronDown, ChevronUp, Pause, Save, PlayCircle } from 'lucide-react'
import { Line } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
} from 'chart.js'
import { formatTime, formatBytes } from '../utils/formatters'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Badge } from '@/components/ui/badge'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
)

function StreamDetail({ stream, orchUrl, apiKey, onStopStream, onDeleteEngine, onClose }) {
  const [stats, setStats] = useState([])
  const [loading, setLoading] = useState(false)
  const [extendedStats, setExtendedStats] = useState(null)
  const [extendedStatsLoading, setExtendedStatsLoading] = useState(false)
  const [isExtendedStatsOpen, setIsExtendedStatsOpen] = useState(false)
  const [liveposData, setLiveposData] = useState(null)
  const [seekValue, setSeekValue] = useState(null)
  const [seekLoading, setSeekLoading] = useState(false)
  const [seekError, setSeekError] = useState(null)
  const [seekMessage, setSeekMessage] = useState(null)
  const [isPaused, setIsPaused] = useState(Boolean(stream?.paused))
  const [controlLoading, setControlLoading] = useState(false)
  const [controlError, setControlError] = useState(null)
  const [controlMessage, setControlMessage] = useState(null)
  const [saveDialogOpen, setSaveDialogOpen] = useState(false)
  const [savePath, setSavePath] = useState('')
  const [saveIndex, setSaveIndex] = useState('0')

  const fetchStats = useCallback(async () => {
    if (!stream) return
    
    setLoading(true)
    try {
      const since = new Date(Date.now() - 60 * 60 * 1000).toISOString()
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }
      
      const response = await fetch(
        `${orchUrl}/api/v1/streams/${encodeURIComponent(stream.id)}/stats?since=${encodeURIComponent(since)}`,
        { headers }
      )
      
      if (response.ok) {
        const data = await response.json()
        setStats(data)
      }
    } catch (err) {
      console.error('Failed to fetch stats:', err)
    } finally {
      setLoading(false)
    }
  }, [stream, orchUrl, apiKey])

  const fetchExtendedStats = useCallback(async () => {
    if (!stream) return
    
    setExtendedStatsLoading(true)
    try {
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }
      
      const response = await fetch(
        `${orchUrl}/api/v1/streams/${encodeURIComponent(stream.id)}/extended-stats`,
        { headers }
      )
      
      if (response.ok) {
        const data = await response.json()
        setExtendedStats(data)
      }
    } catch (err) {
      console.error('Failed to fetch extended stats:', err)
    } finally {
      setExtendedStatsLoading(false)
    }
  }, [stream, orchUrl, apiKey])

  const fetchLivepos = useCallback(async () => {
    if (!stream) return

    try {
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }

      const response = await fetch(
        `${orchUrl}/api/v1/streams/${encodeURIComponent(stream.id)}/livepos`,
        { headers }
      )

      if (response.ok) {
        const data = await response.json()
        setLiveposData(data)
      }
    } catch (err) {
      console.error('Failed to fetch livepos:', err)
    }
  }, [stream, orchUrl, apiKey])

  useEffect(() => {
    fetchStats()
    fetchExtendedStats()
    fetchLivepos()
    const interval = setInterval(fetchStats, 10000) // Refresh every 10 seconds
    const liveposInterval = setInterval(fetchLivepos, 5000)
    return () => {
      clearInterval(interval)
      clearInterval(liveposInterval)
    }
  }, [fetchStats, fetchExtendedStats, fetchLivepos])

  useEffect(() => {
    const posRaw = liveposData?.livepos?.pos
    const posValue = Number.parseInt(String(posRaw ?? ''), 10)
    if (Number.isFinite(posValue)) {
      setSeekValue(posValue)
    }
  }, [liveposData?.livepos?.pos])

  useEffect(() => {
    setIsPaused(Boolean(stream?.paused))
  }, [stream?.paused])

  const formatTimelineTimestamp = (value) => {
    const parsed = Number.parseInt(String(value ?? ''), 10)
    if (!Number.isFinite(parsed)) return 'N/A'
    try {
      return new Date(parsed * 1000).toLocaleTimeString()
    } catch {
      return String(parsed)
    }
  }

  const handleSeekCommit = async () => {
    const timeline = liveposData?.livepos || {}
    const firstTs = Number.parseInt(String(timeline.first_ts ?? timeline.live_first ?? ''), 10)
    const lastTs = Number.parseInt(String(timeline.last_ts ?? timeline.live_last ?? ''), 10)
    const selected = Number.parseInt(String(seekValue ?? ''), 10)

    if (!Number.isFinite(firstTs) || !Number.isFinite(lastTs) || !Number.isFinite(selected)) {
      return
    }

    // LIVESEEK is intended for catch-up (backwards seek) on live broadcasts.
    if (selected >= lastTs) {
      return
    }

    setSeekLoading(true)
    setSeekError(null)
    setSeekMessage(null)
    try {
      const headers = {
        'Content-Type': 'application/json',
      }
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }

      const response = await fetch(
        `${orchUrl}/api/v1/streams/${encodeURIComponent(stream.id)}/seek`,
        {
          method: 'POST',
          headers,
          body: JSON.stringify({ target_timestamp: selected }),
        }
      )

      let payload = null
      try {
        payload = await response.json()
      } catch {
        payload = null
      }

      if (!response.ok) {
        throw new Error(payload?.detail || `HTTP ${response.status}: ${response.statusText}`)
      }

      if (payload?.status === 'seek_issued') {
        setSeekMessage(`Seek issued for ${formatTimelineTimestamp(selected)}`)
      } else {
        setSeekMessage(`Seek applied to ${formatTimelineTimestamp(selected)}`)
      }
      fetchLivepos()
    } catch (err) {
      setSeekError(err.message || String(err))
    } finally {
      setSeekLoading(false)
    }
  }

  const handlePauseResume = async (shouldPause) => {
    if (!apiKey) {
      setControlError('Set API key in Settings to use media controls.')
      return
    }

    setControlLoading(true)
    setControlError(null)
    setControlMessage(null)

    try {
      const action = shouldPause ? 'pause' : 'resume'
      const response = await fetch(
        `${orchUrl}/api/v1/streams/${encodeURIComponent(stream.id)}/${action}`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${apiKey}`,
          },
        },
      )

      let payload = null
      try {
        payload = await response.json()
      } catch {
        payload = null
      }

      if (!response.ok) {
        throw new Error(payload?.detail || `HTTP ${response.status}: ${response.statusText}`)
      }

      setIsPaused(shouldPause)
      setControlMessage(shouldPause ? 'Stream paused.' : 'Stream resumed.')
    } catch (err) {
      setControlError(err?.message || 'Failed to update playback state')
    } finally {
      setControlLoading(false)
    }
  }

  const handleSaveStream = async () => {
    if (!apiKey) {
      setControlError('Set API key in Settings to use media controls.')
      return
    }

    const normalizedPath = String(savePath || '').trim()
    if (!normalizedPath) {
      setControlError('Save path is required.')
      return
    }

    const parsedIndex = Number.parseInt(String(saveIndex || '0'), 10)
    if (!Number.isFinite(parsedIndex) || parsedIndex < 0) {
      setControlError('Save index must be a non-negative integer.')
      return
    }

    setControlLoading(true)
    setControlError(null)
    setControlMessage(null)

    try {
      const response = await fetch(
        `${orchUrl}/api/v1/streams/${encodeURIComponent(stream.id)}/save`,
        {
          method: 'POST',
          headers: {
            Authorization: `Bearer ${apiKey}`,
            'Content-Type': 'application/json',
          },
          body: JSON.stringify({
            path: normalizedPath,
            index: parsedIndex,
            infohash: resolvedInfohash || undefined,
          }),
        },
      )

      let payload = null
      try {
        payload = await response.json()
      } catch {
        payload = null
      }

      if (!response.ok) {
        throw new Error(payload?.detail || `HTTP ${response.status}: ${response.statusText}`)
      }

      setControlMessage(`Save command issued for index ${parsedIndex}.`)
      setSaveDialogOpen(false)
    } catch (err) {
      setControlError(err?.message || 'Failed to issue save command')
    } finally {
      setControlLoading(false)
    }
  }

  // AceStream API returns speed in KB/s, so we divide by 1024 to convert to MB/s
  const chartData = {
    labels: stats.map(s => new Date(s.ts).toLocaleTimeString()),
    datasets: [
      {
        label: 'Download (MB/s)',
        data: stats.map(s => (s.speed_down || 0) / 1024),
        borderColor: 'rgb(75, 192, 192)',
        backgroundColor: 'rgba(75, 192, 192, 0.2)',
        yAxisID: 'y',
      },
      {
        label: 'Upload (MB/s)',
        data: stats.map(s => (s.speed_up || 0) / 1024),
        borderColor: 'rgb(255, 99, 132)',
        backgroundColor: 'rgba(255, 99, 132, 0.2)',
        yAxisID: 'y',
      },
      {
        label: 'Peers',
        data: stats.map(s => s.peers || 0),
        borderColor: 'rgb(153, 102, 255)',
        backgroundColor: 'rgba(153, 102, 255, 0.2)',
        yAxisID: 'y1',
      },
    ],
  }

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index',
      intersect: false,
    },
    plugins: {
      legend: {
        position: 'top',
      },
      title: {
        display: true,
        text: 'Stream Statistics (Last Hour)',
      },
    },
    scales: {
      y: {
        type: 'linear',
        display: true,
        position: 'left',
        title: {
          display: true,
          text: 'Speed (MB/s)',
        },
      },
      y1: {
        type: 'linear',
        display: true,
        position: 'right',
        title: {
          display: true,
          text: 'Peers',
        },
        grid: {
          drawOnChartArea: false,
        },
      },
    },
  }

  const streamLabels = stream?.labels || {}
  const streamControlMode = streamLabels['proxy.control_mode'] || null
  const resolvedInfohash = streamLabels['stream.resolved_infohash'] || null
  const normalizedControlMode = String(streamControlMode || '')
    .trim()
    .toUpperCase()
    .replace(/[^A-Z0-9]+/g, '_')
  const hasApiControlLabel = normalizedControlMode.includes('API')
  const formattedControlMode = hasApiControlLabel
    ? 'API Mode'
    : normalizedControlMode.includes('HTTP')
      ? 'HTTP Mode'
      : streamControlMode
  const hasNoEngineControlLinks = !stream?.stat_url && !stream?.command_url
  const isApiMode = hasApiControlLabel || hasNoEngineControlLinks
  const rawDeadReason = [
    stream?.dead_reason,
    stream?.last_error,
    streamLabels['stream.dead_reason'],
    streamLabels['stream.last_error'],
    streamLabels['stream.stop_reason'],
    streamLabels['stream.end_reason'],
  ].find((value) => typeof value === 'string' && value.trim().length > 0) || ''
  const deadReasonText = String(rawDeadReason || '').trim()
  const normalizedDeadReason = deadReasonText.toLowerCase()
  const isDownloadStopped = normalizedDeadReason.includes('download_stopped') || normalizedDeadReason.includes('download stopped')
  const showMissingControlFlowHint = !isApiMode
  const showLinksBlock = Boolean(
    stream.stat_url
    || stream.command_url
    || showMissingControlFlowHint,
  )
  const liveposTimeline = liveposData?.livepos || {}
  const timelineFirstTs = Number.parseInt(String(liveposTimeline.first_ts ?? liveposTimeline.live_first ?? ''), 10)
  const timelineLastTs = Number.parseInt(String(liveposTimeline.last_ts ?? liveposTimeline.live_last ?? ''), 10)
  const timelinePos = Number.parseInt(String(liveposTimeline.pos ?? ''), 10)
  const canSeekTimeline = Boolean(
    liveposData?.has_livepos
    && liveposData?.is_live
    && Number.isFinite(timelineFirstTs)
    && Number.isFinite(timelineLastTs)
    && timelineLastTs > timelineFirstTs
  )

  return (
    <Card>
      <CardHeader>
        <div className="flex justify-between items-start">
          <CardTitle className="text-2xl">Stream Details</CardTitle>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        {isDownloadStopped && (
          <div className="rounded-md border border-rose-300 bg-rose-50 p-3 dark:border-rose-800 dark:bg-rose-950/30">
            <div className="flex items-center gap-2">
              <Badge variant="destructive">Download Stopped</Badge>
              <p className="text-sm font-medium text-rose-700 dark:text-rose-300">AceStream download stopped event detected</p>
            </div>
            {deadReasonText && (
              <p className="mt-2 text-xs text-rose-700 dark:text-rose-300">Reason: {deadReasonText}</p>
            )}
          </div>
        )}

        <Collapsible open={isExtendedStatsOpen} onOpenChange={setIsExtendedStatsOpen}>
          <CollapsibleTrigger asChild>
            <Button variant="outline" className="w-full flex justify-between items-center">
              <span className="font-semibold">Stream Information</span>
              {isExtendedStatsOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="mt-4 space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">Stream ID</p>
                <p className="text-sm font-medium break-all">{stream.id}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Engine</p>
                <p className="text-sm font-medium">
                  {stream.container_name || stream.container_id?.slice(0, 12) || 'N/A'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Started At</p>
                <p className="text-sm font-medium">{formatTime(stream.started_at)}</p>
              </div>
              {streamControlMode && (
                <div>
                  <p className="text-xs text-muted-foreground">Control Mode</p>
                  <p className="text-sm font-medium">{formattedControlMode}</p>
                </div>
              )}
              {resolvedInfohash && (
                <div className="col-span-2">
                  <p className="text-xs text-muted-foreground">Resolved Infohash</p>
                  <p className="text-sm font-medium break-all">{resolvedInfohash}</p>
                </div>
              )}
              
              {/* Extended Stats from analyze_content API */}
              {/* extended stats loading indicator removed to reduce UI clutter */}
              
              {extendedStats && (
                <>
                  {extendedStats.title && (
                    <div className="col-span-2">
                      <p className="text-xs text-muted-foreground">Title</p>
                      <p className="text-sm font-medium break-all">{extendedStats.title}</p>
                    </div>
                  )}
                  {extendedStats.content_type && (
                    <div>
                      <p className="text-xs text-muted-foreground">Content Type</p>
                      <p className="text-sm font-medium">{extendedStats.content_type}</p>
                    </div>
                  )}
                  {extendedStats.transport_type && (
                    <div>
                      <p className="text-xs text-muted-foreground">Transport Type</p>
                      <p className="text-sm font-medium">{extendedStats.transport_type}</p>
                    </div>
                  )}
                  {extendedStats.infohash && (
                    <div className="col-span-2">
                      <p className="text-xs text-muted-foreground">Infohash</p>
                      <p className="text-sm font-medium break-all">{extendedStats.infohash}</p>
                    </div>
                  )}
                  {extendedStats.is_live !== undefined && (
                    <div>
                      <p className="text-xs text-muted-foreground">Live Stream</p>
                      <Badge variant={extendedStats.is_live ? "success" : "secondary"}>
                        {extendedStats.is_live ? 'Yes' : 'No'}
                      </Badge>
                    </div>
                  )}
                  {extendedStats.mime && (
                    <div>
                      <p className="text-xs text-muted-foreground">MIME Type</p>
                      <p className="text-sm font-medium">{extendedStats.mime}</p>
                    </div>
                  )}
                  {extendedStats.categories && extendedStats.categories.length > 0 && (
                    <div className="col-span-2">
                      <p className="text-xs text-muted-foreground">Categories</p>
                      <div className="flex gap-2 flex-wrap mt-1">
                        {extendedStats.categories.map((cat, idx) => (
                          <Badge key={idx} variant="outline">{cat}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {extendedStats.suggested_filename && (
                    <div className="col-span-2">
                      <p className="text-xs text-muted-foreground">Suggested Filename</p>
                      <p className="text-sm font-medium break-all">{extendedStats.suggested_filename}</p>
                    </div>
                  )}
                  {extendedStats.media_files_count !== undefined && (
                    <div>
                      <p className="text-xs text-muted-foreground">Media Files Count</p>
                      <p className="text-sm font-medium">{extendedStats.media_files_count}</p>
                    </div>
                  )}
                </>
              )}
            </div>

            {showLinksBlock && (
              <div className="flex flex-wrap gap-4">
                {stream.stat_url ? (
                  <a
                    href={stream.stat_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-primary hover:underline flex items-center gap-1"
                  >
                    Statistics URL <ExternalLink className="h-3 w-3" />
                  </a>
                ) : showMissingControlFlowHint ? (
                  <span className="text-sm text-muted-foreground">Statistics URL not available in this control flow</span>
                ) : null}
                {stream.command_url ? (
                  <a
                    href={stream.command_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-sm text-primary hover:underline flex items-center gap-1"
                  >
                    Command URL <ExternalLink className="h-3 w-3" />
                  </a>
                ) : showMissingControlFlowHint ? (
                  <span className="text-sm text-muted-foreground">Command URL not available in this control flow</span>
                ) : null}
              </div>
            )}
          </CollapsibleContent>
        </Collapsible>

        <div className="border-t pt-4">
          <div className="mb-4 rounded-md border p-3 bg-muted/30 space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-sm font-semibold">Live Timeline (Catch-up)</p>
              <Badge variant={liveposData?.is_live ? 'success' : 'secondary'}>
                {liveposData?.is_live ? 'Live' : 'Not live'}
              </Badge>
            </div>

            {canSeekTimeline ? (
              <>
                <input
                  type="range"
                  min={timelineFirstTs}
                  max={timelineLastTs}
                  step={1}
                  value={seekValue ?? timelinePos ?? timelineLastTs}
                  onChange={(e) => setSeekValue(Number.parseInt(e.target.value, 10))}
                  onMouseUp={handleSeekCommit}
                  onTouchEnd={handleSeekCommit}
                  disabled={seekLoading}
                  className="w-full"
                />
                <div className="grid grid-cols-3 gap-2 text-xs text-muted-foreground">
                  <div>
                    <p>Window Start</p>
                    <p className="font-medium text-foreground">{formatTimelineTimestamp(timelineFirstTs)}</p>
                  </div>
                  <div>
                    <p>Selected</p>
                    <p className="font-medium text-foreground">{formatTimelineTimestamp(seekValue)}</p>
                  </div>
                  <div>
                    <p>Live Edge</p>
                    <p className="font-medium text-foreground">{formatTimelineTimestamp(timelineLastTs)}</p>
                  </div>
                </div>
              </>
            ) : (
              <p className="text-xs text-muted-foreground">Live timeline is unavailable for this stream.</p>
            )}

            {!hasApiControlLabel && !hasNoEngineControlLinks && (
              <p className="text-xs text-muted-foreground">LIVESEEK requires API mode.</p>
            )}
            {seekLoading && <p className="text-xs text-muted-foreground">Applying seek...</p>}
            {seekMessage && <p className="text-xs text-green-600 dark:text-green-400">{seekMessage}</p>}
            {seekError && <p className="text-xs text-destructive">{seekError}</p>}
          </div>

          <div className="h-80">
            {stats.length > 0 ? (
              <Line data={chartData} options={chartOptions} />
            ) : (
              <div className="flex items-center justify-center h-full">
                <p className="text-muted-foreground">
                  {loading ? 'Loading statistics...' : 'No statistics available'}
                </p>
              </div>
            )}
          </div>
        </div>

        <div className="space-y-3">
          {isApiMode ? (
            <div className="flex flex-wrap gap-3">
              <Button
                variant="outline"
                disabled={controlLoading || !apiKey}
                onClick={() => handlePauseResume(!isPaused)}
                className="flex items-center gap-2"
              >
                {isPaused ? <PlayCircle className="h-4 w-4" /> : <Pause className="h-4 w-4" />}
                {isPaused ? 'Resume' : 'Pause'}
              </Button>
              <Button
                variant="outline"
                disabled={controlLoading || !apiKey}
                onClick={() => setSaveDialogOpen(true)}
                className="flex items-center gap-2"
              >
                <Save className="h-4 w-4" />
                Save
              </Button>
            </div>
          ) : (
            <p className="text-xs text-muted-foreground">PAUSE/RESUME/SAVE require API mode.</p>
          )}

          <div className="flex flex-wrap gap-3">
            <Button
              variant="destructive"
              onClick={() => onStopStream(stream.id, stream.container_id)}
              className="flex items-center gap-2"
            >
              <StopCircle className="h-4 w-4" />
              Stop Stream
            </Button>
            <Button
              variant="outline"
              onClick={() => onDeleteEngine(stream.container_id)}
              className="flex items-center gap-2 text-destructive hover:text-destructive"
            >
              <Trash2 className="h-4 w-4" />
              Delete Engine
            </Button>
          </div>

          {controlLoading && <p className="text-xs text-muted-foreground">Sending control command...</p>}
          {controlMessage && <p className="text-xs text-green-600 dark:text-green-400">{controlMessage}</p>}
          {controlError && <p className="text-xs text-destructive">{controlError}</p>}

          <Dialog open={saveDialogOpen} onOpenChange={setSaveDialogOpen}>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Save Stream File</DialogTitle>
                <DialogDescription>
                  Issue SAVE for this stream to store a file on disk from the active AceStream session.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-3">
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">Destination path</p>
                  <Input
                    value={savePath}
                    onChange={(e) => setSavePath(e.target.value)}
                    placeholder="/downloads"
                  />
                </div>
                <div className="space-y-1">
                  <p className="text-xs text-muted-foreground">File index</p>
                  <Input
                    type="number"
                    min="0"
                    step="1"
                    value={saveIndex}
                    onChange={(e) => setSaveIndex(e.target.value)}
                    placeholder="0"
                  />
                </div>
              </div>
              <DialogFooter>
                <Button variant="outline" onClick={() => setSaveDialogOpen(false)}>
                  Cancel
                </Button>
                <Button onClick={handleSaveStream} disabled={controlLoading}>
                  Save Now
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        </div>
      </CardContent>
    </Card>
  )
}

export default StreamDetail
