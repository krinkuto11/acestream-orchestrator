import React, { useEffect, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Progress } from '@/components/ui/progress'
import { Checkbox } from '@/components/ui/checkbox'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Radio, PlayCircle, StopCircle, MoveRight, Trash2, ChevronDown, ChevronUp, Activity, Gauge, Users, Play } from 'lucide-react'
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

function movementVariant(movement, status) {
  if (status === 'stuck') return 'warning'
  if (status === 'dead') return 'secondary'
  if (!movement) return 'secondary'
  if (movement.is_moving) return 'success'
  if (movement.direction === 'stable') return 'warning'
  return 'secondary'
}

function movementLabel(movement, status) {
  if (status === 'stuck') return 'stuck'
  if (status === 'dead') return 'unknown'
  if (!movement) return 'unknown'
  if (movement.is_moving) return `moving (${movement.direction})`
  if (movement.direction === 'stable') return 'stuck'
  return 'unknown'
}

function statusVariant(status) {
  if (status === 'running') return 'success'
  if (status === 'stuck') return 'warning'
  if (status === 'dead') return 'destructive'
  if (status === 'reconnecting') return 'warning'
  return 'secondary'
}

function statusAccent(status) {
  if (status === 'running') return 'border-emerald-200 bg-emerald-50 dark:border-emerald-900 dark:bg-emerald-950/20'
  if (status === 'stuck') return 'border-amber-200 bg-amber-50 dark:border-amber-900 dark:bg-amber-950/20'
  if (status === 'dead') return 'border-rose-200 bg-rose-50 dark:border-rose-900 dark:bg-rose-950/20'
  return 'border-slate-200 bg-slate-50 dark:border-slate-800 dark:bg-slate-900/40'
}

function toInt(value) {
  if (value == null) return null
  const n = Number(value)
  if (Number.isNaN(n)) return null
  return n
}

function normalizeContentRef(value) {
  if (!value) return ''
  const trimmed = String(value).trim().toLowerCase()
  if (trimmed.startsWith('acestream://')) {
    return trimmed.slice('acestream://'.length)
  }
  return trimmed
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

  const totalSeconds = Math.max(0, last - first)
  const posSeconds = Math.max(0, pos - first)
  const leadSeconds = Math.max(0, last - pos)

  // Adapt viewport size from the lead gap so last-pos remains readable.
  // If gap is zero, keep a small tail window for context.
  const desiredGapRatio = 0.25
  const minWindowFromGap = 8
  const maxWindowFromGap = 240
  const windowFromGap = leadSeconds > 0
    ? Math.ceil(leadSeconds / desiredGapRatio)
    : minWindowFromGap
  const adaptiveWindowSeconds = Math.min(
    totalSeconds,
    Math.max(minWindowFromGap, Math.min(maxWindowFromGap, windowFromGap)),
  )

  const viewportStart = Math.max(first, last - adaptiveWindowSeconds)
  const viewportEnd = last
  const viewportSpan = Math.max(1, viewportEnd - viewportStart)
  const viewportPosRatio = Math.max(0, Math.min(1, (pos - viewportStart) / viewportSpan))
  const viewportLastRatio = 1
  const viewportStartOffset = viewportStart - first

  return (
    <div className="space-y-1">
      <div className="relative h-2 w-full rounded-full bg-slate-200 dark:bg-slate-700">
        <div className="absolute inset-y-0 left-0 rounded-full bg-sky-300/60" style={{ width: '100%' }} />
        <div
          className="absolute -top-1 h-4 w-1 -translate-x-1/2 rounded bg-emerald-700 dark:bg-emerald-300"
          style={{ left: `${viewportPosRatio * 100}%` }}
          title="pos"
        />
        <div
          className="absolute -top-1 h-4 w-1 -translate-x-1/2 rounded bg-sky-700 dark:bg-sky-300"
          style={{ left: `${viewportLastRatio * 100}%` }}
          title="last_ts"
        />
      </div>
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{viewportStartOffset}s</span>
        <span>pos: {posSeconds}s</span>
        <span>{totalSeconds}s</span>
      </div>
      <div className="mt-1 text-[11px] text-muted-foreground">
        adaptive window: {adaptiveWindowSeconds}s, gap (last-pos): {leadSeconds}s
      </div>

      <div className="text-[11px] text-muted-foreground">
        abs first_ts={first}, pos={pos}, last_ts={last}
      </div>
    </div>
  )
}

