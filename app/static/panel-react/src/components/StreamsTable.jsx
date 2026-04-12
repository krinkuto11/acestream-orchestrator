import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  Activity,
  ChevronDown,
  ChevronUp,
  Clock,
  ExternalLink,
  Download,
  Pause,
  PlayCircle,
  Save,
  Server,
  StopCircle,
  Trash2,
  Upload,
  Users,
} from 'lucide-react'
import { formatTime, formatBytesPerSecond, formatBitrate } from '../utils/formatters'
import StreamTimelineGraphic from './StreamTimelineGraphic'

const TRUNCATED_CONTAINER_ID_LENGTH = 12
const TRUNCATED_CLIENT_ID_LENGTH = 16
const DETAILS_RECONNECT_DELAY_MS = 2000
const SESSION_GAP_RESET_MS = 1 * 60 * 1000
const SESSION_IDENTITY_RETENTION_MS = 10 * 60 * 1000

function toNumber(value) {
  const parsed = Number.parseFloat(String(value ?? ''))
  return Number.isFinite(parsed) ? parsed : null
}

function toEpochSeconds(value) {
  const parsed = toNumber(value)
  if (!Number.isFinite(parsed) || parsed <= 0) return null
  if (parsed > 1e12) return parsed / 1000
  return parsed
}


function formatUptime(startedAt) {
  const startedMs = new Date(startedAt || 0).getTime()
  if (!Number.isFinite(startedMs) || startedMs <= 0) return 'N/A'

  const totalSeconds = Math.max(0, Math.floor((Date.now() - startedMs) / 1000))
  const hours = Math.floor(totalSeconds / 3600)
  const minutes = Math.floor((totalSeconds % 3600) / 60)
  const seconds = totalSeconds % 60

  if (hours > 0) return `${hours}h ${minutes}m`
  if (minutes > 0) return `${minutes}m ${seconds}s`
  return `${seconds}s`
}

function buildStreamDetailsSseUrl({ orchUrl, streamId, apiKey }) {
  const streamUrl = new URL(`${orchUrl}/api/v1/streams/${encodeURIComponent(streamId)}/details/stream`)
  streamUrl.searchParams.set('since_seconds', '1800')
  streamUrl.searchParams.set('interval_seconds', '1.5')
  if (apiKey) {
    streamUrl.searchParams.set('api_key', apiKey)
    streamUrl.searchParams.set('token', apiKey)
  }
  return streamUrl
}

function mergeStreamSnapshot(baseStream, payload) {
  if (!baseStream) return baseStream

  const next = { ...baseStream }
  const stats = Array.isArray(payload?.stats) ? payload.stats : []
  const latest = stats.length > 0 ? stats[stats.length - 1] : null
  const normalizedStatus = String(payload?.status || '').trim().toLowerCase()

  // Keep lifecycle state stable (started/ended); avoid replacing it with engine metric status strings.
  if (normalizedStatus === 'started' || normalizedStatus === 'ended') {
    next.status = normalizedStatus
  }

  if (latest && typeof latest === 'object') {
    next.peers = latest.peers ?? next.peers
    next.speed_down = latest.speed_down ?? next.speed_down
    next.speed_up = latest.speed_up ?? next.speed_up
    next.downloaded = latest.downloaded ?? next.downloaded
    next.uploaded = latest.uploaded ?? next.uploaded
    if (latest.livepos && typeof latest.livepos === 'object') {
      next.livepos = latest.livepos
    }
  }

  if (payload?.livepos && typeof payload.livepos === 'object') {
    next.livepos = payload.livepos
  }

  if (typeof payload?.paused === 'boolean') {
    next.paused = payload.paused
  }

  return next
}

function resolveClientKey(client, fallback = 'client') {
  return (
    client?.client_id
    || client?.ip_address
    || `${fallback}-${client?.connected_at || 'na'}-${client?.user_agent || 'na'}`
  )
}

function upsertClient(prevClients, incomingClient) {
  if (!incomingClient || typeof incomingClient !== 'object') return prevClients
  const key = resolveClientKey(incomingClient)
  const next = [...prevClients]
  const index = next.findIndex((client) => resolveClientKey(client) === key)
  if (index >= 0) {
    next[index] = { ...next[index], ...incomingClient }
  } else {
    next.push(incomingClient)
  }
  return next
}

