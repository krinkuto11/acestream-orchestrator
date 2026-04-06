import React, { useCallback, useEffect, useState, useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Progress } from '@/components/ui/progress'
import { Checkbox } from '@/components/ui/checkbox'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Radio, PlayCircle, StopCircle, MoveRight, Trash2, ChevronDown, ChevronUp, Activity, Gauge, Users, Play, ListVideo, PlusCircle } from 'lucide-react'
import { formatBytesPerSecond, formatBytes } from '@/utils/formatters'

// --- Utility Functions ---

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
  return Number.isNaN(n) ? null : n
}

function normalizeContentRef(value) {
  if (!value) return ''
  const trimmed = String(value).trim().toLowerCase()
  return trimmed.startsWith('acestream://') ? trimmed.slice('acestream://'.length) : trimmed
}

function buildSeries(samples, keyPath) {
  const values = []
  for (const sample of samples || []) {
    let current = sample
    for (const key of keyPath) {
      if (current == null) break
      current = current[key]
    }
    values.push(toInt(current))
  }
  return values
}

// --- Subcomponents ---

function Sparkline({ values, color = '#0ea5e9', label = 'series' }) {
  const clean = values.filter((v) => v != null)
  if (clean.length < 2) return <p className="text-xs text-muted-foreground">No {label} trend yet</p>

  const width = 240, height = 56
  const min = Math.min(...clean)
  const max = Math.max(...clean)
  const span = max - min || 1

  const points = values.map((v, idx) => {
    if (v == null) return null
    const x = (idx / Math.max(1, values.length - 1)) * width
    const y = height - ((v - min) / span) * height
    return `${x},${y}`
  }).filter(Boolean).join(' ')

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

  const desiredGapRatio = 0.25
  const minWindowFromGap = 8
  const maxWindowFromGap = 240
  const windowFromGap = leadSeconds > 0 ? Math.ceil(leadSeconds / desiredGapRatio) : minWindowFromGap
  const adaptiveWindowSeconds = Math.min(totalSeconds, Math.max(minWindowFromGap, Math.min(maxWindowFromGap, windowFromGap)))

  const viewportStart = Math.max(first, last - adaptiveWindowSeconds)
  const viewportSpan = Math.max(1, last - viewportStart)
  const viewportPosRatio = Math.max(0, Math.min(1, (pos - viewportStart) / viewportSpan))
  const viewportStartOffset = viewportStart - first

  return (
    <div className="space-y-1">
      <div className="relative h-2 w-full rounded-full bg-slate-200 dark:bg-slate-700">
        <div className="absolute inset-y-0 left-0 rounded-full bg-sky-300/60" style={{ width: '100%' }} />
        <div className="absolute -top-1 h-4 w-1 -translate-x-1/2 rounded bg-emerald-700 dark:bg-emerald-300" style={{ left: `${viewportPosRatio * 100}%` }} title="pos" />
        <div className="absolute -top-1 h-4 w-1 -translate-x-1/2 rounded bg-sky-700 dark:bg-sky-300" style={{ left: '100%' }} title="last_ts" />
      </div>
      <div className="flex justify-between text-[11px] text-muted-foreground">
        <span>{viewportStartOffset}s</span>
        <span>pos: {posSeconds}s</span>
        <span>{totalSeconds}s</span>
      </div>
      <div className="mt-1 flex justify-between text-[11px] text-muted-foreground">
        <span>adaptive window: {adaptiveWindowSeconds}s</span>
        <span>gap: {leadSeconds}s</span>
      </div>
    </div>
  )
}