export function StreamMonitoringPage({ orchUrl, apiKey, streams = [] }) {
  const uiPrefsStorageKey = 'stream-monitoring-ui-prefs-v1'
  const [monitors, setMonitors] = useState([])
  const [activeProxyKeys, setActiveProxyKeys] = useState(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [starting, setStarting] = useState(false)
  const [stoppingById, setStoppingById] = useState({})
  const [deletingById, setDeletingById] = useState({})
  const [expandedById, setExpandedById] = useState({})
  const [selectedById, setSelectedById] = useState({})
  const [viewMode, setViewMode] = useState('cards')
  const [sortMode, setSortMode] = useState('status_then_recent')
  const [groupByStatus, setGroupByStatus] = useState(true)
  const [statusFilter, setStatusFilter] = useState('all')
  const [batchBusy, setBatchBusy] = useState(false)
  const [m3uFileName, setM3uFileName] = useState('')
  const [m3uContent, setM3uContent] = useState('')
  const [m3uEntries, setM3uEntries] = useState([])
  const [m3uSelectedById, setM3uSelectedById] = useState({})
  const [m3uParsing, setM3uParsing] = useState(false)
  const [m3uStarting, setM3uStarting] = useState(false)
  const [newMonitor, setNewMonitor] = useState({
    content_id: '',
    live_delay: '',
    interval_s: '1.0',
    run_seconds: '0',
  })

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(uiPrefsStorageKey)
      if (!raw) return
      const parsed = JSON.parse(raw)
      if (typeof parsed.viewMode === 'string') setViewMode(parsed.viewMode)
      if (typeof parsed.sortMode === 'string') setSortMode(parsed.sortMode)
      if (typeof parsed.groupByStatus === 'boolean') setGroupByStatus(parsed.groupByStatus)
      if (typeof parsed.statusFilter === 'string') setStatusFilter(parsed.statusFilter)
    } catch {
      // Ignore malformed persisted preferences.
    }
  }, [])

  useEffect(() => {
    const prefs = {
      viewMode,
      sortMode,
      groupByStatus,
      statusFilter,
    }
    try {
      window.localStorage.setItem(uiPrefsStorageKey, JSON.stringify(prefs))
    } catch {
      // Ignore storage write failures.
    }
  }, [viewMode, sortMode, groupByStatus, statusFilter])

  useEffect(() => {
    const keys = new Set(
      (Array.isArray(streams) ? streams : [])
        .map((stream) => normalizeContentRef(stream?.key))
        .filter(Boolean),
    )
    setActiveProxyKeys(keys)
  }, [streams])

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
      const monitorResponse = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy`, {
        headers: {
          Authorization: `Bearer ${apiKey}`,
        },
      })

      if (!monitorResponse.ok) {
        throw new Error(`${monitorResponse.status} ${monitorResponse.statusText}`)
      }

      const payload = await monitorResponse.json()
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
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/start`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          content_id: contentId,
          stream_name: null,
          live_delay: Number(newMonitor.live_delay || 0),
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
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/${encodeURIComponent(monitorId)}`, {
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
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/${encodeURIComponent(monitorId)}/entry`, {
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

  const statusRank = {
    running: 0,
    starting: 1,
    stuck: 2,
    reconnecting: 3,
    stopped: 4,
    dead: 5,
  }

  const parsedMonitors = monitors.map((monitor) => {
    const latest = monitor.latest_status || {}
    const progressValue = Number(latest.progress ?? latest.immediate_progress ?? latest.total_progress ?? 0) || 0
    const speedDownKbps = Number(latest.speed_down ?? latest.http_speed_down ?? 0) || 0
    const speedUpKbps = Number(latest.speed_up ?? 0) || 0
    const peers = Number(latest.peers ?? latest.http_peers ?? 0) || 0
    const lastCollectedAtMs = monitor.last_collected_at ? Date.parse(monitor.last_collected_at) : 0
    return {
      ...monitor,
      _derived: {
        latest,
        progressValue,
        speedDownKbps,
        speedUpKbps,
        peers,
        lastCollectedAtMs: Number.isFinite(lastCollectedAtMs) ? lastCollectedAtMs : 0,
      },
    }
  })

  const filterCounts = {
    all: parsedMonitors.length,
    active: parsedMonitors.filter((m) => ['running', 'starting', 'stuck', 'reconnecting'].includes(m.status)).length,
    running: parsedMonitors.filter((m) => m.status === 'running').length,
    stuck: parsedMonitors.filter((m) => m.status === 'stuck').length,
    dead: parsedMonitors.filter((m) => m.status === 'dead').length,
  }

  const filteredMonitors = parsedMonitors.filter((monitor) => {
    if (statusFilter === 'active') {
      return ['running', 'starting', 'stuck', 'reconnecting'].includes(monitor.status)
    }
    if (statusFilter === 'running') {
      return monitor.status === 'running'
    }
    if (statusFilter === 'stuck') {
      return monitor.status === 'stuck'
    }
    if (statusFilter === 'dead') {
      return monitor.status === 'dead'
    }
    return true
  })

  const sortedMonitors = [...filteredMonitors].sort((a, b) => {
    const aStatusRank = statusRank[a.status] ?? 99
    const bStatusRank = statusRank[b.status] ?? 99

    if (sortMode === 'recent') {
      return b._derived.lastCollectedAtMs - a._derived.lastCollectedAtMs
    }
    if (sortMode === 'speed') {
      return b._derived.speedDownKbps - a._derived.speedDownKbps
    }
    if (sortMode === 'progress') {
      return b._derived.progressValue - a._derived.progressValue
    }
    if (sortMode === 'content') {
      return (a.content_id || '').localeCompare(b.content_id || '')
    }

    if (aStatusRank !== bStatusRank) return aStatusRank - bStatusRank
    return b._derived.lastCollectedAtMs - a._derived.lastCollectedAtMs
  })

  const groupedMonitors = groupByStatus
    ? [
      { key: 'running', label: 'Running', items: sortedMonitors.filter((m) => m.status === 'running') },
      { key: 'starting', label: 'Starting', items: sortedMonitors.filter((m) => m.status === 'starting') },
      { key: 'stuck', label: 'Stuck', items: sortedMonitors.filter((m) => m.status === 'stuck') },
      { key: 'reconnecting', label: 'Reconnecting', items: sortedMonitors.filter((m) => m.status === 'reconnecting') },
      { key: 'stopped', label: 'Stopped', items: sortedMonitors.filter((m) => m.status === 'stopped') },
      { key: 'dead', label: 'Dead', items: sortedMonitors.filter((m) => m.status === 'dead') },
      { key: 'other', label: 'Other', items: sortedMonitors.filter((m) => !statusRank.hasOwnProperty(m.status)) },
    ].filter((group) => group.items.length > 0)
    : [{ key: 'all', label: 'All Sessions', items: sortedMonitors }]

  const visibleMonitorIds = sortedMonitors.map((m) => m.monitor_id)
  const selectedVisibleIds = visibleMonitorIds.filter((id) => Boolean(selectedById[id]))
  const selectedMonitors = sortedMonitors.filter((m) => Boolean(selectedById[m.monitor_id]))
  const allVisibleSelected = visibleMonitorIds.length > 0 && selectedVisibleIds.length === visibleMonitorIds.length

  const activeCount = monitors.filter((m) => ['starting', 'running', 'stuck'].includes(m.status)).length
  const runningCount = monitors.filter((m) => m.status === 'running').length
  const deadCount = monitors.filter((m) => m.status === 'dead').length
  const stuckCount = monitors.filter((m) => m.status === 'stuck').length
  const avgProgress = monitors.length > 0
    ? Math.round(parsedMonitors.reduce((acc, monitor) => acc + monitor._derived.progressValue, 0) / monitors.length)
    : 0
  const totalDownloadKbps = parsedMonitors.reduce((acc, monitor) => acc + monitor._derived.speedDownKbps, 0)

  const handleExpandAll = () => {
    setExpandedById((prev) => {
      const next = { ...prev }
      for (const id of visibleMonitorIds) next[id] = true
      return next
    })
  }

  const handleCollapseAll = () => {
    setExpandedById((prev) => {
      const next = { ...prev }
      for (const id of visibleMonitorIds) next[id] = false
      return next
    })
  }

  const handleSelectAllVisible = (checked) => {
    const shouldSelect = Boolean(checked)
    setSelectedById((prev) => {
      const next = { ...prev }
      for (const id of visibleMonitorIds) {
        next[id] = shouldSelect
      }
      return next
    })
  }

  const handleBatchStop = async () => {
    if (!apiKey) {
      setActionError('Set API key in Settings to run batch stop')
      return
    }

    const targetMonitors = selectedMonitors.filter((m) => m.status !== 'dead')
    if (targetMonitors.length === 0) {
      setActionError('No stoppable selected sessions')
      return
    }

    setBatchBusy(true)
    setActionError(null)
    const failures = []

    try {
      for (const monitor of targetMonitors) {
        setStoppingById((prev) => ({ ...prev, [monitor.monitor_id]: true }))
        try {
          const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/${encodeURIComponent(monitor.monitor_id)}`, {
            method: 'DELETE',
            headers: {
              Authorization: `Bearer ${apiKey}`,
            },
          })
          if (!response.ok) {
            failures.push(monitor.monitor_id)
          }
        } catch {
          failures.push(monitor.monitor_id)
        } finally {
          setStoppingById((prev) => ({ ...prev, [monitor.monitor_id]: false }))
        }
      }

      await fetchMonitorsNow(false)

      if (failures.length > 0) {
        setActionError(`Batch stop completed with ${failures.length} failure(s)`)
      }
    } finally {
      setBatchBusy(false)
    }
  }

  const handleBatchDelete = async () => {
    if (!apiKey) {
      setActionError('Set API key in Settings to run batch delete')
      return
    }
    if (selectedMonitors.length === 0) {
      setActionError('No selected sessions to delete')
      return
    }

    setBatchBusy(true)
    setActionError(null)
    const failures = []

    try {
      for (const monitor of selectedMonitors) {
        setDeletingById((prev) => ({ ...prev, [monitor.monitor_id]: true }))
        try {
          const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/${encodeURIComponent(monitor.monitor_id)}/entry`, {
            method: 'DELETE',
            headers: {
              Authorization: `Bearer ${apiKey}`,
            },
          })
          if (!response.ok) {
            failures.push(monitor.monitor_id)
          }
        } catch {
          failures.push(monitor.monitor_id)
        } finally {
          setDeletingById((prev) => ({ ...prev, [monitor.monitor_id]: false }))
        }
      }

      await fetchMonitorsNow(false)
      setSelectedById({})

      if (failures.length > 0) {
        setActionError(`Batch delete completed with ${failures.length} failure(s)`)
      }
    } finally {
      setBatchBusy(false)
    }
  }

  const handleM3uFilePicked = async (event) => {
    const file = event?.target?.files?.[0]
    if (!file) return

    try {
      const text = await file.text()
      setM3uFileName(file.name || 'playlist.m3u')
      setM3uContent(text || '')
      setM3uEntries([])
      setM3uSelectedById({})
      setActionError(null)
    } catch {
      setActionError('Failed to read selected M3U file')
    }
  }

  const handleParseM3u = async () => {
    if (!apiKey) {
      setActionError('Set API key in Settings to parse M3U playlists')
      return
    }
    if (!m3uContent.trim()) {
      setActionError('Select an M3U file first')
      return
    }

    setM3uParsing(true)
    setActionError(null)
    try {
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/parse-m3u`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          m3u_content: m3uContent,
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

      const payload = await response.json()
      const items = Array.isArray(payload?.items) ? payload.items : []
      setM3uEntries(items)
      const initialSelection = {}
      for (const item of items) {
        if (item?.content_id) initialSelection[item.content_id] = true
      }
      setM3uSelectedById(initialSelection)
    } catch (err) {
      setActionError(err?.message || 'Failed to parse M3U content')
    } finally {
      setM3uParsing(false)
    }
  }

  const handleStartSelectedM3uEntries = async () => {
    if (!apiKey) {
      setActionError('Set API key in Settings to start monitor sessions')
      return
    }

    const selectedEntries = m3uEntries.filter((entry) => Boolean(m3uSelectedById[entry.content_id]))
    if (selectedEntries.length === 0) {
      setActionError('Select at least one parsed playlist entry')
      return
    }

    setM3uStarting(true)
    setActionError(null)

    const failures = []
    try {
      for (const entry of selectedEntries) {
        try {
          const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/start`, {
            method: 'POST',
            headers: {
              'Content-Type': 'application/json',
              Authorization: `Bearer ${apiKey}`,
            },
            body: JSON.stringify({
              content_id: entry.content_id,
              stream_name: entry.name || null,
              live_delay: Number(newMonitor.live_delay || 0),
              interval_s: Number(newMonitor.interval_s || 1),
              run_seconds: Number(newMonitor.run_seconds || 0),
            }),
          })
          if (!response.ok) {
            failures.push(entry.content_id)
          }
        } catch {
          failures.push(entry.content_id)
        }
      }

      await fetchMonitorsNow(false)
      if (failures.length > 0) {
        setActionError(`Started with ${failures.length} failure(s)`)
      }
    } finally {
      setM3uStarting(false)
    }
  }

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

      <Card className="border-slate-200 bg-gradient-to-br from-white via-slate-50 to-sky-50 dark:border-slate-800 dark:from-slate-950 dark:via-slate-950 dark:to-slate-900/70">
        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Radio className="h-4 w-4" />
            Start Monitoring Session
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-2 md:grid-cols-6">
            <Input
              className="md:col-span-3"
              placeholder="content_id / infohash"
              value={newMonitor.content_id}
              onChange={(e) => setNewMonitor((prev) => ({ ...prev, content_id: e.target.value }))}
            />
            <Input
              type="number"
              min="0"
              step="1"
              placeholder="live_delay"
              title="Starts live streams slightly behind the live edge to improve buffer stability. 0 disables this feature."
              value={newMonitor.live_delay}
              onChange={(e) => setNewMonitor((prev) => ({ ...prev, live_delay: e.target.value }))}
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
            <span className="text-xs text-muted-foreground">live_delay 0 disables seekback, interval default 1s, run_seconds 0 means continuous</span>
          </div>

          <div className="mt-4 rounded-lg border border-slate-200 bg-white p-3 dark:border-slate-800 dark:bg-slate-900/50">
            <p className="text-xs font-medium text-slate-700 dark:text-slate-300">Import M3U (acestream://)</p>
            <p className="mt-1 text-xs text-muted-foreground">Upload an M3U file to parse stream names and AceStream IDs.</p>

            <div className="mt-2 flex flex-wrap items-center gap-2">
              <Input type="file" accept=".m3u,.m3u8,text/plain" onChange={handleM3uFilePicked} className="max-w-md" />
              <Button size="sm" variant="outline" onClick={handleParseM3u} disabled={m3uParsing || !m3uContent || !apiKey}>
                {m3uParsing ? 'Parsing...' : 'Parse playlist'}
              </Button>
              {m3uFileName && <span className="text-xs text-muted-foreground">{m3uFileName}</span>}
            </div>

            {m3uEntries.length > 0 && (
              <div className="mt-3 space-y-2">
                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => {
                      const next = {}
                      for (const entry of m3uEntries) next[entry.content_id] = true
                      setM3uSelectedById(next)
                    }}
                  >
                    Select all
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => setM3uSelectedById({})}
                  >
                    Clear selection
                  </Button>
                  <Button
                    size="sm"
                    onClick={handleStartSelectedM3uEntries}
                    disabled={m3uStarting || !apiKey}
                  >
                    {m3uStarting ? 'Starting selected...' : 'Start selected entries'}
                  </Button>
                </div>

                <div className="max-h-56 space-y-1 overflow-auto rounded-md border border-slate-200 bg-slate-50 p-2 dark:border-slate-800 dark:bg-slate-950/40">
                  {m3uEntries.map((entry) => (
                    <div key={entry.content_id} className="flex items-center justify-between gap-2 rounded border border-slate-200 bg-white px-2 py-1 dark:border-slate-700 dark:bg-slate-900/60">
                      <div className="flex items-center gap-2">
                        <Checkbox
                          checked={Boolean(m3uSelectedById[entry.content_id])}
                          onCheckedChange={(checked) => {
                            const shouldSelect = Boolean(checked)
                            setM3uSelectedById((prev) => ({ ...prev, [entry.content_id]: shouldSelect }))
                          }}
                        />
                        <div>
                          <p className="text-xs font-medium text-slate-800 dark:text-slate-200">{entry.name || 'Unnamed stream'}</p>
                          <p className="text-[11px] text-muted-foreground">{entry.content_id}</p>
                        </div>
                      </div>
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={() => setNewMonitor((prev) => ({ ...prev, content_id: entry.content_id }))}
                      >
                        Use ID
                      </Button>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>

          {actionError && (
            <p className="mt-2 text-xs text-red-600 dark:text-red-400">{actionError}</p>
          )}
        </CardContent>
      </Card>

      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 dark:border-emerald-900 dark:bg-emerald-950/20">
          <p className="text-xs text-emerald-700 dark:text-emerald-300">Running Streams</p>
          <p className="mt-1 text-2xl font-semibold text-emerald-900 dark:text-emerald-100">{runningCount}</p>
        </div>
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/20">
          <p className="text-xs text-amber-700 dark:text-amber-300">Stuck Sessions</p>
          <p className="mt-1 text-2xl font-semibold text-amber-900 dark:text-amber-100">{stuckCount}</p>
        </div>
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 dark:border-rose-900 dark:bg-rose-950/20">
          <p className="text-xs text-rose-700 dark:text-rose-300">Dead Sessions</p>
          <p className="mt-1 text-2xl font-semibold text-rose-900 dark:text-rose-100">{deadCount}</p>
        </div>
        <div className="rounded-xl border border-sky-200 bg-sky-50 p-3 dark:border-sky-900 dark:bg-sky-950/20">
          <p className="text-xs text-sky-700 dark:text-sky-300">Aggregate Downlink</p>
          <p className="mt-1 text-2xl font-semibold text-sky-900 dark:text-sky-100">{formatBytesPerSecond(totalDownloadKbps * 1024)}</p>
        </div>
      </div>

      <Card className="border-slate-200 bg-gradient-to-b from-white to-slate-50 dark:border-slate-800 dark:from-slate-950 dark:to-slate-900/60">
        <CardHeader>
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
            <CardTitle className="text-base flex items-center gap-2">
            <Activity className="h-4 w-4" />
            Session List
            </CardTitle>
            <div className="grid gap-2 sm:grid-cols-2 lg:flex lg:items-center">
              <Select value={sortMode} onValueChange={setSortMode}>
                <SelectTrigger className="h-8 min-w-[180px] text-xs">
                  <SelectValue placeholder="Sort sessions" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="status_then_recent">Status then recent</SelectItem>
                  <SelectItem value="recent">Most recent first</SelectItem>
                  <SelectItem value="speed">Highest downlink</SelectItem>
                  <SelectItem value="progress">Highest progress</SelectItem>
                  <SelectItem value="content">Content id A-Z</SelectItem>
                </SelectContent>
              </Select>

              <div className="flex items-center gap-2">
                <Button size="sm" variant={groupByStatus ? 'default' : 'outline'} onClick={() => setGroupByStatus((prev) => !prev)}>
                  {groupByStatus ? 'Grouped' : 'Ungrouped'}
                </Button>
                <Button size="sm" variant={viewMode === 'table' ? 'default' : 'outline'} onClick={() => setViewMode((prev) => (prev === 'cards' ? 'table' : 'cards'))}>
                  {viewMode === 'table' ? 'Table mode' : 'Card mode'}
                </Button>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            <Button size="sm" variant="outline" onClick={handleExpandAll}>Expand all</Button>
            <Button size="sm" variant="outline" onClick={handleCollapseAll}>Collapse all</Button>
            <div className="mx-1 h-4 w-px bg-slate-300 dark:bg-slate-700" />
            <div className="flex flex-wrap items-center gap-1">
              <Button size="sm" variant={statusFilter === 'all' ? 'default' : 'outline'} onClick={() => setStatusFilter('all')}>
                All ({filterCounts.all})
              </Button>
              <Button size="sm" variant={statusFilter === 'active' ? 'default' : 'outline'} onClick={() => setStatusFilter('active')}>
                Active ({filterCounts.active})
              </Button>
              <Button size="sm" variant={statusFilter === 'running' ? 'default' : 'outline'} onClick={() => setStatusFilter('running')}>
                Running ({filterCounts.running})
              </Button>
              <Button size="sm" variant={statusFilter === 'stuck' ? 'warning' : 'outline'} onClick={() => setStatusFilter('stuck')}>
                Stuck ({filterCounts.stuck})
              </Button>
              <Button size="sm" variant={statusFilter === 'dead' ? 'destructive' : 'outline'} onClick={() => setStatusFilter('dead')}>
                Dead ({filterCounts.dead})
              </Button>
            </div>
            <div className="mx-1 h-4 w-px bg-slate-300 dark:bg-slate-700" />
            <div className="flex items-center gap-2 rounded-md border border-slate-200 px-2 py-1 dark:border-slate-800">
              <Checkbox checked={allVisibleSelected} onCheckedChange={handleSelectAllVisible} />
              <span className="text-xs text-muted-foreground">Select visible ({selectedVisibleIds.length}/{visibleMonitorIds.length})</span>
            </div>
            <Button size="sm" variant="outline" disabled={batchBusy || selectedMonitors.length === 0 || !apiKey} onClick={handleBatchStop}>
              Batch stop
            </Button>
            <Button size="sm" variant="destructive" disabled={batchBusy || selectedMonitors.length === 0 || !apiKey} onClick={handleBatchDelete}>
              Batch delete
            </Button>
          </div>

          <p className="text-xs text-muted-foreground">Compact by default. Expand any block for full telemetry details. Showing {sortedMonitors.length} filtered sessions.</p>
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
              {groupedMonitors.map((group) => (
                <div key={group.key} className="space-y-2">
                  {groupByStatus && (
                    <div className="flex items-center justify-between rounded-md border border-slate-200 bg-slate-100 px-2 py-1 text-xs text-slate-700 dark:border-slate-800 dark:bg-slate-900/60 dark:text-slate-300">
                      <span className="font-medium">{group.label}</span>
                      <span>{group.items.length}</span>
                    </div>
                  )}

                  {group.items.map((monitor) => {
                const latest = monitor._derived.latest || {}
                const movement = monitor.livepos_movement || {}
                const statusText = latest.status_text || latest.status || 'unknown'
                const peers = monitor._derived.peers
                const speedDown = monitor._derived.speedDownKbps
                const speedUp = monitor._derived.speedUpKbps
                const progress = monitor._derived.progressValue
                const livepos = latest.livepos || {}
                const posSeries = buildSeries(monitor.recent_status || [], ['livepos', 'pos'])
                const lastTsSeries = buildSeries(monitor.recent_status || [], ['livepos', 'last_ts'])
                const deadReason = monitor.dead_reason || monitor.last_error
                const isExpanded = Boolean(expandedById[monitor.monitor_id])
                const isCompact = viewMode === 'table'
                const engineInfo = monitor.engine || {}
                const engineId = engineInfo.container_id || 'n/a'
                const engineShortId = engineId !== 'n/a' ? engineId.slice(0, 12) : 'n/a'
                const isPlayingInProxy = activeProxyKeys.has(normalizeContentRef(monitor.content_id))

                return (
                  <Collapsible
                    key={monitor.monitor_id}
                    open={isExpanded}
                    onOpenChange={(open) => setExpandedById((prev) => ({ ...prev, [monitor.monitor_id]: open }))}
                  >
                    <div className={`rounded-xl border shadow-sm transition-colors ${isCompact ? 'p-2' : 'p-3'} ${statusAccent(monitor.status)}`}>
                      <div className="flex items-start gap-2">
                        <Checkbox
                          checked={Boolean(selectedById[monitor.monitor_id])}
                          onCheckedChange={(checked) => {
                            const shouldSelect = Boolean(checked)
                            setSelectedById((prev) => ({ ...prev, [monitor.monitor_id]: shouldSelect }))
                          }}
                          className="mt-1"
                        />

                        <CollapsibleTrigger asChild>
                        <button type="button" className="w-full text-left">
                          <div className={`flex items-start justify-between gap-3 ${isCompact ? 'flex-wrap' : ''}`}>
                            <div className="min-w-0 flex-1">
                              {monitor.stream_name ? (
                                <>
                                  <p className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">{monitor.stream_name}</p>
                                  <p className="truncate text-[11px] text-muted-foreground">{monitor.content_id}</p>
                                </>
                              ) : (
                                <p className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">{monitor.content_id}</p>
                              )}
                              <div className={`mt-2 flex flex-wrap items-center gap-2 ${isCompact ? 'text-[11px]' : ''}`}>
                                <Badge variant={statusVariant(monitor.status)}>{monitor.status}</Badge>
                                <Badge variant={movementVariant(movement, monitor.status)}>{movementLabel(movement, monitor.status)}</Badge>
                                <Badge variant="info" className="gap-1">
                                  <Users className="h-3 w-3" />
                                  peers {peers}
                                </Badge>
                                <Badge variant="secondary" className="gap-1">
                                  <Gauge className="h-3 w-3" />
                                  {formatBytesPerSecond((speedDown || 0) * 1024)}
                                </Badge>
                                <Badge variant="outline">
                                  engine {engineShortId}
                                </Badge>
                                {isPlayingInProxy && (
                                  <Badge
                                    variant="default"
                                    className="px-1.5"
                                    title="Playing in proxy"
                                    aria-label="Playing in proxy"
                                  >
                                    <Play className="h-3 w-3" />
                                  </Badge>
                                )}
                              </div>
                            </div>

                            <div className="flex flex-col items-end gap-1">
                              <span className="text-xs text-muted-foreground">{formatAge(monitor.last_collected_at)}</span>
                              <div className="flex items-center gap-1 text-xs text-muted-foreground">
                                <span>{isExpanded ? 'Collapse' : 'Expand'}</span>
                                {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                              </div>
                            </div>
                          </div>

                          <div className={`mt-3 grid gap-2 ${isCompact ? 'grid-cols-2 lg:grid-cols-5' : 'sm:grid-cols-2 lg:grid-cols-4'}`}>
                            <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
                              <p className="text-[11px] text-muted-foreground">Status</p>
                              <p className="mt-1 text-sm font-medium">{statusText}</p>
                            </div>
                            <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
                              <p className="text-[11px] text-muted-foreground">Down / Up</p>
                              <p className="mt-1 text-sm font-medium">{formatBytesPerSecond((speedDown || 0) * 1024)} / {formatBytesPerSecond((speedUp || 0) * 1024)}</p>
                            </div>
                            <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
                              <p className="text-[11px] text-muted-foreground">Progress</p>
                              <p className="mt-1 text-sm font-medium">{progress}%</p>
                              <Progress className="mt-1 h-1.5 bg-slate-200 dark:bg-slate-800" value={Math.max(0, Math.min(100, Number(progress) || 0))} />
                            </div>
                            <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
                              <p className="text-[11px] text-muted-foreground">Movement events</p>
                              <p className="mt-1 text-sm font-medium">{movement.movement_events ?? 0} / {movement.sample_points ?? 0}</p>
                            </div>
                            {isCompact && (
                              <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
                                <p className="text-[11px] text-muted-foreground">ID</p>
                                <p className="mt-1 truncate text-sm font-medium">{monitor.monitor_id.slice(0, 8)}</p>
                              </div>
                            )}
                          </div>
                        </button>
                        </CollapsibleTrigger>
                      </div>

                      <CollapsibleContent>
                        {monitor.status === 'dead' && (
                          <p className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-700 dark:border-rose-900 dark:bg-rose-950/20 dark:text-rose-300">
                            dead reason: {deadReason || 'unknown'}
                          </p>
                        )}

                        <div className="mt-3 grid gap-2 md:grid-cols-2 lg:grid-cols-4 text-xs">
                          <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
                            <p className="text-muted-foreground">Position Delta</p>
                            <p className="mt-1 flex items-center gap-1 font-medium">
                              <MoveRight className="h-3 w-3" />
                              {movement.pos_delta ?? 'n/a'}
                            </p>
                          </div>

                          <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
                            <p className="text-muted-foreground">Live Timestamp Delta</p>
                            <p className="mt-1 font-medium">{movement.last_ts_delta ?? 'n/a'}</p>
                          </div>

                          <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
                            <p className="text-muted-foreground">Downloaded Delta</p>
                            <p className="mt-1 font-medium">
                              {movement.downloaded_delta != null ? formatBytes(movement.downloaded_delta) : 'n/a'}
                            </p>
                          </div>

                          <div className="rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
                            <p className="text-muted-foreground">Current Timeline</p>
                            <p className="mt-1 font-medium">pos {movement.current_pos ?? 'n/a'} / ts {movement.current_last_ts ?? 'n/a'}</p>
                          </div>
                        </div>

                        <div className="mt-3 grid gap-3 md:grid-cols-2">
                          <div className="rounded-lg border border-emerald-200 bg-emerald-50 p-2 dark:border-emerald-900 dark:bg-emerald-950/20">
                            <p className="mb-1 text-xs text-emerald-700 dark:text-emerald-300">POS movement (sliding trend)</p>
                            <Sparkline values={posSeries} color="#22c55e" label="pos trend" />
                          </div>
                          <div className="rounded-lg border border-sky-200 bg-sky-50 p-2 dark:border-sky-900 dark:bg-sky-950/20">
                            <p className="mb-1 text-xs text-sky-700 dark:text-sky-300">last_ts movement (sliding trend)</p>
                            <Sparkline values={lastTsSeries} color="#0ea5e9" label="last_ts trend" />
                          </div>
                        </div>

                        <div className="mt-3 rounded-lg border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
                          <p className="mb-1 text-xs text-muted-foreground">Live buffer window (first_ts -&gt; pos -&gt; last_ts)</p>
                          <BufferWindowBar livepos={livepos} />
                        </div>

                        <div className="mt-3 flex flex-wrap items-center gap-2">
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
                      </CollapsibleContent>
                    </div>
                  </Collapsible>
                )
                  })}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <div className="text-xs text-muted-foreground">
        Fleet snapshot: {monitors.length} sessions, average progress {avgProgress}%.
      </div>
    </div>
  )
}
