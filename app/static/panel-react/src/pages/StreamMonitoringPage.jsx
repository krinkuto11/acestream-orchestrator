import React, { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Radio, PlayCircle, StopCircle, MoveRight, Trash2 } from 'lucide-react'
import { formatBytesPerSecond, formatBytes } from '@/utils/formatters'

function formatAge(ts) {
  if (!ts) return 'n/a'
  const parsed = Date.parse(ts)
  if (Number.isNaN(parsed)) return 'n/a'
  const delta = Math.max(0, Math.floor((Date.now() - parsed) / 1000))
  if (delta < 60) return `${delta}s ago`
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`
  return `${Math.floor(delta / 3600)}h ago`
}

function movementVariant(movement) {
  if (!movement) return 'secondary'
  if (movement.is_moving) return 'success'
  if (movement.direction === 'stable') return 'warning'
  return 'secondary'
}

function movementLabel(movement) {
  if (!movement) return 'unknown'
  if (movement.is_moving) return `moving (${movement.direction})`
  if (movement.direction === 'stable') return 'stable'
  return 'unknown'
}

function statusVariant(status) {
  if (status === 'running') return 'success'
  if (status === 'dead') return 'destructive'
  if (status === 'reconnecting') return 'warning'
  return 'secondary'
}

function toInt(value) {
  if (value == null) return null
  const n = Number(value)
  if (Number.isNaN(n)) return null
  return n
}

function buildSeries(samples, keyPath) {
  const values = []
  for (const sample of samples || []) {
    let current = sample
    for (const key of keyPath) {
      if (current == null) break
      current = current[key]
    }
    const parsed = toInt(current)
    values.push(parsed)
  }
  return values
}

function Sparkline({ values, color = '#0ea5e9', label = 'series' }) {
  const clean = values.filter((v) => v != null)
  if (clean.length < 2) {
    return <p className="text-xs text-muted-foreground">No {label} trend yet</p>
  }

  const width = 240
  const height = 56
  const min = Math.min(...clean)
  const max = Math.max(...clean)
  const span = max - min || 1

  const points = values
    .map((v, idx) => {
      if (v == null) return null
      const x = (idx / Math.max(1, values.length - 1)) * width
      const y = height - ((v - min) / span) * height
      return `${x},${y}`
    })
    .filter(Boolean)
    .join(' ')

  return (
    <svg viewBox={`0 0 ${width} ${height}`} className="h-14 w-full" preserveAspectRatio="none" aria-label={label}>
      <polyline fill="none" stroke={color} strokeWidth="2" points={points} />
    </svg>
  )
}

function BufferWindowBar({ livepos }) {
  const first = toInt(livepos?.live_first ?? livepos?.first_ts)
  const last = toInt(livepos?.live_last ?? livepos?.last_ts)
  const pos = toInt(livepos?.pos)

  if (first == null || last == null || pos == null || last <= first) {
    return <p className="text-xs text-muted-foreground">Buffer window unavailable</p>
  }

  const ratio = Math.max(0, Math.min(1, (pos - first) / (last - first)))
  const markerLeft = `${ratio * 100}%`

  return (
    <div className="space-y-1">
      <div className="relative h-2 w-full rounded-full bg-slate-200 dark:bg-slate-700">
        <div className="absolute inset-y-0 left-0 rounded-full bg-sky-400/60" style={{ width: '100%' }} />
        <div className="absolute -top-1 h-4 w-1 rounded bg-sky-700 dark:bg-sky-300" style={{ left: markerLeft }} />
      </div>
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>first_ts: {first}</span>
        <span>pos: {pos}</span>
        <span>last_ts: {last}</span>
      </div>
    </div>
  )
}

export function StreamMonitoringPage({ orchUrl, apiKey }) {
  const [monitors, setMonitors] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [starting, setStarting] = useState(false)
  const [stoppingById, setStoppingById] = useState({})
  const [deletingById, setDeletingById] = useState({})
  const [newMonitor, setNewMonitor] = useState({
    content_id: '',
    interval_s: '1.0',
    run_seconds: '0',
  })

  const fetchMonitorsNow = async (showLoading = false) => {
    if (showLoading) {
      setLoading(true)
    }

    if (!apiKey) {
      setMonitors([])
      setError('Set API key in Settings to view stream monitoring sessions')
      setLoading(false)
      return
    }

    try {
      const response = await fetch(`${orchUrl}/ace/monitor/legacy`, {
        headers: {
          Authorization: `Bearer ${apiKey}`,
        },
      })
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`)
      }
      const payload = await response.json()
      setMonitors(Array.isArray(payload?.items) ? payload.items : [])
      setError(null)
    } catch (err) {
      setError(err?.message || 'Failed to fetch stream monitoring sessions')
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    let cancelled = false

    const fetchLoop = async (showLoading = false) => {
      if (cancelled) return
      await fetchMonitorsNow(showLoading)
    }

    fetchLoop(true)
    const interval = setInterval(() => fetchLoop(false), 1000)

    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [orchUrl, apiKey])

  const handleStartMonitor = async () => {
    if (!apiKey) {
      setActionError('Set API key in Settings to start monitor sessions')
      return
    }

    const contentId = (newMonitor.content_id || '').trim()
    if (!contentId) {
      setActionError('content_id is required')
      return
    }

    setStarting(true)
    setActionError(null)

    try {
      const response = await fetch(`${orchUrl}/ace/monitor/legacy/start`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          content_id: contentId,
          interval_s: Number(newMonitor.interval_s || 1),
          run_seconds: Number(newMonitor.run_seconds || 0),
        }),
      })

      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`
        try {
          const payload = await response.json()
          detail = payload?.detail || detail
        } catch {
          // Keep fallback detail.
        }
        throw new Error(detail)
      }

      await response.json()
      setNewMonitor((prev) => ({ ...prev, content_id: '' }))
      await fetchMonitorsNow(false)
    } catch (err) {
      setActionError(err?.message || 'Failed to start monitor session')
    } finally {
      setStarting(false)
    }
  }

  const handleStopMonitor = async (monitorId) => {
    if (!apiKey) {
      setActionError('Set API key in Settings to stop monitor sessions')
      return
    }

    setStoppingById((prev) => ({ ...prev, [monitorId]: true }))
    setActionError(null)

    try {
      const response = await fetch(`${orchUrl}/ace/monitor/legacy/${encodeURIComponent(monitorId)}`, {
        method: 'DELETE',
        headers: {
          Authorization: `Bearer ${apiKey}`,
        },
      })

      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`
        try {
          const payload = await response.json()
          detail = payload?.detail || detail
        } catch {
          // Keep fallback detail.
        }
        throw new Error(detail)
      }

      await fetchMonitorsNow(false)
    } catch (err) {
      setActionError(err?.message || 'Failed to stop monitor session')
    } finally {
      setStoppingById((prev) => ({ ...prev, [monitorId]: false }))
    }
  }

  const handleDeleteEntry = async (monitorId) => {
    if (!apiKey) {
      setActionError('Set API key in Settings to delete monitor entries')
      return
    }

    setDeletingById((prev) => ({ ...prev, [monitorId]: true }))
    setActionError(null)

    try {
      const response = await fetch(`${orchUrl}/ace/monitor/legacy/${encodeURIComponent(monitorId)}/entry`, {
        method: 'DELETE',
        headers: {
          Authorization: `Bearer ${apiKey}`,
        },
      })

      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`
        try {
          const payload = await response.json()
          detail = payload?.detail || detail
        } catch {
          // Keep fallback detail.
        }
        throw new Error(detail)
      }

      await fetchMonitorsNow(false)
    } catch (err) {
      setActionError(err?.message || 'Failed to delete monitor entry')
    } finally {
      setDeletingById((prev) => ({ ...prev, [monitorId]: false }))
    }
  }

  const activeCount = monitors.filter((m) => ['starting', 'running'].includes(m.status)).length

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Stream Monitoring</h1>
          <p className="text-muted-foreground mt-1">Broadcast-like status sessions with livepos movement telemetry and 1s updates</p>
        </div>
        <Badge variant={activeCount > 0 ? 'success' : 'secondary'}>
          {activeCount} active
        </Badge>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Radio className="h-4 w-4" />
            Start Monitoring Session
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-2 md:grid-cols-5">
            <Input
              className="md:col-span-3"
              placeholder="content_id / infohash"
              value={newMonitor.content_id}
              onChange={(e) => setNewMonitor((prev) => ({ ...prev, content_id: e.target.value }))}
            />
            <Input
              type="number"
              min="0.5"
              step="0.5"
              placeholder="interval_s"
              value={newMonitor.interval_s}
              onChange={(e) => setNewMonitor((prev) => ({ ...prev, interval_s: e.target.value }))}
            />
            <Input
              type="number"
              min="0"
              step="1"
              placeholder="run_seconds"
              value={newMonitor.run_seconds}
              onChange={(e) => setNewMonitor((prev) => ({ ...prev, run_seconds: e.target.value }))}
            />
          </div>
          <div className="mt-3 flex items-center gap-2">
            <Button onClick={handleStartMonitor} disabled={starting || !apiKey} size="sm">
              <PlayCircle className="mr-1 h-4 w-4" />
              {starting ? 'Starting...' : 'Start Monitor'}
            </Button>
            <span className="text-xs text-muted-foreground">interval default 1s, run_seconds 0 means continuous</span>
          </div>
          {actionError && (
            <p className="mt-2 text-xs text-red-600 dark:text-red-400">{actionError}</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Active Sessions</CardTitle>
        </CardHeader>
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground">Loading stream monitoring sessions...</p>
          ) : error ? (
            <p className="text-sm text-muted-foreground">{error}</p>
          ) : monitors.length === 0 ? (
            <p className="text-sm text-muted-foreground">No stream monitoring sessions</p>
          ) : (
            <div className="space-y-3">
              {monitors.map((monitor) => {
                const latest = monitor.latest_status || {}
                const movement = monitor.livepos_movement || {}
                const statusText = latest.status_text || latest.status || 'unknown'
                const peers = latest.peers ?? latest.http_peers ?? 0
                const speedDown = latest.speed_down ?? latest.http_speed_down ?? 0
                const speedUp = latest.speed_up ?? 0
                const progress = latest.progress ?? latest.immediate_progress ?? latest.total_progress ?? 0
                const livepos = latest.livepos || {}
                const posSeries = buildSeries(monitor.recent_status || [], ['livepos', 'pos'])
                const lastTsSeries = buildSeries(monitor.recent_status || [], ['livepos', 'last_ts'])
                const deadReason = monitor.dead_reason || monitor.last_error

                return (
                  <div key={monitor.monitor_id} className="rounded-md border p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0 flex-1">
                        <p className="text-sm font-semibold truncate">{monitor.content_id}</p>
                        <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted-foreground">
                          <span>status: {statusText}</span>
                          <span>peers: {peers}</span>
                          <span>down: {formatBytesPerSecond((speedDown || 0) * 1024)}</span>
                          <span>up: {formatBytesPerSecond((speedUp || 0) * 1024)}</span>
                          <span>progress: {progress}%</span>
                        </div>
                      </div>
                      <div className="flex flex-col items-end gap-1">
                        <Badge variant={statusVariant(monitor.status)}>
                          {monitor.status}
                        </Badge>
                        <span className="text-xs text-muted-foreground">{formatAge(monitor.last_collected_at)}</span>
                        <div className="flex items-center gap-1">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => handleStopMonitor(monitor.monitor_id)}
                            disabled={Boolean(stoppingById[monitor.monitor_id]) || !apiKey || monitor.status === 'dead'}
                          >
                            <StopCircle className="mr-1 h-3 w-3" />
                            {stoppingById[monitor.monitor_id] ? 'Stopping...' : 'Stop'}
                          </Button>
                          <Button
                            variant="destructive"
                            size="sm"
                            onClick={() => handleDeleteEntry(monitor.monitor_id)}
                            disabled={Boolean(deletingById[monitor.monitor_id]) || !apiKey}
                          >
                            <Trash2 className="mr-1 h-3 w-3" />
                            {deletingById[monitor.monitor_id] ? 'Deleting...' : 'Delete'}
                          </Button>
                        </div>
                      </div>
                    </div>

                    {monitor.status === 'dead' && (
                      <p className="mt-2 text-xs text-red-600 dark:text-red-400">dead reason: {deadReason || 'unknown'}</p>
                    )}

                    <div className="mt-3 grid gap-2 md:grid-cols-2 lg:grid-cols-4 text-xs">
                      <div className="rounded-md bg-muted/40 p-2">
                        <p className="text-muted-foreground">Livepos Movement</p>
                        <div className="mt-1 flex items-center gap-1">
                          <Badge variant={movementVariant(movement)}>{movementLabel(movement)}</Badge>
                        </div>
                      </div>

                      <div className="rounded-md bg-muted/40 p-2">
                        <p className="text-muted-foreground">Position Delta</p>
                        <p className="mt-1 font-medium flex items-center gap-1">
                          <MoveRight className="h-3 w-3" />
                          {movement.pos_delta ?? 'n/a'}
                        </p>
                      </div>

                      <div className="rounded-md bg-muted/40 p-2">
                        <p className="text-muted-foreground">Live Timestamp Delta</p>
                        <p className="mt-1 font-medium">{movement.last_ts_delta ?? 'n/a'}</p>
                      </div>

                      <div className="rounded-md bg-muted/40 p-2">
                        <p className="text-muted-foreground">Downloaded Delta</p>
                        <p className="mt-1 font-medium">
                          {movement.downloaded_delta != null ? formatBytes(movement.downloaded_delta) : 'n/a'}
                        </p>
                      </div>
                    </div>

                    <div className="mt-3 grid gap-3 md:grid-cols-2">
                      <div className="rounded-md bg-muted/30 p-2">
                        <p className="mb-1 text-xs text-muted-foreground">POS movement (sliding trend)</p>
                        <Sparkline values={posSeries} color="#22c55e" label="pos trend" />
                      </div>
                      <div className="rounded-md bg-muted/30 p-2">
                        <p className="mb-1 text-xs text-muted-foreground">last_ts movement (sliding trend)</p>
                        <Sparkline values={lastTsSeries} color="#0ea5e9" label="last_ts trend" />
                      </div>
                    </div>

                    <div className="mt-3 rounded-md bg-muted/30 p-2">
                      <p className="mb-1 text-xs text-muted-foreground">Live buffer window (first_ts -&gt; pos -&gt; last_ts)</p>
                      <BufferWindowBar livepos={livepos} />
                    </div>

                    <div className="mt-2 grid gap-2 md:grid-cols-3 text-xs text-muted-foreground">
                      <span>current pos: {movement.current_pos ?? 'n/a'}</span>
                      <span>current last_ts: {movement.current_last_ts ?? 'n/a'}</span>
                      <span>movement events: {movement.movement_events ?? 0} / samples: {movement.sample_points ?? 0}</span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
