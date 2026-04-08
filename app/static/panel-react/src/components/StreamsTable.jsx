import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader } from '@/components/ui/card'
import { Checkbox } from '@/components/ui/checkbox'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import {
  Activity,
  ChevronDown,
  ChevronUp,
  Clock,
  Download,
  Pause,
  PlayCircle,
  Server,
  StopCircle,
  Trash2,
  Upload,
  Users,
} from 'lucide-react'
import { formatTime, formatBytesPerSecond } from '../utils/formatters'
import StreamTimelineGraphic from './StreamTimelineGraphic'

const TRUNCATED_CONTAINER_ID_LENGTH = 12
const TRUNCATED_CLIENT_ID_LENGTH = 16

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
            <Badge variant="outline" className="text-[10px]">Lag {formatLagValue(client.buffer_seconds_behind)}</Badge>
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

function StreamCard({ stream, orchUrl, apiKey, onStopStream, onDeleteEngine, isSelected, onToggleSelect, selectable }) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [extendedStats, setExtendedStats] = useState(null)
  const [clients, setClients] = useState([])
  const [clientsLoading, setClientsLoading] = useState(false)
  const [isPaused, setIsPaused] = useState(Boolean(stream.paused))
  const hasLoadedClientsRef = useRef(false)

  const isActive = stream.status === 'started'
  const streamIsLive = Boolean(stream.livepos?.live_last)
  const labels = stream.labels || {}
  const isPrebuffering = String(labels['stream.status_text'] || '').toLowerCase().includes('prebuf')
  const deadReason = String(stream.dead_reason || stream.last_error || labels['stream.dead_reason'] || '').toLowerCase()
  const isDownloadStopped = deadReason.includes('download_stopped') || deadReason.includes('download stopped')

  useEffect(() => {
    setIsPaused(Boolean(stream.paused))
  }, [stream.paused])

  const fetchExtendedStats = useCallback(async () => {
    if (!stream?.id || (!isExpanded && !isActive)) return
    try {
      const headers = {}
      if (apiKey) headers.Authorization = `Bearer ${apiKey}`
      const response = await fetch(`${orchUrl}/api/v1/streams/${encodeURIComponent(stream.id)}/extended-stats`, { headers })
      if (response.ok) {
        setExtendedStats(await response.json())
      }
    } catch (err) {
      console.error('Failed to fetch extended stats:', err)
    }
  }, [stream?.id, orchUrl, apiKey, isExpanded, isActive])

  const fetchClients = useCallback(async () => {
    if (!stream?.key || !isExpanded) return
    if (!hasLoadedClientsRef.current) setClientsLoading(true)
    try {
      const response = await fetch(`${orchUrl}/api/v1/proxy/streams/${encodeURIComponent(stream.key)}/clients`)
      if (response.ok) {
        const data = await response.json()
        setClients(data.clients || [])
        hasLoadedClientsRef.current = true
      }
    } catch (err) {
      console.error('Failed to fetch clients:', err)
    } finally {
      setClientsLoading(false)
    }
  }, [stream?.key, orchUrl, isExpanded])

  useEffect(() => {
    fetchExtendedStats()
    fetchClients()
  }, [isExpanded, fetchExtendedStats, fetchClients])

  const title = extendedStats?.title || stream.id

  return (
    <Card className="overflow-hidden border-border/80">
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
                <p className="text-xs text-muted-foreground font-mono truncate" title={stream.id}>{stream.id}</p>
              </div>
              <div className="flex items-center gap-2">
                <StreamStatusBadge
                  isActive={isActive}
                  isPaused={isPaused}
                  isPrebuffering={isPrebuffering}
                  isDownloadStopped={isDownloadStopped}
                />
                <Button variant="ghost" size="sm" className="h-8 w-8 p-0" onClick={() => setIsExpanded((v) => !v)}>
                  {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                </Button>
              </div>
            </div>

            <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
              {tile('Engine', stream.container_name || stream.container_id?.slice(0, TRUNCATED_CONTAINER_ID_LENGTH) || 'N/A', <Server className="h-3 w-3" />)}
              {tile('Started', formatTime(stream.started_at), <Clock className="h-3 w-3" />)}
              {tile('Download', isActive ? formatBytesPerSecond((stream.speed_down || 0) * 1024) : '—', <Download className="h-3 w-3" />)}
              {tile('Upload', isActive ? formatBytesPerSecond((stream.speed_up || 0) * 1024) : '—', <Upload className="h-3 w-3" />)}
              {tile('Peers', isActive ? (stream.peers ?? 'N/A') : '—', <Users className="h-3 w-3" />)}
            </div>

            {isActive && (
              <StreamTimelineGraphic livepos={stream.livepos} clients={clients} isLive={streamIsLive} compact />
            )}
          </div>
        </div>
      </CardHeader>

      {isExpanded && (
        <CardContent className="space-y-5 border-t bg-muted/10 pt-5">
          {isActive && (
            <div className="space-y-2">
              <p className="text-sm font-semibold text-foreground">Stream timeline & client positions</p>
              <StreamTimelineGraphic livepos={stream.livepos} clients={clients} isLive={streamIsLive} />
            </div>
          )}

          <div className="space-y-3">
            <p className="text-sm font-semibold text-foreground">Active sessions ({clients.length})</p>
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

          <div className="sticky bottom-0 z-10 rounded-xl border bg-background/95 p-3 shadow-sm backdrop-blur supports-[backdrop-filter]:bg-background/75">
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
              {isActive && (
                <Badge variant="outline" className="flex items-center gap-1">
                  {isPaused ? <Pause className="h-3 w-3" /> : <Activity className="h-3 w-3" />}
                  {isPaused ? 'Paused' : 'Running'}
                </Badge>
              )}
            </div>
          </div>
        </CardContent>
      )}
    </Card>
  )
}

function StreamsTable({ streams, orchUrl, apiKey, onStopStream, onDeleteEngine }) {
  const activeStreams = streams.filter((s) => s.status === 'started')
  const endedStreams = streams.filter((s) => s.status === 'ended')

  const [selectedStreams, setSelectedStreams] = useState(new Set())
  const [endedStreamsOpen, setEndedStreamsOpen] = useState(false)
  const [batchStopping, setBatchStopping] = useState(false)

  const allSelected = activeStreams.length > 0 && selectedStreams.size === activeStreams.length

  const handleSelectAll = (checked) => {
    if (checked) {
      setSelectedStreams(new Set(activeStreams.map((s) => s.id)))
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
        .filter((s) => selectedStreams.has(s.id))
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
              <StreamCard
                key={stream.id}
                stream={stream}
                orchUrl={orchUrl}
                apiKey={apiKey}
                onStopStream={onStopStream}
                onDeleteEngine={onDeleteEngine}
                isSelected={selectedStreams.has(stream.id)}
                onToggleSelect={() => handleToggleSelect(stream.id)}
                selectable
              />
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
              <StreamCard
                key={stream.id}
                stream={stream}
                orchUrl={orchUrl}
                apiKey={apiKey}
                onStopStream={onStopStream}
                onDeleteEngine={onDeleteEngine}
              />
            ))}
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  )
}

export default StreamsTable