function removeClient(prevClients, payload) {
  const clientId = String(
    payload?.client_id
    || payload?.client?.client_id
    || payload?.id
    || '',
  ).trim()
  if (!clientId) return prevClients
  return prevClients.filter((client) => String(client?.client_id || '').trim() !== clientId)
}

function tile(label, value, icon = null) {
  return (
    <div className="rounded-lg border bg-muted/30 px-3 py-2">
      <p className="text-[11px] text-muted-foreground flex items-center gap-1">{icon}{label}</p>
      <p className="text-sm font-semibold text-foreground truncate">{value}</p>
    </div>
  )
}

function formatLagValue(value) {
  const lag = Number.parseFloat(String(value ?? ''))
  if (!Number.isFinite(lag)) return 'N/A'
  if (lag <= 0) return '0.0s'
  if (lag < 0.1) return `${Math.round(lag * 1000)}ms`
  return `${lag.toFixed(2)}s`
}

function isLikelyInfohash(value) {
  return /^[a-f0-9]{40}$/i.test(String(value || '').trim())
}

function getStreamDisplayId(stream) {
  const fromResolved = String(stream?.labels?.['stream.resolved_infohash'] || '').trim()
  if (isLikelyInfohash(fromResolved)) return fromResolved

  const fromKey = String(stream?.key || '').trim()
  if (isLikelyInfohash(fromKey)) return fromKey

  const rawId = String(stream?.id || '').trim()
  const [firstSegment] = rawId.split('|')
  if (isLikelyInfohash(firstSegment)) return firstSegment

  return firstSegment || rawId || 'N/A'
}

function parseTimestampMs(value) {
  const parsed = new Date(value || 0).getTime()
  return Number.isFinite(parsed) ? parsed : 0
}

function normalizeIdentityToken(value) {
  const raw = String(value || '').trim()
  if (!raw) return ''
  return isLikelyInfohash(raw) ? raw.toLowerCase() : raw
}

function getCanonicalStreamIdentity(stream) {
  const fromResolved = normalizeIdentityToken(stream?.labels?.['stream.resolved_infohash'])
  if (fromResolved) return fromResolved

  const fromKey = normalizeIdentityToken(stream?.key)
  if (fromKey) return fromKey

  const rawId = String(stream?.id || '').trim()
  const [firstSegment] = rawId.split('|')
  const fromIdPrefix = normalizeIdentityToken(firstSegment)
  if (fromIdPrefix) return fromIdPrefix

  return normalizeIdentityToken(rawId) || 'unknown-stream'
}

function buildRecoveryMarker({ timestampMs, rawId, reason = 'Recovery' }) {
  const ts = Number.isFinite(timestampMs) && timestampMs > 0 ? timestampMs : Date.now()
  const safeRawId = String(rawId || '').trim() || 'unknown'
  const normalizedReason = String(reason || '').toLowerCase()

  let markerType = 'recovery'
  if (normalizedReason.includes('engine')) {
    markerType = 'engine_switch'
  } else if (normalizedReason.includes('failover')) {
    markerType = 'failover'
  }

  return {
    id: `${safeRawId}-${ts}`,
    time: ts,
    label: reason,
    type: markerType,
  }
}