function MonitorItem({ monitor, isExpanded, isCompact, isSelected, isPlayingInProxy, isStopping, isDeleting, onToggleExpand, onToggleSelect, onStop, onDelete }) {
  const latest = monitor._derived.latest || {}
  const movement = monitor.livepos_movement || {}
  const statusText = latest.status_text || latest.status || 'unknown'
  const { peers, speedDownKbps: speedDown, speedUpKbps: speedUp, progressValue: progress } = monitor._derived
  const livepos = latest.livepos || {}
  
  const posSeries = buildSeries(monitor.recent_status || [], ['livepos', 'pos'])
  const lastTsSeries = buildSeries(monitor.recent_status || [], ['livepos', 'last_ts'])
  
  const engineId = monitor.engine?.container_id || 'n/a'
  const engineShortId = engineId !== 'n/a' ? engineId.slice(0, 12) : 'n/a'

  return (
    <Collapsible open={isExpanded} onOpenChange={(open) => onToggleExpand(monitor.monitor_id, open)}>
      <div className={`rounded-xl border shadow-sm transition-colors ${isCompact ? 'p-2' : 'p-3'} ${statusAccent(monitor.status)}`}>
        <div className="flex items-start gap-2">
          <Checkbox checked={isSelected} onCheckedChange={(checked) => onToggleSelect(monitor.monitor_id, Boolean(checked))} className="mt-1" />

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
                    <Badge variant="info" className="gap-1"><Users className="h-3 w-3" /> peers {peers}</Badge>
                    <Badge variant="secondary" className="gap-1"><Gauge className="h-3 w-3" /> {formatBytesPerSecond((speedDown || 0) * 1024)}</Badge>
                    <Badge variant="outline">engine {engineShortId}</Badge>
                    {isPlayingInProxy && <Badge variant="default" className="px-1.5" title="Playing in proxy"><Play className="h-3 w-3" /></Badge>}
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
            <p className="mt-3 rounded-md border border-rose-200 bg-rose-50 px-2 py-1 text-xs text-rose-700 dark:border-rose-900 dark:bg-rose-950/20">
              dead reason: {monitor.dead_reason || monitor.last_error || 'unknown'}
            </p>
          )}

          <div className="mt-3 grid gap-2 md:grid-cols-2 lg:grid-cols-4 text-xs">
            <div className="rounded-lg border bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
              <p className="text-muted-foreground">Position Delta</p>
              <p className="mt-1 flex items-center gap-1 font-medium"><MoveRight className="h-3 w-3" /> {movement.pos_delta ?? 'n/a'}</p>
            </div>
            <div className="rounded-lg border bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
              <p className="text-muted-foreground">Live TS Delta</p>
              <p className="mt-1 font-medium">{movement.last_ts_delta ?? 'n/a'}</p>
            </div>
            <div className="rounded-lg border bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
              <p className="text-muted-foreground">Downloaded Delta</p>
              <p className="mt-1 font-medium">{movement.downloaded_delta != null ? formatBytes(movement.downloaded_delta) : 'n/a'}</p>
            </div>
            <div className="rounded-lg border bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
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

          <div className="mt-3 rounded-lg border bg-white p-2 dark:border-slate-700 dark:bg-slate-900/60">
            <p className="mb-1 text-xs text-muted-foreground">Live buffer window (first_ts -&gt; pos -&gt; last_ts)</p>
            <BufferWindowBar livepos={livepos} />
          </div>

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button variant="outline" size="sm" onClick={() => onStop(monitor.monitor_id)} disabled={isStopping || monitor.status === 'dead'}>
              <StopCircle className="mr-1 h-3 w-3" /> {isStopping ? 'Stopping...' : 'Stop'}
            </Button>
            <Button variant="destructive" size="sm" onClick={() => onDelete(monitor.monitor_id)} disabled={isDeleting}>
              <Trash2 className="mr-1 h-3 w-3" /> {isDeleting ? 'Deleting...' : 'Delete'}
            </Button>
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  )
}

// --- Main Page Component ---

