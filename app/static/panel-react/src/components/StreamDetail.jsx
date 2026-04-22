import React, { useState, useEffect, useMemo, useCallback } from 'react'
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
import {
  X,
  StopCircle,
  Trash2,
  ExternalLink,
  ChevronDown,
  ChevronUp,
  Users,
  Download,
  Upload,
  Activity,
} from 'lucide-react'
import { Line } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js'
import { formatTime, formatBytesPerSecond, formatBitrate } from '../utils/formatters'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Badge } from '@/components/ui/badge'
import StreamTimelineGraphic from './StreamTimelineGraphic'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend,
)

const DETAILS_RECONNECT_DELAY_MS = 2000
const MAX_STATS_POINTS = 360

function toNumber(value) {
  const parsed = Number.parseFloat(String(value ?? ''))
  return Number.isFinite(parsed) ? parsed : null
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

function buildStreamDetailsSseUrl({ orchUrl, streamId, apiKey }) {
  const streamUrl = new URL(`${orchUrl}/api/v1/streams/${encodeURIComponent(streamId)}/details/stream`)
  streamUrl.searchParams.set('since_seconds', '3600')
  streamUrl.searchParams.set('interval_seconds', '1.5')
  if (apiKey) {
    streamUrl.searchParams.set('api_key', apiKey)
    streamUrl.searchParams.set('token', apiKey)
  }
  return streamUrl
}

function mergeStatSamples(prevStats, incomingStats) {
  const nextStats = [...prevStats]
  const seenByTimestamp = new Map(prevStats.map((sample) => [sample.ts, sample]))

  for (const sample of Array.isArray(incomingStats) ? incomingStats : []) {
    if (!sample || !sample.ts) continue
    seenByTimestamp.set(sample.ts, sample)
  }

  for (const [, sample] of seenByTimestamp.entries()) {
    nextStats.push(sample)
  }

  const unique = Array.from(new Map(nextStats.map((sample) => [sample.ts, sample])).values())
  unique.sort((a, b) => new Date(a.ts).getTime() - new Date(b.ts).getTime())

  if (unique.length <= MAX_STATS_POINTS) return unique
  return unique.slice(unique.length - MAX_STATS_POINTS)
}

function formatTimelineTimestamp(value) {
  const parsed = Number.parseInt(String(value ?? ''), 10)
  if (!Number.isFinite(parsed)) return 'N/A'
  try {
    return new Date(parsed * 1000).toLocaleTimeString()
  } catch {
    return String(parsed)
  }
}


function StreamDetail({ stream, orchUrl, apiKey, onStopStream, onDeleteEngine, onClose }) {
  const [stats, setStats] = useState([])
  const [loading, setLoading] = useState(true)
  const [liveConnected, setLiveConnected] = useState(false)
  const [extendedStats, setExtendedStats] = useState(null)
  const [isExtendedStatsOpen, setIsExtendedStatsOpen] = useState(false)
  const [clients, setClients] = useState([])
  const [liveposData, setLiveposData] = useState({
    has_livepos: Boolean(stream?.livepos),
    is_live: Boolean(stream?.is_live),
    livepos: stream?.livepos || null,
  })

  useEffect(() => {
    if (!stream?.id) return undefined

    let eventSource = null
    let reconnectTimer = null
    let closed = false

    const matchesStream = (targetId) => {
      if (!targetId || !stream?.id) return false
      return targetId === stream.id || stream.id.startsWith(`${targetId}|`)
    }

    const applySnapshot = (payload) => {
      if (!payload || !matchesStream(payload.stream_id)) return

      if (Array.isArray(payload.stats)) {
        setStats((prev) => mergeStatSamples(prev, payload.stats))
      }

      if (payload.extended_stats) {
        setExtendedStats(payload.extended_stats)
      }

      if (Array.isArray(payload.clients)) {
        setClients(payload.clients)
      }

      const latestFromStats = Array.isArray(payload.stats) && payload.stats.length > 0
        ? payload.stats[payload.stats.length - 1]?.livepos
        : null
      const nextLivepos = payload.livepos || latestFromStats || null
      if (nextLivepos) {
        setLiveposData({
          has_livepos: true,
          is_live: true,
          livepos: nextLivepos,
        })
      }


      setLoading(false)
      setLiveConnected(true)
    }

    const connect = () => {
      if (closed) return
      if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
        setLoading(false)
        setLiveConnected(false)
        return
      }

      eventSource = new EventSource(buildStreamDetailsSseUrl({ orchUrl, streamId: stream.id, apiKey }).toString())

      const handleSse = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          const type = String(parsed?.type || event.type || '').trim()
          const payload = parsed?.payload || {}

          if ((type === 'stream_details_snapshot' || !type) && matchesStream(payload?.stream_id)) {
            applySnapshot(payload)
            return
          }

          if (type === 'stream_metrics' && matchesStream(payload?.stream_id)) {
            if (Array.isArray(payload?.stats)) {
              setStats((prev) => mergeStatSamples(prev, payload.stats))
            }


            if (payload?.livepos) {
              setLiveposData({
                has_livepos: true,
                is_live: true,
                livepos: payload.livepos,
              })
            }
            return
          }

          if (type === 'client_update' && matchesStream(payload?.stream_id) && payload?.client) {
            setClients((prev) => {
              const next = [...prev]
              const key = payload.client.client_id || payload.client.ip_address
              const idx = next.findIndex((client) => (client.client_id || client.ip_address) === key)
              if (idx >= 0) next[idx] = { ...next[idx], ...payload.client }
              else next.push(payload.client)
              return next
            })
            return
          }

          if (type === 'client_connected' && matchesStream(payload?.stream_id) && payload?.client) {
            setClients((prev) => [...prev, payload.client])
            return
          }

          if (type === 'client_disconnected' && matchesStream(payload?.stream_id)) {
            const disconnectedId = String(payload?.client_id || payload?.client?.client_id || '').trim()
            if (disconnectedId) {
              setClients((prev) => prev.filter((client) => String(client?.client_id || '').trim() !== disconnectedId))
            }
          }
        } catch {
          // Ignore malformed frames and keep current state.
        }
      }

      eventSource.addEventListener('stream_details_snapshot', handleSse)
      eventSource.addEventListener('stream_metrics', handleSse)
      eventSource.addEventListener('client_update', handleSse)
      eventSource.addEventListener('client_connected', handleSse)
      eventSource.addEventListener('client_disconnected', handleSse)
      eventSource.onmessage = handleSse
      eventSource.onopen = () => setLiveConnected(true)
      eventSource.onerror = () => {
        setLiveConnected(false)
        if (eventSource) {
          eventSource.close()
          eventSource = null
        }
        if (!closed) {
          reconnectTimer = window.setTimeout(connect, DETAILS_RECONNECT_DELAY_MS)
        }
      }
    }

    setLoading(true)
    connect()

    return () => {
      closed = true
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer)
      }
      if (eventSource) {
        eventSource.close()
      }
    }
  }, [stream?.id, orchUrl, apiKey])

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

  const liveposTimeline = liveposData?.livepos || {}
  const timelineFirstTs = Number.parseInt(String(liveposTimeline.first_ts ?? liveposTimeline.live_first ?? ''), 10)
  const timelineLastTs = Number.parseInt(String(liveposTimeline.last_ts ?? liveposTimeline.live_last ?? ''), 10)
  const timelinePos = Number.parseInt(String(liveposTimeline.pos ?? ''), 10)



  const chartData = useMemo(() => ({
    labels: stats.map((sample) => new Date(sample.ts).toLocaleTimeString()),
    datasets: [
      {
        label: 'Download (MB/s)',
        data: stats.map((sample) => (sample.speed_down || 0) / 1024),
        borderColor: 'rgb(16, 185, 129)',
        backgroundColor: 'rgba(16, 185, 129, 0.2)',
        yAxisID: 'y',
        tension: 0.3,
      },
      {
        label: 'Upload (MB/s)',
        data: stats.map((sample) => (sample.speed_up || 0) / 1024),
        borderColor: 'rgb(59, 130, 246)',
        backgroundColor: 'rgba(59, 130, 246, 0.2)',
        yAxisID: 'y',
        tension: 0.3,
      },
      {
        label: 'Peers',
        data: stats.map((sample) => sample.peers || 0),
        borderColor: 'rgb(245, 158, 11)',
        backgroundColor: 'rgba(245, 158, 11, 0.2)',
        yAxisID: 'y1',
        tension: 0.3,
      },
    ],
  }), [stats])

  const chartOptions = useMemo(() => ({
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
        text: 'Realtime Stream Metrics',
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
  }), [])

  const latestSample = stats.length > 0 ? stats[stats.length - 1] : null

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
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={liveposData?.is_live ? 'success' : 'secondary'}>
            {liveposData?.is_live ? 'Live stream' : 'Non-live stream'}
          </Badge>
          <Badge variant={liveConnected ? 'success' : 'secondary'}>
            {liveConnected ? 'SSE connected' : 'SSE reconnecting'}
          </Badge>
          {streamControlMode && (
            <Badge variant="outline">{formattedControlMode}</Badge>
          )}
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="rounded-lg border bg-muted/30 px-3 py-2">
            <p className="text-[11px] text-muted-foreground flex items-center gap-1"><Download className="h-3 w-3" />Download</p>
            <p className="text-sm font-semibold">{formatBytesPerSecond(((latestSample?.speed_down ?? stream?.speed_down ?? 0) || 0) * 1024)}</p>
          </div>
          <div className="rounded-lg border bg-muted/30 px-3 py-2">
            <p className="text-[11px] text-muted-foreground flex items-center gap-1"><Upload className="h-3 w-3" />Upload</p>
            <p className="text-sm font-semibold">{formatBytesPerSecond(((latestSample?.speed_up ?? stream?.speed_up ?? 0) || 0) * 1024)}</p>
          </div>
          <div className="rounded-lg border bg-muted/30 px-3 py-2">
            <p className="text-[11px] text-muted-foreground flex items-center gap-1"><Users className="h-3 w-3" />Peers</p>
            <p className="text-sm font-semibold">{latestSample?.peers ?? stream?.peers ?? 'N/A'}</p>
          </div>
          <div className="rounded-lg border bg-muted/30 px-3 py-2">
            <p className="text-[11px] text-muted-foreground flex items-center gap-1"><Activity className="h-3 w-3" />Target Bitrate</p>
            <p className="text-sm font-semibold">{(stream?.bitrate || latestSample?.bitrate) ? formatBitrate(stream?.bitrate || latestSample?.bitrate) : '—'}</p>
          </div>
        </div>

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
                <p className="text-xs text-muted-foreground">Target Bitrate</p>
                <p className="text-sm font-medium">
                  {stream.bitrate ? formatBitrate(stream.bitrate) : 'N/A'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Started At</p>
                <p className="text-sm font-medium">{formatTime(stream.started_at)}</p>
              </div>
              {extendedStats?.title && (
                <div className="col-span-2">
                  <p className="text-xs text-muted-foreground">Title</p>
                  <p className="text-sm font-medium break-all">{extendedStats.title}</p>
                </div>
              )}
              {extendedStats?.infohash && (
                <div className="col-span-2">
                  <p className="text-xs text-muted-foreground">Infohash</p>
                  <p className="text-sm font-medium break-all">{extendedStats.infohash}</p>
                </div>
              )}
              {extendedStats?.content_type && (
                <div>
                  <p className="text-xs text-muted-foreground">Content Type</p>
                  <p className="text-sm font-medium">{extendedStats.content_type}</p>
                </div>
              )}
            </div>

            <div className="flex flex-wrap gap-4">
              {stream.stat_url && (
                <a
                  href={stream.stat_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-primary hover:underline flex items-center gap-1"
                >
                  Statistics URL <ExternalLink className="h-3 w-3" />
                </a>
              )}
              {stream.command_url && (
                <a
                  href={stream.command_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-sm text-primary hover:underline flex items-center gap-1"
                >
                  Command URL <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>
          </CollapsibleContent>
        </Collapsible>

        <div className="rounded-xl border bg-muted/30 p-4 space-y-4">
          <p className="text-sm font-semibold">Stream timeline & client positions</p>
          <StreamTimelineGraphic
            streamId={stream.id}
            livepos={liveposData?.livepos}
            clients={clients}
            isLive={Boolean(liveposData?.is_live)}
          />

        </div>

        <div className="h-80 rounded-xl border bg-muted/20 p-3">
          {stats.length > 0 ? (
            <Line data={chartData} options={chartOptions} />
          ) : (
            <div className="flex items-center justify-center h-full">
              <p className="text-muted-foreground">
                {loading ? 'Waiting for stream snapshots...' : 'No statistics available'}
              </p>
            </div>
          )}
        </div>

        <div className="rounded-xl border bg-background/95 p-3 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-background/75 space-y-2">
          <div className="flex flex-wrap gap-2">

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


        </div>
      </CardContent>
    </Card>
  )
}

export default StreamDetail