function mergeDuplicateStreams(entries) {
  if (!Array.isArray(entries) || entries.length === 0) {
    return null
  }

  const sorted = [...entries].sort((a, b) => {
    const aLifecycleTs = Math.max(parseTimestampMs(a?.started_at), parseTimestampMs(a?.ended_at))
    const bLifecycleTs = Math.max(parseTimestampMs(b?.started_at), parseTimestampMs(b?.ended_at))
    return aLifecycleTs - bLifecycleTs
  })
  const latest = sorted[sorted.length - 1]
  const mergedLabels = sorted.reduce((acc, item) => ({ ...acc, ...(item?.labels || {}) }), {})

  const latestStartedTs = sorted.reduce((max, item) => {
    const status = String(item?.status || '').trim().toLowerCase()
    if (status !== 'started') return max
    return Math.max(max, parseTimestampMs(item?.started_at))
  }, 0)

  const latestEndedTs = sorted.reduce((max, item) => {
    const status = String(item?.status || '').trim().toLowerCase()
    if (status !== 'ended' && !item?.ended_at) return max
    const endedRefTs = Math.max(parseTimestampMs(item?.ended_at), parseTimestampMs(item?.started_at))
    return Math.max(max, endedRefTs)
  }, 0)

  const latestUnclosedStartedTs = sorted.reduce((max, item) => {
    const status = String(item?.status || '').trim().toLowerCase()
    if (status !== 'started') return max
    if (item?.ended_at) return max
    return Math.max(max, parseTimestampMs(item?.started_at))
  }, 0)

  const latestPendingFailoverTs = sorted.reduce((max, item) => {
    const status = String(item?.status || '').trim().toLowerCase()
    if (status !== 'pending_failover') return max
    return Math.max(max, parseTimestampMs(item?.started_at), parseTimestampMs(item?.ended_at))
  }, 0)

  const merged = {
    ...latest,
    labels: mergedLabels,
    __identity: getCanonicalStreamIdentity(latest),
  }

  const latestStatus = String(latest?.status || '').trim().toLowerCase()

  // Mark as started only if there is unclosed started evidence newer than the latest ended evidence.
  if (
    latestStatus === 'pending_failover'
    && latestPendingFailoverTs >= latestEndedTs
    && latestPendingFailoverTs >= latestStartedTs
  ) {
    merged.status = 'pending_failover'
  } else if (latestUnclosedStartedTs > 0 && latestUnclosedStartedTs >= latestEndedTs) {
    merged.status = 'started'
  } else if (latestEndedTs > 0 && latestEndedTs >= latestStartedTs) {
    merged.status = 'ended'
  } else {
    merged.status = String(latest?.status || '').trim().toLowerCase() === 'started' ? 'started' : 'ended'
  }

  const seenIds = new Set()
  const recoveryMarkers = []
  sorted.forEach((item, index) => {
    const rawId = String(item?.id || '').trim()
    if (!rawId || seenIds.has(rawId)) return
    seenIds.add(rawId)
    if (index === 0) return

    const previous = sorted[index - 1] || {}
    const previousContainer = String(previous?.container_id || '').trim()
    const nextContainer = String(item?.container_id || '').trim()
    const switchedEngine = Boolean(previousContainer && nextContainer && previousContainer !== nextContainer)

    recoveryMarkers.push(
      buildRecoveryMarker({
        timestampMs: parseTimestampMs(item?.started_at),
        rawId,
        reason: switchedEngine ? 'Engine switch' : 'Recovery',
      }),
    )
  })

  merged.__recoveryMarkers = recoveryMarkers
  return merged
}

function isStreamActiveStatus(status) {
  const normalizedStatus = String(status || '').trim().toLowerCase()
  return normalizedStatus === 'started' || normalizedStatus === 'pending_failover'
}

function StreamStatusBadge({ isActive, isPaused, isPrebuffering, isDownloadStopped }) {
  if (isDownloadStopped) {
    return <Badge variant="destructive">DOWNLOAD STOPPED</Badge>
  }
  if (isActive && isPaused) {
    return <Badge className="bg-amber-500 text-white hover:bg-amber-600 border-transparent">PAUSED</Badge>
  }
  if (isPrebuffering) {
    return <Badge className="bg-orange-500 text-white hover:bg-orange-600 border-transparent">PREBUFFERING</Badge>
  }
  return (
    <Badge variant={isActive ? 'success' : 'secondary'}>{isActive ? 'ACTIVE' : 'ENDED'}</Badge>
  )
}

function ClientSession({ client }) {
  const runway = toNumber(client?.buffer_seconds_behind)

  return (
    <div className="rounded-lg border bg-muted/20 p-3">
      <div className="flex items-start gap-3">
        <Avatar className="h-8 w-8">
          <AvatarFallback className="text-xs">
            <Users className="h-4 w-4" />
          </AvatarFallback>
        </Avatar>
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-2 flex-wrap">
            <p className="text-sm font-medium truncate" title={client.ip_address || client.client_id}>
              {client.ip_address || client.client_id || 'Unknown client'}
            </p>
            <Badge variant="outline" className="text-[10px] bg-indigo-50 dark:bg-indigo-950/20 text-indigo-700 dark:text-indigo-300 border-indigo-200 dark:border-indigo-900">
              Runway {formatLagValue(runway)}
            </Badge>
            {Number.isFinite(streamWindow) && streamWindow > 0 ? (
              <Badge variant="outline" className="text-[10px]">
                Window {formatLagValue(streamWindow)}
              </Badge>
            ) : null}
          </div>
          <p className="text-xs text-muted-foreground truncate" title={client.user_agent}>
            {client.user_agent || 'Unknown agent'}
          </p>
          <p className="text-[11px] text-muted-foreground font-mono">
            {client.client_id && client.client_id.length > TRUNCATED_CLIENT_ID_LENGTH
              ? `${client.client_id.slice(0, TRUNCATED_CLIENT_ID_LENGTH)}...`
              : client.client_id || 'N/A'}
          </p>
        </div>
      </div>
    </div>
  )
}