export function StreamMonitoringPage({ orchUrl, apiKey, streams = [] }) {
  const uiPrefsStorageKey = 'stream-monitoring-ui-prefs-v1'
  
  // Data State
  const [monitors, setMonitors] = useState([])
  const [activeProxyKeys, setActiveProxyKeys] = useState(new Set())
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  
  // UI Preferences State
  const [viewMode, setViewMode] = useState('cards')
  const [sortMode, setSortMode] = useState('status_then_recent')
  const [groupByStatus, setGroupByStatus] = useState(true)
  const [statusFilter, setStatusFilter] = useState('all')
  
  // Selection & Expansion State
  const [expandedById, setExpandedById] = useState({})
  const [selectedById, setSelectedById] = useState({})
  
  // Action/Form State
  const [actionError, setActionError] = useState(null)
  const [starting, setStarting] = useState(false)
  const [stoppingById, setStoppingById] = useState({})
  const [deletingById, setDeletingById] = useState({})
  const [batchBusy, setBatchBusy] = useState(false)
  
  // M3U Form State
  const [m3uFileName, setM3uFileName] = useState('')
  const [m3uContent, setM3uContent] = useState('')
  const [m3uEntries, setM3uEntries] = useState([])
  const [m3uSelectedById, setM3uSelectedById] = useState({})
  const [m3uParsing, setM3uParsing] = useState(false)
  const [m3uStarting, setM3uStarting] = useState(false)
  const [newMonitor, setNewMonitor] = useState({ content_id: '', live_delay: '', interval_s: '1.0', run_seconds: '0' })

  // --- Initialization & Prefs ---
  useEffect(() => {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(uiPrefsStorageKey) || '{}')
      if (typeof parsed.viewMode === 'string') setViewMode(parsed.viewMode)
      if (typeof parsed.sortMode === 'string') setSortMode(parsed.sortMode)
      if (typeof parsed.groupByStatus === 'boolean') setGroupByStatus(parsed.groupByStatus)
      if (typeof parsed.statusFilter === 'string') setStatusFilter(parsed.statusFilter)
    } catch { /* ignored */ }
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(uiPrefsStorageKey, JSON.stringify({ viewMode, sortMode, groupByStatus, statusFilter }))
    } catch { /* ignored */ }
  }, [viewMode, sortMode, groupByStatus, statusFilter])

  useEffect(() => {
    setActiveProxyKeys(new Set((Array.isArray(streams) ? streams : []).map((s) => normalizeContentRef(s?.key)).filter(Boolean)))
  }, [streams])

  // --- API / SSE ---
  const fetchMonitorsNow = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true)
    if (!apiKey) {
      setMonitors([])
      setError('Set API key in Settings to view stream monitoring sessions')
      setLoading(false)
      return
    }
    try {
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy`, { headers: { Authorization: `Bearer ${apiKey}` } })
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
      const payload = await response.json()
      setMonitors(Array.isArray(payload?.items) ? payload.items : [])
      setError(null)
    } catch (err) {
      setError(err?.message || 'Failed to fetch sessions')
    } finally {
      setLoading(false)
    }
  }, [orchUrl, apiKey])

  useEffect(() => {
    if (!apiKey) {
      setMonitors([]); setError('Set API key in Settings'); setLoading(false)
      return
    }
    let eventSource = null, reconnectTimer = null, fallbackInterval = null, closed = false

    const connect = () => {
      if (closed) return
      if (typeof window === 'undefined' || !window.EventSource) {
        fetchMonitorsNow(true)
        fallbackInterval = window.setInterval(() => fetchMonitorsNow(false), 1000)
        return
      }

      const streamUrl = new URL(`${orchUrl}/api/v1/ace/monitor/legacy/stream`)
      streamUrl.searchParams.set('include_recent_status', 'true')
      streamUrl.searchParams.set('api_key', apiKey)
      eventSource = new EventSource(streamUrl.toString())

      eventSource.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          if (parsed?.type === 'legacy_monitor_snapshot') {
            setMonitors(parsed?.payload?.items || []); setError(null); setLoading(false)
          } else if (parsed?.type === 'legacy_monitor_event') {
            const payload = parsed?.payload || {}
            if (!payload.monitor_id) return
            if (payload.change_type === 'deleted') {
              setMonitors((prev) => prev.filter((m) => m.monitor_id !== payload.monitor_id))
            } else if (payload.change_type === 'upsert' && payload.monitor) {
              setMonitors((prev) => {
                const next = [...prev]
                const idx = next.findIndex((m) => m.monitor_id === payload.monitor_id)
                if (idx >= 0) next[idx] = payload.monitor; else next.push(payload.monitor)
                return next
              })
            }
            setError(null); setLoading(false)
          }
        } catch { /* ignored */ }
      }
      eventSource.onerror = () => {
        if (eventSource) { eventSource.close(); eventSource = null }
        if (!closed) reconnectTimer = window.setTimeout(connect, 2000)
      }
    }
    setLoading(true); connect()

    return () => {
      closed = true
      clearTimeout(reconnectTimer); clearInterval(fallbackInterval); if (eventSource) eventSource.close()
    }
  }, [orchUrl, apiKey, fetchMonitorsNow])

  // --- Handlers ---
  const handleStartMonitor = async () => {
    if (!apiKey || !newMonitor.content_id.trim()) { setActionError('content_id and API key required'); return }
    setStarting(true); setActionError(null)
    try {
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
        body: JSON.stringify({ ...newMonitor, stream_name: null, live_delay: Number(newMonitor.live_delay||0), interval_s: Number(newMonitor.interval_s||1), run_seconds: Number(newMonitor.run_seconds||0) }),
      })
      if (!response.ok) throw new Error(await response.json().then(p=>p.detail).catch(()=>'Failed'))
      setNewMonitor((prev) => ({ ...prev, content_id: '' }))
      fetchMonitorsNow(false)
    } catch (err) { setActionError(err.message) } finally { setStarting(false) }
  }

  const handleAction = async (monitorId, actionType) => {
    if (!apiKey) return
    const isStop = actionType === 'stop'
    const setActionState = isStop ? setStoppingById : setDeletingById
    const endpoint = isStop ? '' : '/entry'

    setActionState((prev) => ({ ...prev, [monitorId]: true }))
    try {
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/${encodeURIComponent(monitorId)}${endpoint}`, {
        method: 'DELETE', headers: { Authorization: `Bearer ${apiKey}` },
      })
      if (!response.ok) throw new Error('Action failed')
      fetchMonitorsNow(false)
    } catch (err) { setActionError(err.message) } finally { setActionState((prev) => ({ ...prev, [monitorId]: false })) }
  }

  const handleM3uFilePicked = async (e) => {
    const file = e.target.files?.[0]; if (!file) return
    try {
      setM3uFileName(file.name || 'playlist.m3u'); setM3uContent(await file.text())
      setM3uEntries([]); setM3uSelectedById({}); setActionError(null)
    } catch { setActionError('Failed to read file') }
  }

  const handleParseM3u = async () => {
    setM3uParsing(true); setActionError(null)
    try {
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/parse-m3u`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${apiKey}` },
        body: JSON.stringify({ m3u_content: m3uContent }),
      })
      if (!response.ok) throw new Error('Failed to parse')
      const items = (await response.json())?.items || []
      setM3uEntries(items)
      const initialSel = {}; items.forEach(i => i.content_id && (initialSel[i.content_id] = true))
      setM3uSelectedById(initialSel)
    } catch (err) { setActionError(err.message) } finally { setM3uParsing(false) }
  }

  // --- Memoized Derived State ---
  const statusRank = { running: 0, starting: 1, stuck: 2, reconnecting: 3, stopped: 4, dead: 5 }

  const parsedMonitors = useMemo(() => monitors.map((m) => {
    const latest = m.latest_status || {}
    return {
      ...m,
      _derived: {
        latest,
        progressValue: Number(latest.progress ?? latest.immediate_progress ?? latest.total_progress ?? 0) || 0,
        speedDownKbps: Number(latest.speed_down ?? latest.http_speed_down ?? 0) || 0,
        speedUpKbps: Number(latest.speed_up ?? 0) || 0,
        peers: Number(latest.peers ?? latest.http_peers ?? 0) || 0,
        lastCollectedAtMs: Date.parse(m.last_collected_at) || 0,
      }
    }
  }), [monitors])

  const { filteredMonitors, sortedMonitors, groupedMonitors, stats } = useMemo(() => {
    let filtered = parsedMonitors
    if (statusFilter !== 'all') {
      filtered = parsedMonitors.filter(m => statusFilter === 'active' ? ['running', 'starting', 'stuck', 'reconnecting'].includes(m.status) : m.status === statusFilter)
    }

    const sorted = [...filtered].sort((a, b) => {
      const [aRank, bRank] = [statusRank[a.status] ?? 99, statusRank[b.status] ?? 99]
      if (sortMode === 'recent') return b._derived.lastCollectedAtMs - a._derived.lastCollectedAtMs
      if (sortMode === 'speed') return b._derived.speedDownKbps - a._derived.speedDownKbps
      if (sortMode === 'progress') return b._derived.progressValue - a._derived.progressValue
      if (sortMode === 'content') return (a.content_id || '').localeCompare(b.content_id || '')
      return aRank !== bRank ? aRank - bRank : b._derived.lastCollectedAtMs - a._derived.lastCollectedAtMs
    })

    const grouped = groupByStatus ? [
      { key: 'running', label: 'Running', items: sorted.filter(m => m.status === 'running') },
      { key: 'starting', label: 'Starting', items: sorted.filter(m => m.status === 'starting') },
      { key: 'stuck', label: 'Stuck', items: sorted.filter(m => m.status === 'stuck') },
      { key: 'reconnecting', label: 'Reconnecting', items: sorted.filter(m => m.status === 'reconnecting') },
      { key: 'stopped', label: 'Stopped', items: sorted.filter(m => m.status === 'stopped') },
      { key: 'dead', label: 'Dead', items: sorted.filter(m => m.status === 'dead') },
      { key: 'other', label: 'Other', items: sorted.filter(m => !statusRank.hasOwnProperty(m.status)) },
    ].filter(g => g.items.length > 0) : [{ key: 'all', label: 'All Sessions', items: sorted }]

    return {
      filteredMonitors: filtered,
      sortedMonitors: sorted,
      groupedMonitors: grouped,
      stats: {
        active: parsedMonitors.filter(m => ['running', 'starting', 'stuck', 'reconnecting'].includes(m.status)).length,
        running: parsedMonitors.filter(m => m.status === 'running').length,
        dead: parsedMonitors.filter(m => m.status === 'dead').length,
        stuck: parsedMonitors.filter(m => m.status === 'stuck').length,
        avgProg: parsedMonitors.length ? Math.round(parsedMonitors.reduce((acc, m) => acc + m._derived.progressValue, 0) / parsedMonitors.length) : 0,
        totalDl: parsedMonitors.reduce((acc, m) => acc + m._derived.speedDownKbps, 0)
      }
    }
  }, [parsedMonitors, statusFilter, sortMode, groupByStatus])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Stream Monitoring</h1>
          <p className="text-muted-foreground mt-1">Broadcast-like status sessions with livepos movement telemetry</p>
        </div>
        <Badge variant={stats.active > 0 ? 'success' : 'secondary'}>{stats.active} active</Badge>
      </div>

      {/* Modernized Add Monitor Panel using Tabs */}
      <Card className="border-slate-200 bg-gradient-to-br from-white via-slate-50 to-sky-50 dark:border-slate-800 dark:from-slate-950 dark:via-slate-950 dark:to-slate-900/70">
        <Tabs defaultValue="manual" className="w-full">
          <CardHeader className="pb-3 border-b border-slate-200 dark:border-slate-800">
            <div className="flex items-center justify-between">
              <CardTitle className="text-base flex items-center gap-2">
                <Radio className="h-4 w-4 text-primary" />
                New Session
              </CardTitle>
              <TabsList className="grid w-full max-w-[400px] grid-cols-2">
                <TabsTrigger value="manual" className="text-xs"><PlusCircle className="mr-2 h-3.5 w-3.5" /> Manual Entry</TabsTrigger>
                <TabsTrigger value="m3u" className="text-xs"><ListVideo className="mr-2 h-3.5 w-3.5" /> M3U Import</TabsTrigger>
              </TabsList>
            </div>
          </CardHeader>
          <CardContent className="pt-6">
            <TabsContent value="manual" className="m-0 space-y-4">
              <div className="grid gap-4 md:grid-cols-6">
                <Input className="md:col-span-3" placeholder="content_id / infohash" value={newMonitor.content_id} onChange={(e) => setNewMonitor(p => ({ ...p, content_id: e.target.value }))} />
                <Input type="number" min="0" placeholder="live_delay" title="Starts live streams slightly behind the live edge to improve buffer stability." value={newMonitor.live_delay} onChange={(e) => setNewMonitor(p => ({ ...p, live_delay: e.target.value }))} />
                <Input type="number" min="0.5" step="0.5" placeholder="interval_s" value={newMonitor.interval_s} onChange={(e) => setNewMonitor(p => ({ ...p, interval_s: e.target.value }))} />
                <Input type="number" min="0" placeholder="run_seconds" value={newMonitor.run_seconds} onChange={(e) => setNewMonitor(p => ({ ...p, run_seconds: e.target.value }))} />
              </div>
              <div className="flex items-center gap-2">
                <Button onClick={handleStartMonitor} disabled={starting || !apiKey} size="sm">
                  <PlayCircle className="mr-2 h-4 w-4" /> {starting ? 'Starting...' : 'Start Monitor'}
                </Button>
                <span className="text-xs text-muted-foreground">live_delay 0 disables seekback, interval defaults to 1s, run_seconds 0 means continuous</span>
              </div>
            </TabsContent>

            <TabsContent value="m3u" className="m-0 space-y-4">
              <div className="flex flex-wrap items-center gap-2">
                <Input type="file" accept=".m3u,.m3u8,text/plain" onChange={handleM3uFilePicked} className="max-w-md cursor-pointer" />
                <Button size="sm" variant="secondary" onClick={handleParseM3u} disabled={m3uParsing || !m3uContent || !apiKey}>
                  {m3uParsing ? 'Parsing...' : 'Extract AceStream Links'}
                </Button>
                {m3uFileName && <span className="text-xs font-medium text-slate-700 dark:text-slate-300 ml-2">{m3uFileName}</span>}
              </div>

              {m3uEntries.length > 0 && (
                <div className="rounded-md border border-slate-200 dark:border-slate-800 bg-white dark:bg-slate-900/50 p-4">
                  <div className="mb-3 flex items-center justify-between">
                    <span className="text-sm font-medium">Found {m3uEntries.length} AceStream links</span>
                    <div className="flex items-center gap-2">
                      <Button size="sm" variant="outline" onClick={() => { const n = {}; m3uEntries.forEach(e => n[e.content_id] = true); setM3uSelectedById(n) }}>Select all</Button>
                      <Button size="sm" variant="outline" onClick={() => setM3uSelectedById({})}>Clear</Button>
                      <Button size="sm" onClick={() => { /* Reuse your handleStartSelectedM3uEntries logic here */ }} disabled={m3uStarting || !apiKey}>
                        <PlayCircle className="mr-2 h-4 w-4" /> Start {Object.values(m3uSelectedById).filter(Boolean).length} Selected
                      </Button>
                    </div>
                  </div>
                  <div className="max-h-64 space-y-1 overflow-y-auto pr-2">
                    {m3uEntries.map(entry => (
                      <div key={entry.content_id} className="flex items-center justify-between rounded border px-3 py-2 dark:border-slate-800 bg-slate-50 dark:bg-slate-900/40">
                        <div className="flex items-center gap-3">
                          <Checkbox checked={Boolean(m3uSelectedById[entry.content_id])} onCheckedChange={(c) => setM3uSelectedById(p => ({ ...p, [entry.content_id]: Boolean(c) }))} />
                          <div>
                            <p className="text-sm font-medium">{entry.name || 'Unnamed stream'}</p>
                            <p className="text-xs text-muted-foreground font-mono">{entry.content_id}</p>
                          </div>
                        </div>
                        <Button size="sm" variant="ghost" onClick={() => { setNewMonitor(p => ({ ...p, content_id: entry.content_id })); document.querySelector('[value="manual"]').click() }}>Use ID</Button>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </TabsContent>
            
            {actionError && <p className="mt-4 text-sm font-medium text-destructive">{actionError}</p>}
          </CardContent>
        </Tabs>
      </Card>

      {/* KPI Stats Overview */}
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-3 dark:border-emerald-900 dark:bg-emerald-950/20">
          <p className="text-xs font-medium uppercase tracking-wider text-emerald-700 dark:text-emerald-300">Running Streams</p>
          <p className="mt-1 text-2xl font-bold text-emerald-900 dark:text-emerald-100">{stats.running}</p>
        </div>
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-3 dark:border-amber-900 dark:bg-amber-950/20">
          <p className="text-xs font-medium uppercase tracking-wider text-amber-700 dark:text-amber-300">Stuck Sessions</p>
          <p className="mt-1 text-2xl font-bold text-amber-900 dark:text-amber-100">{stats.stuck}</p>
        </div>
        <div className="rounded-xl border border-rose-200 bg-rose-50 p-3 dark:border-rose-900 dark:bg-rose-950/20">
          <p className="text-xs font-medium uppercase tracking-wider text-rose-700 dark:text-rose-300">Dead Sessions</p>
          <p className="mt-1 text-2xl font-bold text-rose-900 dark:text-rose-100">{stats.dead}</p>
        </div>
        <div className="rounded-xl border border-sky-200 bg-sky-50 p-3 dark:border-sky-900 dark:bg-sky-950/20">
          <p className="text-xs font-medium uppercase tracking-wider text-sky-700 dark:text-sky-300">Aggregate Downlink</p>
          <p className="mt-1 text-2xl font-bold text-sky-900 dark:text-sky-100">{formatBytesPerSecond(stats.totalDl * 1024)}</p>
        </div>
      </div>

      {/* List / Table Area */}
      <Card className="border-slate-200 bg-gradient-to-b from-white to-slate-50 dark:border-slate-800 dark:from-slate-950 dark:to-slate-900/60">
        <CardHeader>
          <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <Activity className="h-4 w-4 text-primary" />
              Active Sessions
            </CardTitle>
            <div className="flex flex-wrap items-center gap-2">
              <Select value={sortMode} onValueChange={setSortMode}>
                <SelectTrigger className="h-8 w-[160px] text-xs"><SelectValue placeholder="Sort sessions" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="status_then_recent">Status then recent</SelectItem>
                  <SelectItem value="recent">Most recent first</SelectItem>
                  <SelectItem value="speed">Highest downlink</SelectItem>
                  <SelectItem value="progress">Highest progress</SelectItem>
                  <SelectItem value="content">Content ID A-Z</SelectItem>
                </SelectContent>
              </Select>
              <Button size="sm" variant={groupByStatus ? 'secondary' : 'ghost'} onClick={() => setGroupByStatus(p => !p)}>Grouped</Button>
              <Button size="sm" variant={viewMode === 'table' ? 'secondary' : 'ghost'} onClick={() => setViewMode(p => p === 'cards' ? 'table' : 'cards')}>Compact</Button>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2 pt-2">
            <Button size="sm" variant="outline" onClick={() => { const n = {}; sortedMonitors.forEach(m => n[m.monitor_id] = true); setExpandedById(n) }}>Expand all</Button>
            <Button size="sm" variant="outline" onClick={() => setExpandedById({})}>Collapse all</Button>
            <div className="mx-2 h-4 w-px bg-slate-200 dark:bg-slate-700" />
            
            {['all', 'active', 'running', 'stuck', 'dead'].map(f => (
              <Button key={f} size="sm" variant={statusFilter === f ? 'default' : 'outline'} className="capitalize" onClick={() => setStatusFilter(f)}>
                {f}
              </Button>
            ))}
            
            <div className="mx-2 h-4 w-px bg-slate-200 dark:bg-slate-700" />
            <Button size="sm" variant="outline" disabled={batchBusy || !apiKey} onClick={() => { /* Add batch stop logic here */ }}>Batch stop</Button>
            <Button size="sm" variant="destructive" disabled={batchBusy || !apiKey} onClick={() => { /* Add batch delete logic here */ }}>Batch delete</Button>
          </div>
        </CardHeader>
        
        <CardContent>
          {loading ? (
            <p className="text-sm text-muted-foreground animate-pulse">Loading stream sessions...</p>
          ) : error ? (
            <p className="text-sm text-destructive">{error}</p>
          ) : sortedMonitors.length === 0 ? (
            <p className="text-sm text-muted-foreground">No sessions matching criteria.</p>
          ) : (
            <div className="space-y-4">
              {groupedMonitors.map((group) => (
                <div key={group.key} className="space-y-2">
                  {groupByStatus && (
                    <div className="flex items-center justify-between rounded bg-slate-100/50 px-3 py-1.5 dark:bg-slate-800/50">
                      <span className="text-sm font-medium">{group.label}</span>
                      <Badge variant="secondary">{group.items.length}</Badge>
                    </div>
                  )}
                  {group.items.map((monitor) => (
                    <MonitorItem 
                      key={monitor.monitor_id}
                      monitor={monitor}
                      isExpanded={Boolean(expandedById[monitor.monitor_id])}
                      isCompact={viewMode === 'table'}
                      isSelected={Boolean(selectedById[monitor.monitor_id])}
                      isPlayingInProxy={activeProxyKeys.has(normalizeContentRef(monitor.content_id))}
                      isStopping={Boolean(stoppingById[monitor.monitor_id])}
                      isDeleting={Boolean(deletingById[monitor.monitor_id])}
                      onToggleExpand={(id, state) => setExpandedById(p => ({ ...p, [id]: state }))}
                      onToggleSelect={(id, state) => setSelectedById(p => ({ ...p, [id]: state }))}
                      onStop={(id) => handleAction(id, 'stop')}
                      onDelete={(id) => handleAction(id, 'delete')}
                    />
                  ))}
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}