function StreamCard({
  stream,
  streamIdentity,
  streamSessionKey,
  eventMarkers = [],
  orchUrl,
  apiKey,
  onStopStream,
  onDeleteEngine,
  isSelected,
  onToggleSelect,
  selectable,
}) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [localStream, setLocalStream] = useState(stream)
  const [extendedStats, setExtendedStats] = useState(null)
  const [clients, setClients] = useState([])
  const [clientsLoading, setClientsLoading] = useState(true)
  const [isPaused, setIsPaused] = useState(Boolean(stream.paused))
  const [detailsLive, setDetailsLive] = useState(false)
  const [detailsLoading, setDetailsLoading] = useState(false)
  const [controlLoading, setControlLoading] = useState(false)
  const [controlError, setControlError] = useState(null)
  const [controlMessage, setControlMessage] = useState(null)
  const [saveDialogOpen, setSaveDialogOpen] = useState(false)
  const [savePath, setSavePath] = useState('')
  const [saveIndex, setSaveIndex] = useState('0')

  useEffect(() => {
    setLocalStream(stream)
  }, [stream])

  const streamStatus = String(localStream?.status || '').trim().toLowerCase()
  const isPendingFailover = streamStatus === 'pending_failover'
  const isActive = isStreamActiveStatus(streamStatus)
  const streamIsLive = Boolean(localStream?.livepos?.live_last || localStream?.livepos?.last_ts)
  const labels = localStream?.labels || {}
  const isPrebuffering = String(labels['stream.status_text'] || '').toLowerCase().includes('prebuf')
  const deadReason = String(localStream?.dead_reason || localStream?.last_error || labels['stream.dead_reason'] || '').toLowerCase()
  const isDownloadStopped = deadReason.includes('download_stopped') || deadReason.includes('download stopped')
  const streamControlMode = labels['proxy.control_mode'] || null
  const resolvedInfohash = labels['stream.resolved_infohash'] || null
  const streamModeLabel = String(labels['stream_mode'] || labels['stream.mode'] || '').trim().toUpperCase()
  const streamSourceLabel = String(labels.source || '').trim().toLowerCase()
  const isHlsStream = streamModeLabel === 'HLS' || streamSourceLabel === 'hls_proxy'
  const showStreamWindow = isHlsStream
  const normalizedControlMode = String(streamControlMode || '').trim().toUpperCase().replace(/[^A-Z0-9]+/g, '_')
  const hasApiControlLabel = normalizedControlMode.includes('API')
  const hasNoEngineControlLinks = !localStream?.stat_url && !localStream?.command_url
  const isApiMode = hasApiControlLabel || hasNoEngineControlLinks

  useEffect(() => {
    setIsPaused(Boolean(localStream?.paused))
  }, [localStream?.paused])

  useEffect(() => {
    if (!stream?.id || !isStreamActiveStatus(stream?.status)) return undefined

    let eventSource = null
    let reconnectTimer = null
    let closed = false
    const streamId = stream.id

    const applySnapshot = (payload) => {
      if (!payload || payload.stream_id !== streamId) return
      setLocalStream((prev) => mergeStreamSnapshot(prev, payload))
      if (Array.isArray(payload.clients)) {
        setClients(payload.clients)
        setClientsLoading(false)
      }
      if (payload.extended_stats) {
        setExtendedStats(payload.extended_stats)
      }
      if (isExpanded) {
        setDetailsLoading(false)
      }
      setDetailsLive(true)
    }

    const connect = () => {
      if (closed) return
      if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
        setDetailsLive(false)
        setDetailsLoading(false)
        return
      }

      if (isExpanded) {
        setDetailsLoading(true)
      }
      eventSource = new EventSource(buildStreamDetailsSseUrl({ orchUrl, streamId, apiKey }).toString())

      const handleSse = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          const type = String(parsed?.type || event.type || '').trim()
          const payload = parsed?.payload || {}

          if ((type === 'stream_details_snapshot' || !type) && payload?.stream_id === streamId) {
            applySnapshot(payload)
            return
          }

          if (type === 'stream_metrics' && payload?.stream_id === streamId) {
            setLocalStream((prev) => mergeStreamSnapshot(prev, payload))
            return
          }

          if ((type === 'client_connected' || type === 'client_update') && payload?.stream_id === streamId) {
            const nextClient = payload?.client || payload
            setClients((prev) => upsertClient(prev, nextClient))
            setClientsLoading(false)
            return
          }

          if (type === 'client_disconnected' && payload?.stream_id === streamId) {
            setClients((prev) => removeClient(prev, payload))
            return
          }

          // Compatibility path for full-sync frames from global streams SSE.
          if (type === 'full_sync' && Array.isArray(payload?.streams)) {
            const matchingStream = payload.streams.find((item) => item?.id === streamId)
            if (matchingStream) {
              setLocalStream((prev) => mergeStreamSnapshot(prev, matchingStream))
            }
          }
        } catch {
          // Ignore malformed frames and keep current card state.
        }
      }

      eventSource.addEventListener('stream_details_snapshot', handleSse)
      eventSource.addEventListener('stream_metrics', handleSse)
      eventSource.addEventListener('client_update', handleSse)
      eventSource.addEventListener('client_connected', handleSse)
      eventSource.addEventListener('client_disconnected', handleSse)
      eventSource.onmessage = handleSse
      eventSource.onopen = () => setDetailsLive(true)
      eventSource.onerror = () => {
        setDetailsLive(false)
        if (isExpanded) {
          setDetailsLoading(true)
        }
        if (eventSource) {
          eventSource.close()
          eventSource = null
        }
        if (!closed) {
          reconnectTimer = window.setTimeout(connect, DETAILS_RECONNECT_DELAY_MS)
        }
      }
    }

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
  }, [isExpanded, stream?.id, stream?.status, orchUrl, apiKey])

  const handlePauseResume = useCallback(async (shouldPause) => {
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
      setLocalStream((prev) => ({ ...prev, paused: shouldPause }))
      setControlMessage(shouldPause ? 'Stream paused.' : 'Stream resumed.')
    } catch (err) {
      setControlError(err?.message || 'Failed to update playback state')
    } finally {
      setControlLoading(false)
    }
  }, [apiKey, orchUrl, stream.id])

  const handleSaveStream = useCallback(async () => {
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

      setSaveDialogOpen(false)
      setControlMessage(`Save command issued for index ${parsedIndex}.`)
    } catch (err) {
      setControlError(err?.message || 'Failed to issue save command')
    } finally {
      setControlLoading(false)
    }
  }, [apiKey, orchUrl, stream.id, savePath, saveIndex, resolvedInfohash])

  const displayId = getStreamDisplayId(localStream)
  const title = extendedStats?.title || displayId
  const showDisplayIdSubtitle = Boolean(extendedStats?.title)

  return (
    <Card className="overflow-hidden border-border/80 transition-all duration-300">
      <CardHeader className="pb-3">
        <div className="flex items-start gap-3">
          {selectable && isActive && (
            <div className="pt-1">
              <Checkbox checked={isSelected} onCheckedChange={onToggleSelect} aria-label="Select stream" />
            </div>
          )}
          <div className="min-w-0 flex-1 space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="min-w-0">
                <p className="text-base font-semibold truncate" title={title}>{title}</p>
                {showDisplayIdSubtitle && (
                  <p className="text-xs text-muted-foreground font-mono truncate" title={displayId}>{displayId}</p>
                )}
              </div>
              <div className="flex items-center gap-2">
                <StreamStatusBadge
                  isActive={isActive}
                  isPaused={isPaused}
                  isPrebuffering={isPrebuffering}
                  isDownloadStopped={isDownloadStopped}
                />
                {isPendingFailover && (
                  <Badge className="bg-amber-500 text-white hover:bg-amber-600 border-transparent">FAILOVERING</Badge>
                )}
                <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={() => setIsExpanded((v) => !v)}>
                  {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                </Button>
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
              {tile('Engine', localStream.container_name || localStream.container_id?.slice(0, TRUNCATED_CONTAINER_ID_LENGTH) || 'N/A', <Server className="h-3 w-3" />)}
              {tile('Uptime', formatUptime(localStream.started_at), <Clock className="h-3 w-3" />)}
              {tile('Download', isActive ? formatBytesPerSecond((localStream.speed_down || 0) * 1024) : '—', <Download className="h-3 w-3" />)}
              {tile('Upload', isActive ? formatBytesPerSecond((localStream.speed_up || 0) * 1024) : '—', <Upload className="h-3 w-3" />)}
              {tile('Peers', isActive ? (localStream.peers ?? 'N/A') : '—', <Users className="h-3 w-3" />)}
              {tile('Target', isActive && localStream.bitrate ? formatBitrate(localStream.bitrate) : '—', <Activity className="h-3 w-3" />)}
            </div>

            {isActive && !isExpanded && (
              <StreamTimelineGraphic
                streamId={streamSessionKey || streamIdentity || localStream.id}
                livepos={localStream.livepos}
                clients={clients}
                isLive={streamIsLive}
                showStreamWindow={showStreamWindow}
                eventMarkers={eventMarkers}
                compact
              />
            )}
          </div>
        </div>
      </CardHeader>

      {isExpanded && (
        <CardContent className="space-y-5 border-t bg-muted/10 pt-5 transition-all duration-300">
          {isActive && (
            <div className="space-y-2">
              <p className="text-sm font-semibold text-foreground">Stream timeline & client positions</p>
              <StreamTimelineGraphic
                streamId={streamSessionKey || streamIdentity || localStream.id}
                livepos={localStream.livepos}
                clients={clients}
                isLive={streamIsLive}
                showStreamWindow={showStreamWindow}
                eventMarkers={eventMarkers}
              />
            </div>
          )}

          <div className="space-y-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-sm font-semibold text-foreground">Active sessions ({clients.length})</p>
              {localStream?.command_url && (
                <a
                  href={localStream.command_url}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-xs text-primary hover:underline flex items-center gap-1"
                >
                  Engine command URL <ExternalLink className="h-3 w-3" />
                </a>
              )}
            </div>
            {clientsLoading ? (
              <p className="text-sm text-muted-foreground">Loading clients...</p>
            ) : clients.length > 0 ? (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                {clients.map((client, idx) => (
                  <ClientSession
                    key={client.client_id || `${client.ip_address || 'unknown'}-${client.connected_at || 'na'}-${client.user_agent || idx}`}
                    client={client}
                  />
                ))}
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No clients connected</p>
            )}
          </div>

          <div className="sticky bottom-0 z-10 rounded-xl border bg-background/95 p-3 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-background/75 space-y-2">
            <div className="flex flex-wrap gap-2">
              <Button
                variant="destructive"
                onClick={() => onStopStream(stream.id, stream.container_id)}
                className="flex items-center gap-2"
              >
                <StopCircle className="h-4 w-4" />
                Stop Stream
              </Button>
            </div>

            {detailsLoading && <p className="text-xs text-muted-foreground">Waiting for realtime stream snapshot...</p>}
            {controlLoading && <p className="text-xs text-muted-foreground">Sending control command...</p>}
            {controlMessage && <p className="text-xs text-emerald-600 dark:text-emerald-400">{controlMessage}</p>}
            {controlError && <p className="text-xs text-destructive">{controlError}</p>}
          </div>
        </CardContent>
      )}
    </Card>
  )
}

function StreamsTable({ streams, orchUrl, apiKey, onStopStream, onDeleteEngine }) {
  const canonicalStreams = useMemo(() => {
    const groups = new Map()
    ;(Array.isArray(streams) ? streams : []).forEach((stream) => {
      const identity = getCanonicalStreamIdentity(stream)
      if (!groups.has(identity)) {
        groups.set(identity, [])
      }
      groups.get(identity).push(stream)
    })

    return Array.from(groups.values())
      .map((entries) => mergeDuplicateStreams(entries))
      .filter(Boolean)
  }, [streams])

  const activeStreams = canonicalStreams.filter((s) => isStreamActiveStatus(s?.status))
  const endedStreams = canonicalStreams.filter((s) => s.status === 'ended')

  const [selectedStreams, setSelectedStreams] = useState(new Set())
  const [endedStreamsOpen, setEndedStreamsOpen] = useState(false)
  const [batchStopping, setBatchStopping] = useState(false)
  const [runtimeRecoveryMarkersByIdentity, setRuntimeRecoveryMarkersByIdentity] = useState({})
  const [sessionKeyByIdentity, setSessionKeyByIdentity] = useState({})
  const lastSeenByIdentityRef = useRef(new Map())
  const sessionLastSeenByIdentityRef = useRef(new Map())
  const markerLastSeenByIdentityRef = useRef(new Map())

  useEffect(() => {
    const now = Date.now()

    setSessionKeyByIdentity((prev) => {
      let changed = false
      const next = { ...prev }
      const seenIdentities = new Set()

      canonicalStreams.forEach((stream) => {
        const identity = String(stream?.__identity || getCanonicalStreamIdentity(stream))
        if (!identity) return
        seenIdentities.add(identity)

        const previousSeenAt = sessionLastSeenByIdentityRef.current.get(identity)
        sessionLastSeenByIdentityRef.current.set(identity, now)

        const previousSessionKey = next[identity]
        const isGapReset = Boolean(previousSeenAt && now - previousSeenAt > SESSION_GAP_RESET_MS)

        if (!previousSessionKey || isGapReset) {
          const startedAtMs = parseTimestampMs(stream?.started_at) || now
          const nextSessionKey = `${identity}:${startedAtMs}`
          if (previousSessionKey !== nextSessionKey) {
            next[identity] = nextSessionKey
            changed = true
          }
        }
      })

      Object.keys(next).forEach((identity) => {
        if (seenIdentities.has(identity)) {
          return
        }
        const lastSeenAt = sessionLastSeenByIdentityRef.current.get(identity)
        if (!lastSeenAt || now - lastSeenAt > SESSION_IDENTITY_RETENTION_MS) {
          delete next[identity]
          sessionLastSeenByIdentityRef.current.delete(identity)
          changed = true
        }
      })

      return changed ? next : prev
    })
  }, [canonicalStreams])

  useEffect(() => {
    const now = Date.now()

    setRuntimeRecoveryMarkersByIdentity((prev) => {
      let changed = false
      const next = { ...prev }

      canonicalStreams.forEach((stream) => {
        const identity = String(stream?.__identity || getCanonicalStreamIdentity(stream))
        const rawId = String(stream?.id || '').trim()
        const containerId = String(stream?.container_id || '').trim()
        const status = String(stream?.status || '').trim().toLowerCase()
        if (!identity || !rawId) return

        const previous = lastSeenByIdentityRef.current.get(identity)

        let markerReason = null
        if (previous?.status !== 'pending_failover' && status === 'pending_failover') {
          markerReason = 'Failover'
        } else if (previous?.containerId && containerId && previous.containerId !== containerId) {
          markerReason = 'Engine switch'
        } else if (previous?.rawId && previous.rawId !== rawId) {
          markerReason = 'Recovery'
        }

        if (markerReason) {
          const marker = buildRecoveryMarker({
            timestampMs: Date.now(),
            rawId,
            reason: markerReason,
          })
          next[identity] = [...(next[identity] || []), marker].slice(-20)
          changed = true
        }

        const seenAt = now
        lastSeenByIdentityRef.current.set(identity, { rawId, containerId, status, seenAt })
        markerLastSeenByIdentityRef.current.set(identity, seenAt)
      })

      Array.from(lastSeenByIdentityRef.current.keys()).forEach((identity) => {
        const seen = lastSeenByIdentityRef.current.get(identity)
        if (!seen?.seenAt || now - seen.seenAt > SESSION_IDENTITY_RETENTION_MS) {
          lastSeenByIdentityRef.current.delete(identity)
        }
      })

      Object.keys(next).forEach((identity) => {
        const lastSeenAt = markerLastSeenByIdentityRef.current.get(identity)
        if (!lastSeenAt || now - lastSeenAt > SESSION_IDENTITY_RETENTION_MS) {
          if (next[identity]) {
            delete next[identity]
            changed = true
          }
          markerLastSeenByIdentityRef.current.delete(identity)
        }
      })

      return changed ? next : prev
    })
  }, [canonicalStreams])

  const getSelectionKey = useCallback((stream) => String(stream?.__identity || getCanonicalStreamIdentity(stream)), [])

  const allSelected = activeStreams.length > 0 && selectedStreams.size === activeStreams.length

  const handleSelectAll = (checked) => {
    if (checked) {
      setSelectedStreams(new Set(activeStreams.map((s) => getSelectionKey(s))))
    } else {
      setSelectedStreams(new Set())
    }
  }

  const handleToggleSelect = (streamId) => {
    setSelectedStreams((prev) => {
      const next = new Set(prev)
      if (next.has(streamId)) next.delete(streamId)
      else next.add(streamId)
      return next
    })
  }

  const handleBatchStop = async () => {
    if (selectedStreams.size === 0) return
    setBatchStopping(true)
    try {
      const commandUrls = activeStreams
        .filter((s) => selectedStreams.has(getSelectionKey(s)))
        .map((s) => s.command_url)
        .filter(Boolean)
      if (commandUrls.length === 0) return

      const headers = { 'Content-Type': 'application/json' }
      if (apiKey) headers.Authorization = `Bearer ${apiKey}`
      const response = await fetch(`${orchUrl}/api/v1/streams/batch-stop`, {
        method: 'POST',
        headers,
        body: JSON.stringify(commandUrls),
      })
      if (response.ok) setSelectedStreams(new Set())
    } catch (error) {
      console.error('Error during batch stop:', error)
    } finally {
      setBatchStopping(false)
    }
  }

  const sortedActiveStreams = [...activeStreams].sort((a, b) => new Date(b.started_at) - new Date(a.started_at))
  const sortedEndedStreams = [...endedStreams].sort((a, b) => new Date(b.started_at) - new Date(a.started_at))

  return (
    <div className="space-y-6">
      <div className="space-y-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="text-2xl font-semibold">Active Streams ({activeStreams.length})</h2>
          <div className="flex items-center gap-3">
            {activeStreams.length > 0 && (
              <div className="flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
                <Checkbox
                  checked={allSelected}
                  onCheckedChange={handleSelectAll}
                  aria-label="Select all active streams"
                />
                <span className="text-muted-foreground">Select all</span>
              </div>
            )}
            {selectedStreams.size > 0 && (
              <Button variant="destructive" onClick={handleBatchStop} disabled={batchStopping}>
                {batchStopping ? 'Stopping...' : `Stop Selected (${selectedStreams.size})`}
              </Button>
            )}
          </div>
        </div>

        {activeStreams.length === 0 ? (
          <div className="rounded-xl border bg-muted/10 p-8 text-center text-muted-foreground">No active streams</div>
        ) : (
          <div className="space-y-3">
            {sortedActiveStreams.map((stream) => (
              (() => {
                const identity = stream.__identity || getCanonicalStreamIdentity(stream)
                const eventMarkers = [
                  ...(stream.__recoveryMarkers || []),
                  ...(runtimeRecoveryMarkersByIdentity[identity] || []),
                ]
                const selectionKey = getSelectionKey(stream)
                const streamSessionKey = sessionKeyByIdentity[identity] || `${identity}:${parseTimestampMs(stream?.started_at) || 0}`
                return (
              <StreamCard
                key={identity}
                stream={stream}
                streamIdentity={identity}
                streamSessionKey={streamSessionKey}
                eventMarkers={eventMarkers}
                orchUrl={orchUrl}
                apiKey={apiKey}
                onStopStream={onStopStream}
                onDeleteEngine={onDeleteEngine}
                isSelected={selectedStreams.has(selectionKey)}
                onToggleSelect={() => handleToggleSelect(selectionKey)}
                selectable
              />
                )
              })()
            ))}
          </div>
        )}
      </div>

      {endedStreams.length > 0 && (
        <Collapsible open={endedStreamsOpen} onOpenChange={setEndedStreamsOpen}>
          <CollapsibleTrigger asChild>
            <Button variant="ghost" className="flex items-center gap-2 p-0 hover:bg-transparent">
              <h2 className="text-2xl font-semibold">Ended Streams ({endedStreams.length})</h2>
              {endedStreamsOpen ? <ChevronUp className="h-5 w-5" /> : <ChevronDown className="h-5 w-5" />}
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="mt-4 space-y-3">
            {sortedEndedStreams.map((stream) => (
              (() => {
                const identity = stream.__identity || getCanonicalStreamIdentity(stream)
                const eventMarkers = [
                  ...(stream.__recoveryMarkers || []),
                  ...(runtimeRecoveryMarkersByIdentity[identity] || []),
                ]
                const streamSessionKey = sessionKeyByIdentity[identity] || `${identity}:${parseTimestampMs(stream?.started_at) || 0}`
                return (
              <StreamCard
                key={identity}
                stream={stream}
                streamIdentity={identity}
                streamSessionKey={streamSessionKey}
                eventMarkers={eventMarkers}
                orchUrl={orchUrl}
                apiKey={apiKey}
                onStopStream={onStopStream}
                onDeleteEngine={onDeleteEngine}
              />
                )
              })()
            ))}
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  )
}

export default StreamsTable
