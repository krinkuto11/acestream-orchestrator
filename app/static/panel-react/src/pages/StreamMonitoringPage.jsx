import React, { useCallback, useEffect, useState, useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Progress } from '@/components/ui/progress'
import { Checkbox } from '@/components/ui/checkbox'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { DropdownMenu, DropdownMenuContent, DropdownMenuRadioGroup, DropdownMenuRadioItem, DropdownMenuTrigger } from '@/components/ui/dropdown-menu'
import { Radio, PlayCircle, StopCircle, MoveRight, Trash2, ChevronDown, ChevronUp, Activity, Gauge, Users, Play, ListVideo, PlusCircle, LayoutGrid, List, Filter } from 'lucide-react'
import { formatBytesPerSecond, formatBytes } from '@/utils/formatters'
import { useTheme } from '@/components/ThemeProvider'
import { toast } from 'sonner' // Assuming sonner is used based on your components.json/UI standard

const ACTIVE_MONITOR_STATUSES = ['running', 'starting', 'stuck', 'reconnecting']
const ALLOWED_VIEW_MODES = new Set(['cards', 'table'])
const ALLOWED_SORT_MODES = new Set(['status_then_recent', 'recent', 'speed', 'progress', 'content'])
const ALLOWED_STATUS_FILTERS = new Set(['all', 'active', 'running', 'starting', 'stuck', 'reconnecting', 'stopped', 'dead'])

// --- Utility Functions ---
// (Keep existing formatAge, movementVariant, movementLabel, statusVariant, statusAccent, toInt, normalizeContentRef, buildSeries)
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

function normalizeMonitorContentId(value) {
  const raw = String(value || '').trim()
  if (!raw) return ''

  const lowered = raw.toLowerCase()
  if (lowered.startsWith('acestream://')) {
    const withoutScheme = raw.slice('acestream://'.length)
    const normalized = withoutScheme.split('?')[0].split('/')[0].trim()
    return normalized
  }

  return raw
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
  if (clean.length < 2) return <p className="text-xs text-muted-foreground">Waiting for data...</p>

  const width = 240, height = 40
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
    <svg viewBox={`0 0 ${width} ${height}`} className="h-10 w-full" preserveAspectRatio="none" aria-label={label}>
      <polyline fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" points={points} />
    </svg>
  )
}

function BufferWindowBar({ livepos, runway = 0, runwayMax = 0 }) {
  const first = toInt(livepos?.live_first ?? livepos?.first_ts)
  const last = toInt(livepos?.live_last ?? livepos?.last_ts)
  const pos = toInt(livepos?.pos)

  if (first == null || last == null || pos == null || last <= first) {
    return <p className="text-xs text-muted-foreground">Unavailable</p>
  }

  const totalSeconds = Math.max(0, last - first)
  const swarmLeadSeconds = Math.max(0, last - pos) // What the engine has downloaded
  const viewerPos = pos - runway
  const viewerLagSeconds = Math.max(0, last - viewerPos) // True viewer lag from live edge

  const adaptiveWindowSeconds = Math.min(totalSeconds, Math.max(8, Math.min(240, viewerLagSeconds > 0 ? Math.ceil(viewerLagSeconds / 0.25) : 8)))

  const viewportStart = Math.max(first, last - adaptiveWindowSeconds)
  const viewportSpan = Math.max(1, last - viewportStart)
  
  const viewportPosRatio = Math.max(0, Math.min(1, (pos - viewportStart) / viewportSpan))
  const viewportViewerRatio = Math.max(0, Math.min(1, (viewerPos - viewportStart) / viewportSpan))
  const viewportViewerMaxRatio = Math.max(0, Math.min(1, (pos - runwayMax - viewportStart) / viewportSpan))

  return (
    <div className="space-y-1.5 w-full">
      <div className="relative h-2 w-full rounded-full bg-slate-200 dark:bg-slate-800 overflow-hidden">
        {/* Layer 1: Swarm Sync / Engine Buffer (Light sky) */}
        <div 
          className="absolute inset-y-0 bg-sky-400/20" 
          style={{ 
            left: `${viewportPosRatio * 100}%`,
            width: `${(1 - viewportPosRatio) * 100}%` 
          }} 
        />
        
        {/* Layer 2: Proxy Inventory (Indigo) */}
        <div 
          className="absolute inset-y-0 bg-indigo-500/60 dark:bg-indigo-600/60" 
          style={{ 
            left: `${viewportViewerRatio * 100}%`,
            width: `${(viewportPosRatio - viewportViewerRatio) * 100}%` 
          }} 
        />

        {/* Layer 3: Viewer Playhead (Red dot) */}
        <div 
          className="absolute top-0 h-full w-1.5 -translate-x-1/2 rounded-full bg-red-500 shadow-[0_0_8px_rgba(239,68,68,0.8)] z-10" 
          style={{ left: `${viewportViewerRatio * 100}%` }} 
          title="Viewer Playhead" 
        />

        {/* Layer 4: Proxy Read Head (Engine connector - Small cyan dot) */}
        <div 
          className="absolute top-0 h-full w-1 -translate-x-1/2 rounded-full bg-sky-500 opacity-50" 
          style={{ left: `${viewportPosRatio * 100}%` }} 
          title="Proxy Read Edge" 
        />

        {/* Layer 5: Live Edge */}
        <div className="absolute top-0 h-full w-1 -translate-x-1/2 rounded-full bg-slate-400 opacity-30" style={{ left: '100%' }} />
      </div>
      
      <div className="grid grid-cols-2 gap-2 text-[9px] font-medium text-muted-foreground uppercase tracking-tighter">
        <div className="flex flex-col">
          <span>Swarm Lead: {swarmLeadSeconds}s</span>
          <span>Proxy Cache: {runway.toFixed(1)}s</span>
        </div>
        <div className="flex flex-col text-right">
          <span className="text-red-600 dark:text-red-400 font-bold">Viewer Lag: {viewerLagSeconds}s</span>
          <span>Window: {adaptiveWindowSeconds}s</span>
        </div>
      </div>
    </div>
  )
}

function MonitorCard({ monitor, isExpanded, isSelected, isPlayingInProxy, isStopping, isDeleting, onToggleExpand, onToggleSelect, onStop, onDelete }) {
  const latest = monitor._derived.latest || {}
  const movement = monitor.livepos_movement || {}
  const statusText = latest.status_text || latest.status || 'unknown'
  const { peers, speedDownKbps: speedDown, speedUpKbps: speedUp, progressValue: progress } = monitor._derived
  const livepos = latest.livepos || {}
  
  const posSeries = buildSeries(monitor.recent_status || [], ['livepos', 'pos'])
  const lastTsSeries = buildSeries(monitor.recent_status || [], ['livepos', 'last_ts'])
  const engineShortId = monitor.engine?.container_id ? monitor.engine.container_id.slice(0, 12) : 'n/a'

  // --- CROSS-TIER TELEMETRY LINKING ---
  // Find the active proxy stream matching this monitor's content_id to calculate real Viewer Lag
  const normalizedContentId = normalizeContentRef(monitor.content_id)
  const matchingStream = streams.find(s => normalizeContentRef(s.key) === normalizedContentId)
  const proxyClients = matchingStream?.clients || []
  
  let proxyRunwayMin = 0
  let proxyRunwayMax = 0
  
  if (proxyClients.length > 0) {
    const runways = proxyClients.map(c => toNumber(c.client_runway_seconds ?? c.buffer_seconds_behind)).filter(v => v !== null)
    if (runways.length > 0) {
      proxyRunwayMin = Math.min(...runways)
      proxyRunwayMax = Math.max(...runways)
    }
  }

  return (
    <Collapsible open={isExpanded} onOpenChange={(open) => onToggleExpand(monitor.monitor_id, open)}>
      <div className={`group rounded-xl border shadow-sm transition-all hover:shadow-md p-4 ${statusAccent(monitor.status)}`}>
        <div className="flex items-start gap-3">
          <Checkbox checked={isSelected} onCheckedChange={(checked) => onToggleSelect(monitor.monitor_id, Boolean(checked))} className="mt-1" />

          <CollapsibleTrigger asChild>
            <button type="button" className="w-full text-left outline-none">
              <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-1">
                    <Badge variant={statusVariant(monitor.status)} className="uppercase tracking-wider text-[10px]">{monitor.status}</Badge>
                    {isPlayingInProxy && <Badge variant="default" className="px-1.5 bg-indigo-500"><Play className="h-3 w-3 mr-1" /> Proxy</Badge>}
                  </div>
                  <p className="truncate text-base font-semibold text-slate-900 dark:text-slate-100">
                    {monitor.stream_name || monitor.content_id}
                  </p>
                  {monitor.stream_name && <p className="truncate text-xs font-mono text-muted-foreground mt-0.5">{monitor.content_id}</p>}
                  
                  <div className="mt-3 flex flex-wrap items-center gap-2">
                    <Badge variant={movementVariant(movement, monitor.status)}>{movementLabel(movement, monitor.status)}</Badge>
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground bg-white dark:bg-slate-900 px-2 py-0.5 rounded-full border">
                      <Users className="h-3 w-3 text-sky-500" /> {peers} peers
                    </div>
                    <div className="flex items-center gap-1.5 text-xs text-muted-foreground bg-white dark:bg-slate-900 px-2 py-0.5 rounded-full border">
                      <Gauge className="h-3 w-3 text-emerald-500" /> {formatBytesPerSecond((speedDown || 0) * 1024)}
                    </div>
                  </div>
                </div>

                <div className="flex sm:flex-col items-center sm:items-end justify-between sm:justify-start gap-2 w-full sm:w-auto mt-2 sm:mt-0">
                  <span className="text-xs text-muted-foreground">{formatAge(monitor.last_collected_at)}</span>
                  <div className="flex items-center justify-center h-8 w-8 rounded-full hover:bg-slate-200 dark:hover:bg-slate-800 transition-colors">
                    {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
                  </div>
                </div>
              </div>
            </button>
          </CollapsibleTrigger>
        </div>

        <CollapsibleContent>
          <div className="mt-4 pt-4 border-t border-slate-200 dark:border-slate-700/50">
            {monitor.status === 'dead' && (
              <div className="mb-4 rounded-md border border-rose-200 bg-rose-50 px-3 py-2 text-sm text-rose-700 dark:border-rose-900 dark:bg-rose-950/30">
                <span className="font-semibold mr-2">Reason:</span> {monitor.dead_reason || monitor.last_error || 'unknown'}
              </div>
            )}

            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4 mb-4">
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">Engine Node</p>
                <p className="text-sm font-mono text-slate-900 dark:text-slate-100">{engineShortId}</p>
              </div>
              <div className="space-y-1">
                <p className="text-xs text-muted-foreground">Up / Down Speed</p>
                <p className="text-sm font-medium">
                  <span className="text-rose-700 dark:text-rose-300">{formatBytesPerSecond((speedUp || 0) * 1024)}</span>
                  <span className="mx-1 text-slate-400 dark:text-slate-500">/</span>
                  <span className="text-emerald-700 dark:text-emerald-300">{formatBytesPerSecond((speedDown || 0) * 1024)}</span>
                </p>
              </div>
              <div className="space-y-1 lg:col-span-2">
                <p className="text-xs text-muted-foreground flex justify-between">
                  <span>Buffer Progress</span>
                  <span>{progress}%</span>
                </p>
              <div className="space-y-1 lg:col-span-2">
                <p className="text-xs text-muted-foreground flex justify-between">
                  <span>Download Progress</span>
                  <span>{progress}%</span>
                </p>
                <Progress className="h-2" value={Math.max(0, Math.min(100, Number(progress) || 0))} />
              </div>
            </div>

            {/* ENRICHED TELEMETRY ALIGNMENT */}
            <div className="grid gap-4 md:grid-cols-3">
              <div className="rounded-lg bg-white p-3 dark:bg-slate-900/40 border">
                <p className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2 flex justify-between items-center">
                  <span>Stream Sync</span>
                  {isPlayingInProxy && <span className="text-[9px] text-indigo-500 font-bold">Proxy Active</span>}
                </p>
                <BufferWindowBar livepos={livepos} runway={proxyRunwayMin} runwayMax={proxyRunwayMax} />
              </div>
              <div className="rounded-lg bg-white p-3 dark:bg-slate-900/40 border">
                <p className="text-[10px] uppercase tracking-wider text-emerald-600 dark:text-emerald-400 mb-1">Pos Trend (Proxy Read Head)</p>
                <Sparkline values={posSeries} color="#10b981" label="pos" />
              </div>
              <div className="rounded-lg bg-white p-3 dark:bg-slate-900/40 border">
                <p className="text-[10px] uppercase tracking-wider text-sky-600 dark:text-sky-400 mb-1">Swarm Edge Trend (last_ts)</p>
                <Sparkline values={lastTsSeries} color="#0ea5e9" label="last_ts" />
              </div>
            </div>

            <div className="mt-4 flex flex-wrap items-center gap-2 justify-end">
              <Button variant="secondary" size="sm" onClick={() => onStop(monitor.monitor_id)} disabled={isStopping || monitor.status === 'dead'}>
                <StopCircle className="mr-2 h-4 w-4" /> {isStopping ? 'Stopping...' : 'Stop Monitoring'}
              </Button>
              <Button variant="destructive" size="sm" onClick={() => onDelete(monitor.monitor_id)} disabled={isDeleting}>
                <Trash2 className="mr-2 h-4 w-4" /> {isDeleting ? 'Deleting...' : 'Delete Record'}
              </Button>
            </div>
          </div>
        </CollapsibleContent>
      </div>
    </Collapsible>
  )
}

// --- Main Page Component ---

export function StreamMonitoringPage({ orchUrl, apiKey, streams = [] }) {
  const uiPrefsStorageKey = 'stream-monitoring-ui-prefs-v2' // Bumped version for new schema
  const { resolvedTheme } = useTheme()
  const isDarkTheme = resolvedTheme === 'dark'
  
  // Data State
  const [monitors, setMonitors] = useState([])
  const [activeProxyKeys, setActiveProxyKeys] = useState(new Set())
  const [loading, setLoading] = useState(true)
  const [isLive, setIsLive] = useState(false)
  
  // UI Preferences State
  const [viewMode, setViewMode] = useState('cards') // 'cards' | 'table'
  const [sortMode, setSortMode] = useState('status_then_recent')
  const [statusFilter, setStatusFilter] = useState('all')
  const [isAddSheetOpen, setIsAddSheetOpen] = useState(false)
  
  // Selection & Expansion State
  const [expandedById, setExpandedById] = useState({})
  const [selectedById, setSelectedById] = useState({})
  
  // Action/Form State
  const [starting, setStarting] = useState(false)
  const [stoppingById, setStoppingById] = useState({})
  const [deletingById, setDeletingById] = useState({})
  
  // M3U Form State
  const [m3uFileName, setM3uFileName] = useState('')
  const [m3uContent, setM3uContent] = useState('')
  const [m3uEntries, setM3uEntries] = useState([])
  const [m3uSelectedById, setM3uSelectedById] = useState({})
  const [m3uParsing, setM3uParsing] = useState(false)
  const [m3uStarting, setM3uStarting] = useState(false)
  const [newMonitor, setNewMonitor] = useState({ content_id: '', live_delay: '', interval_s: '1.0', run_seconds: '0' })
  const normalizedApiKey = String(apiKey || '').trim()
  const authHeaders = useMemo(() => (normalizedApiKey ? { Authorization: `Bearer ${normalizedApiKey}` } : {}), [normalizedApiKey])
  const normalizedManualContentId = useMemo(() => normalizeMonitorContentId(newMonitor.content_id), [newMonitor.content_id])
  const canStartManualMonitor = Boolean(normalizedManualContentId) && !starting

  // --- Initialization & Prefs ---
  useEffect(() => {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(uiPrefsStorageKey) || '{}')
      if (typeof parsed.viewMode === 'string' && ALLOWED_VIEW_MODES.has(parsed.viewMode)) {
        setViewMode(parsed.viewMode)
      }
      if (typeof parsed.sortMode === 'string' && ALLOWED_SORT_MODES.has(parsed.sortMode)) {
        setSortMode(parsed.sortMode)
      }
      if (typeof parsed.statusFilter === 'string' && ALLOWED_STATUS_FILTERS.has(parsed.statusFilter)) {
        setStatusFilter(parsed.statusFilter)
      }
    } catch { /* ignored */ }
  }, [])

  useEffect(() => {
    try {
      window.localStorage.setItem(uiPrefsStorageKey, JSON.stringify({ viewMode, sortMode, statusFilter }))
    } catch { /* ignored */ }
  }, [viewMode, sortMode, statusFilter])

  useEffect(() => {
    setActiveProxyKeys(new Set((Array.isArray(streams) ? streams : []).map((s) => normalizeContentRef(s?.key)).filter(Boolean)))
  }, [streams])

  // --- API / SSE ---
  const fetchMonitorsNow = useCallback(async (showLoading = false) => {
    if (showLoading) setLoading(true)
    try {
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy`, { headers: authHeaders })
      if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
      const payload = await response.json()
      setMonitors(Array.isArray(payload?.items) ? payload.items : [])
    } catch (err) {
      toast.error('Failed to fetch sessions', { description: err.message })
    } finally {
      setLoading(false)
    }
  }, [orchUrl, authHeaders])

  useEffect(() => {
    let eventSource = null, reconnectTimer = null, fallbackInterval = null, closed = false

    const connect = () => {
      if (closed) return
      if (typeof window === 'undefined' || !window.EventSource) {
        fetchMonitorsNow(true)
        fallbackInterval = window.setInterval(() => fetchMonitorsNow(false), 2000)
        return
      }

      const streamUrl = new URL(`${orchUrl}/api/v1/ace/monitor/legacy/stream`)
      streamUrl.searchParams.set('include_recent_status', 'true')
      if (normalizedApiKey) {
        streamUrl.searchParams.set('api_key', normalizedApiKey)
      }
      eventSource = new EventSource(streamUrl.toString())

      eventSource.onopen = () => setIsLive(true)

      const handleMonitorPayload = (parsed) => {
        if (parsed?.type === 'legacy_monitor_snapshot') {
          setMonitors(parsed?.payload?.items || [])
          setLoading(false)
          return
        }

        if (parsed?.type === 'legacy_monitor_event') {
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
          setLoading(false)
        }
      }

      const handleSseEvent = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          handleMonitorPayload(parsed)
        } catch { /* ignored */ }
      }

      // Backend emits named events; keep onmessage for compatibility with unnamed SSE payloads.
      eventSource.addEventListener('legacy_monitor_snapshot', handleSseEvent)
      eventSource.addEventListener('legacy_monitor_event', handleSseEvent)
      eventSource.onmessage = handleSseEvent

      eventSource.onerror = () => {
        setIsLive(false)
        if (eventSource) { eventSource.close(); eventSource = null }
        fetchMonitorsNow(false)
        if (!closed) reconnectTimer = window.setTimeout(connect, 2000)
      }
    }
    setLoading(true); connect()

    return () => {
      closed = true
      clearTimeout(reconnectTimer); clearInterval(fallbackInterval); if (eventSource) eventSource.close()
    }
  }, [orchUrl, normalizedApiKey, fetchMonitorsNow])

  // --- Handlers ---
  const handleStartMonitor = async () => {
    const normalizedContentId = normalizeMonitorContentId(newMonitor.content_id)
    if (!normalizedContentId) {
      toast.error('Missing Information', { description: 'Content ID is required' })
      return 
    }
    setStarting(true)
    try {
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({
          ...newMonitor,
          content_id: normalizedContentId,
          stream_name: null,
          live_delay: Number(newMonitor.live_delay || 0),
          interval_s: Number(newMonitor.interval_s || 1),
          run_seconds: Number(newMonitor.run_seconds || 0)
        }),
      })
      if (!response.ok) throw new Error(await response.json().then(p=>p.detail).catch(()=>'Failed'))
      toast.success('Session Started', { description: `Monitoring ${normalizedContentId.slice(0, 8)}...` })
      setNewMonitor((prev) => ({ ...prev, content_id: '' }))
      setIsAddSheetOpen(false)
      fetchMonitorsNow(false)
    } catch (err) { 
      toast.error('Failed to start', { description: err.message })
    } finally { 
      setStarting(false) 
    }
  }

  const handleAction = async (monitorId, actionType) => {
    const isStop = actionType === 'stop'
    const setActionState = isStop ? setStoppingById : setDeletingById
    const endpoint = isStop ? '' : '/entry'

    setActionState((prev) => ({ ...prev, [monitorId]: true }))
    try {
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/${encodeURIComponent(monitorId)}${endpoint}`, {
        method: 'DELETE', headers: authHeaders,
      })
      if (!response.ok) throw new Error('Action failed')
      toast.success(`Session ${isStop ? 'Stopped' : 'Deleted'}`)
      fetchMonitorsNow(false)
    } catch (err) { 
      toast.error(`Failed to ${actionType}`, { description: err.message })
    } finally { 
      setActionState((prev) => ({ ...prev, [monitorId]: false })) 
    }
  }

  const handleM3uFilePicked = async (e) => {
    const file = e.target.files?.[0]; if (!file) return
    try {
      setM3uFileName(file.name || 'playlist.m3u'); setM3uContent(await file.text())
      setM3uEntries([]); setM3uSelectedById({})
    } catch { 
      toast.error('File Error', { description: 'Failed to read the selected file.' })
    }
  }

  const handleParseM3u = async () => {
    setM3uParsing(true)
    try {
      const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/parse-m3u`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders },
        body: JSON.stringify({ m3u_content: m3uContent }),
      })
      if (!response.ok) throw new Error('Failed to parse')
      const items = (await response.json())?.items || []
      setM3uEntries(items)
      const initialSel = {}; items.forEach(i => i.content_id && (initialSel[i.content_id] = true))
      setM3uSelectedById(initialSel)
      toast.success('Playlist Parsed', { description: `Found ${items.length} AceStream links.` })
    } catch (err) { 
      toast.error('Parse Error', { description: err.message }) 
    } finally { 
      setM3uParsing(false) 
    }
  }

  const handleStartSelectedM3u = async () => {
    const selectedEntries = m3uEntries
      .filter((entry) => Boolean(m3uSelectedById[entry.content_id]))
      .map((entry) => ({ ...entry, content_id: normalizeMonitorContentId(entry.content_id) }))
      .filter((entry) => Boolean(entry.content_id))

    if (selectedEntries.length === 0) {
      toast.error('No streams selected', { description: 'Select at least one valid stream entry.' })
      return
    }

    setM3uStarting(true)
    try {
      const startRequests = selectedEntries.map(async (entry) => {
        const response = await fetch(`${orchUrl}/api/v1/ace/monitor/legacy/start`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders },
          body: JSON.stringify({
            content_id: entry.content_id,
            stream_name: entry.name || null,
            live_delay: Number(newMonitor.live_delay || 0),
            interval_s: Number(newMonitor.interval_s || 1),
            run_seconds: Number(newMonitor.run_seconds || 0)
          }),
        })

        if (!response.ok) {
          const detail = await response.json().then((p) => p?.detail || 'Failed').catch(() => 'Failed')
          throw new Error(`${entry.content_id}: ${detail}`)
        }
        return entry.content_id
      })

      const settled = await Promise.allSettled(startRequests)
      const successCount = settled.filter((r) => r.status === 'fulfilled').length
      const failed = settled
        .filter((r) => r.status === 'rejected')
        .map((r) => r.reason?.message || 'Unknown error')

      if (successCount > 0) {
        toast.success('Sessions Started', { description: `${successCount} stream${successCount === 1 ? '' : 's'} started.` })
        fetchMonitorsNow(false)
      }

      if (failed.length > 0) {
        toast.error('Some streams failed', { description: failed.slice(0, 2).join(' | ') })
      }

      if (failed.length === 0) {
        setIsAddSheetOpen(false)
      }
    } finally {
      setM3uStarting(false)
    }
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

  const { sortedMonitors, stats } = useMemo(() => {
    let filtered = parsedMonitors
    if (statusFilter !== 'all') {
      filtered = parsedMonitors.filter((m) => (statusFilter === 'active' ? ACTIVE_MONITOR_STATUSES.includes(m.status) : m.status === statusFilter))
    }

    const sorted = [...filtered].sort((a, b) => {
      const [aRank, bRank] = [statusRank[a.status] ?? 99, statusRank[b.status] ?? 99]
      if (sortMode === 'recent') return b._derived.lastCollectedAtMs - a._derived.lastCollectedAtMs
      if (sortMode === 'speed') return b._derived.speedDownKbps - a._derived.speedDownKbps
      if (sortMode === 'progress') return b._derived.progressValue - a._derived.progressValue
      if (sortMode === 'content') return (a.content_id || '').localeCompare(b.content_id || '')
      return aRank !== bRank ? aRank - bRank : b._derived.lastCollectedAtMs - a._derived.lastCollectedAtMs
    })

    return {
      sortedMonitors: sorted,
      stats: {
        active: parsedMonitors.filter((m) => ACTIVE_MONITOR_STATUSES.includes(m.status)).length,
        running: parsedMonitors.filter(m => m.status === 'running').length,
        dead: parsedMonitors.filter(m => m.status === 'dead').length,
        stuck: parsedMonitors.filter(m => m.status === 'stuck').length,
        totalDl: parsedMonitors.reduce((acc, m) => acc + m._derived.speedDownKbps, 0)
      }
    }
  }, [parsedMonitors, statusFilter, sortMode])

  return (
    <div className="space-y-8">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <div className="flex items-center gap-3">
            <h1 className="text-3xl font-bold tracking-tight">Stream Monitoring</h1>
            <Badge variant={isLive ? 'success' : 'secondary'} className="h-6">
              {isLive && <span className="mr-1.5 h-1.5 w-1.5 rounded-full bg-emerald-500 animate-pulse" />}
              {isLive ? 'Live' : 'Offline'}
            </Badge>
          </div>
          <p className="text-muted-foreground mt-1 text-sm">Telemetry, buffer analysis, and active connections.</p>
        </div>
        <Button onClick={() => setIsAddSheetOpen(true)} className="shrink-0 shadow-sm">
          <PlusCircle className="mr-2 h-4 w-4" /> Add Monitor
        </Button>
      </div>

      {/* KPI Stats Overview */}
      <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
        <Card className="shadow-sm">
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">Active / Running</p>
              <p className="text-2xl font-bold text-slate-900 dark:text-white">
                {stats.active} <span className="text-muted-foreground text-lg font-normal">/ {stats.running}</span>
              </p>
            </div>
            <div className="h-10 w-10 rounded-full bg-emerald-100 dark:bg-emerald-900/30 flex items-center justify-center">
              <Play className="h-5 w-5 text-emerald-600 dark:text-emerald-400" />
            </div>
          </CardContent>
        </Card>
        
        <Card className="shadow-sm">
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">Stuck</p>
              <p className="text-2xl font-bold text-amber-600 dark:text-amber-400">{stats.stuck}</p>
            </div>
            <div className="h-10 w-10 rounded-full bg-amber-100 dark:bg-amber-900/30 flex items-center justify-center">
              <Activity className="h-5 w-5 text-amber-600 dark:text-amber-400" />
            </div>
          </CardContent>
        </Card>
        
        <Card className="shadow-sm">
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">Dead</p>
              <p className="text-2xl font-bold text-rose-600 dark:text-rose-400">{stats.dead}</p>
            </div>
            <div className="h-10 w-10 rounded-full bg-rose-100 dark:bg-rose-900/30 flex items-center justify-center">
              <Trash2 className="h-5 w-5 text-rose-600 dark:text-rose-400" />
            </div>
          </CardContent>
        </Card>

        <Card className="shadow-sm">
          <CardContent className="p-4 flex items-center justify-between">
            <div>
              <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider mb-1">Total Downlink</p>
              <p className="text-2xl font-bold text-sky-600 dark:text-sky-400">{formatBytesPerSecond(stats.totalDl * 1024)}</p>
            </div>
            <div className="h-10 w-10 rounded-full bg-sky-100 dark:bg-sky-900/30 flex items-center justify-center">
              <Gauge className="h-5 w-5 text-sky-600 dark:text-sky-400" />
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Main List Area */}
      <div className="flex flex-col gap-4">
        {/* Toolbar */}
        <div className="flex flex-col sm:flex-row justify-between items-start sm:items-center gap-4 bg-white dark:bg-slate-900/50 p-3 rounded-lg border shadow-sm">
          <div className="flex items-center gap-2">
            <span className="text-sm font-medium text-slate-700 dark:text-slate-300 ml-1">Sessions</span>
            <Badge variant="secondary" className="font-mono">{sortedMonitors.length}</Badge>
          </div>
          
          <div className="flex flex-wrap items-center gap-2">
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="sm" className="h-8 shadow-none text-foreground bg-background">
                  <Filter className="mr-2 h-3.5 w-3.5" />
                  {statusFilter === 'all' ? 'All Status' : statusFilter.charAt(0).toUpperCase() + statusFilter.slice(1)}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className={`w-40 text-foreground ${isDarkTheme ? 'dark' : ''}`}>
                <DropdownMenuRadioGroup value={statusFilter} onValueChange={setStatusFilter}>
                  <DropdownMenuRadioItem value="all">All Sessions</DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="active">Active Only</DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="running">Running</DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="stuck">Stuck</DropdownMenuRadioItem>
                  <DropdownMenuRadioItem value="dead">Dead</DropdownMenuRadioItem>
                </DropdownMenuRadioGroup>
              </DropdownMenuContent>
            </DropdownMenu>

            <Select value={sortMode} onValueChange={setSortMode}>
              <SelectTrigger className="h-8 w-[140px] text-xs shadow-none text-foreground bg-background"><SelectValue placeholder="Sort by" /></SelectTrigger>
              <SelectContent className={`text-foreground ${isDarkTheme ? 'dark' : ''}`}>
                <SelectItem value="status_then_recent">Status Rank</SelectItem>
                <SelectItem value="recent">Most Recent</SelectItem>
                <SelectItem value="speed">Highest Speed</SelectItem>
                <SelectItem value="progress">Most Progress</SelectItem>
              </SelectContent>
            </Select>

            <div className="flex items-center rounded-md border p-0.5 ml-1">
              <Button 
                variant={viewMode === 'cards' ? 'secondary' : 'ghost'} 
                size="icon" 
                className="h-7 w-7" 
                onClick={() => setViewMode('cards')}
                title="Card View"
              >
                <LayoutGrid className="h-4 w-4" />
              </Button>
              <Button 
                variant={viewMode === 'table' ? 'secondary' : 'ghost'} 
                size="icon" 
                className="h-7 w-7" 
                onClick={() => setViewMode('table')}
                title="Table View"
              >
                <List className="h-4 w-4" />
              </Button>
            </div>
          </div>
        </div>

        {/* Content Area */}
        {loading && sortedMonitors.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
            <Activity className="h-8 w-8 mb-4 animate-pulse" />
            <p>Loading sessions...</p>
          </div>
        ) : sortedMonitors.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-muted-foreground border rounded-lg border-dashed bg-slate-50/50 dark:bg-slate-900/20">
            <Radio className="h-10 w-10 mb-4 opacity-20" />
            <p className="text-lg font-medium text-slate-600 dark:text-slate-400">No monitoring sessions found</p>
            <p className="text-sm mt-1">Adjust your filters or add a new stream to begin.</p>
            <Button variant="outline" className="mt-6" onClick={() => setIsAddSheetOpen(true)}>
              <PlusCircle className="mr-2 h-4 w-4" /> Add Session
            </Button>
          </div>
        ) : viewMode === 'table' ? (
          <div className="rounded-md border bg-white dark:bg-slate-950 shadow-sm overflow-hidden">
            <Table>
              <TableHeader className="bg-slate-50 dark:bg-slate-900/50">
                <TableRow>
                  <TableHead className="w-[40px]"></TableHead>
                  <TableHead>Stream</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Peers</TableHead>
                  <TableHead>Speed</TableHead>
                  <TableHead>Progress</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedMonitors.map((m) => (
                  <TableRow key={m.monitor_id} className="group">
                    <TableCell>
                      <Checkbox checked={Boolean(selectedById[m.monitor_id])} onCheckedChange={(c) => setSelectedById(p => ({ ...p, [m.monitor_id]: Boolean(c) }))} />
                    </TableCell>
                    <TableCell>
                      <div className="font-medium text-slate-900 dark:text-slate-100 max-w-[200px] truncate" title={m.stream_name || m.content_id}>
                        {m.stream_name || m.content_id}
                      </div>
                      {m.stream_name && <div className="text-xs text-muted-foreground font-mono truncate max-w-[200px]">{m.content_id}</div>}
                    </TableCell>
                    <TableCell>
                      <Badge variant={statusVariant(m.status)} className="text-[10px] uppercase">{m.status}</Badge>
                    </TableCell>
                    <TableCell className="text-muted-foreground">{m._derived.peers}</TableCell>
                    <TableCell className="font-mono text-xs">{formatBytesPerSecond(m._derived.speedDownKbps * 1024)}</TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2 max-w-[100px]">
                        <span className="text-xs w-8">{m._derived.progressValue}%</span>
                        <Progress value={m._derived.progressValue} className="h-1.5" />
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex items-center justify-end gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <Button variant="ghost" size="icon" className="h-8 w-8 text-muted-foreground hover:text-slate-900" onClick={() => handleAction(m.monitor_id, 'stop')} disabled={stoppingById[m.monitor_id] || m.status === 'dead'} title="Stop">
                          <StopCircle className="h-4 w-4" />
                        </Button>
                        <Button variant="ghost" size="icon" className="h-8 w-8 text-rose-500 hover:text-rose-600 hover:bg-rose-50 dark:hover:bg-rose-950" onClick={() => handleAction(m.monitor_id, 'delete')} disabled={deletingById[m.monitor_id]} title="Delete">
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        ) : (
          <div className="grid gap-4 md:grid-cols-2">
            {sortedMonitors.map((monitor) => (
              <MonitorCard 
                key={monitor.monitor_id}
                monitor={monitor}
                isExpanded={Boolean(expandedById[monitor.monitor_id])}
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
        )}
      </div>

      {/* Slide-out Sheet for "Add Session" */}
      <Sheet open={isAddSheetOpen} onOpenChange={setIsAddSheetOpen}>
        <SheetContent side="right" className={`w-full sm:max-w-lg overflow-y-auto p-0 border-l bg-background text-foreground ${isDarkTheme ? 'dark' : ''}`}>
          <div className="p-6 bg-background border-b border-border sticky top-0 z-10">
            <SheetHeader>
              <SheetTitle>Add Monitoring Session</SheetTitle>
              <SheetDescription>
                Manually track stream telemetry or extract links from an M3U playlist.
              </SheetDescription>
            </SheetHeader>
          </div>
          
          <div className="p-6">
            <Tabs defaultValue="manual" className="w-full">
              <TabsList className="grid w-full grid-cols-2 mb-6">
                <TabsTrigger value="manual"><PlusCircle className="mr-2 h-4 w-4" /> Manual</TabsTrigger>
                <TabsTrigger value="m3u"><ListVideo className="mr-2 h-4 w-4" /> M3U Import</TabsTrigger>
              </TabsList>
              
              <TabsContent value="manual" className="space-y-6 mt-0">
                <div className="space-y-4">
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Content ID / Infohash</label>
                    <Input className="bg-white text-slate-900 border-slate-300 placeholder:text-slate-500 dark:bg-slate-900 dark:text-slate-100 dark:border-slate-700" placeholder="acestream://..." value={newMonitor.content_id} onChange={(e) => setNewMonitor(p => ({ ...p, content_id: e.target.value }))} />
                  </div>
                  
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-muted-foreground flex justify-between">Live Delay <span>(s)</span></label>
                      <Input className="bg-white text-slate-900 border-slate-300 placeholder:text-slate-500 dark:bg-slate-900 dark:text-slate-100 dark:border-slate-700" type="number" min="0" placeholder="0" value={newMonitor.live_delay} onChange={(e) => setNewMonitor(p => ({ ...p, live_delay: e.target.value }))} />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium text-muted-foreground flex justify-between">Interval <span>(s)</span></label>
                      <Input className="bg-white text-slate-900 border-slate-300 placeholder:text-slate-500 dark:bg-slate-900 dark:text-slate-100 dark:border-slate-700" type="number" min="0.5" step="0.5" placeholder="1.0" value={newMonitor.interval_s} onChange={(e) => setNewMonitor(p => ({ ...p, interval_s: e.target.value }))} />
                    </div>
                  </div>
                  
                  <div className="space-y-2">
                    <label className="text-sm font-medium text-muted-foreground flex justify-between">Run Duration <span>(s)</span></label>
                    <Input className="bg-white text-slate-900 border-slate-300 placeholder:text-slate-500 dark:bg-slate-900 dark:text-slate-100 dark:border-slate-700" type="number" min="0" placeholder="0 (Continuous)" value={newMonitor.run_seconds} onChange={(e) => setNewMonitor(p => ({ ...p, run_seconds: e.target.value }))} />
                  </div>
                </div>

                <div className="pt-4 border-t">
                  <Button type="button" onClick={() => { if (!canStartManualMonitor) return; handleStartMonitor() }} disabled={!canStartManualMonitor} aria-disabled={!canStartManualMonitor} className="w-full bg-emerald-600 text-white hover:bg-emerald-500 dark:bg-emerald-500 dark:text-white dark:hover:bg-emerald-400 disabled:cursor-not-allowed disabled:bg-slate-500 disabled:text-white dark:disabled:bg-slate-700">
                    <PlayCircle className="mr-2 h-4 w-4" /> {starting ? 'Starting...' : 'Start Monitoring'}
                  </Button>
                </div>
              </TabsContent>

              <TabsContent value="m3u" className="space-y-6 mt-0">
                <div className="space-y-4">
                  <div className="rounded-lg border-2 border-dashed border-slate-300 dark:border-slate-700 p-6 flex flex-col items-center justify-center text-center hover:bg-slate-50 dark:hover:bg-slate-900/50 transition-colors">
                    <ListVideo className="h-8 w-8 text-muted-foreground mb-3" />
                    <p className="text-sm font-medium mb-1">Upload Playlist</p>
                    <p className="text-xs text-muted-foreground mb-4">.m3u, .m3u8, or .txt</p>
                    <Input type="file" accept=".m3u,.m3u8,text/plain" onChange={handleM3uFilePicked} className="max-w-[250px]" />
                  </div>

                  <Button variant="secondary" className="w-full bg-slate-800 text-white hover:bg-slate-700 dark:bg-slate-200 dark:text-slate-900 dark:hover:bg-white disabled:bg-slate-500 disabled:text-white" onClick={handleParseM3u} disabled={m3uParsing || !m3uContent}>
                    {m3uParsing ? 'Parsing File...' : 'Extract AceStream Links'}
                  </Button>
                </div>

                {m3uEntries.length > 0 && (
                  <div className="pt-6 border-t space-y-4">
                    <div className="flex items-center justify-between">
                      <span className="text-sm font-medium">Select Streams ({Object.values(m3uSelectedById).filter(Boolean).length}/{m3uEntries.length})</span>
                      <div className="flex items-center gap-2">
                        <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => { const n = {}; m3uEntries.forEach(e => n[e.content_id] = true); setM3uSelectedById(n) }}>All</Button>
                        <Button size="sm" variant="ghost" className="h-7 px-2 text-xs" onClick={() => setM3uSelectedById({})}>None</Button>
                      </div>
                    </div>
                    
                    <div className="max-h-[300px] overflow-y-auto space-y-2 pr-2 border rounded-md p-2 bg-white dark:bg-slate-900">
                      {m3uEntries.map(entry => (
                        <div key={entry.content_id} className="flex items-start gap-3 rounded p-2 hover:bg-slate-50 dark:hover:bg-slate-800/50">
                          <Checkbox className="mt-1" checked={Boolean(m3uSelectedById[entry.content_id])} onCheckedChange={(c) => setM3uSelectedById(p => ({ ...p, [entry.content_id]: Boolean(c) }))} />
                          <div className="min-w-0 flex-1">
                            <p className="text-sm font-medium truncate" title={entry.name}>{entry.name || 'Unnamed stream'}</p>
                            <p className="text-xs text-muted-foreground font-mono truncate" title={entry.content_id}>{entry.content_id}</p>
                          </div>
                        </div>
                      ))}
                    </div>

                    <Button className="w-full bg-emerald-600 text-white hover:bg-emerald-500 dark:bg-emerald-500 dark:text-white dark:hover:bg-emerald-400 disabled:bg-slate-500 disabled:text-white" onClick={handleStartSelectedM3u} disabled={m3uStarting || Object.values(m3uSelectedById).filter(Boolean).length === 0}>
                      <PlayCircle className="mr-2 h-4 w-4" /> Start Selected ({Object.values(m3uSelectedById).filter(Boolean).length})
                    </Button>
                  </div>
                )}
              </TabsContent>
            </Tabs>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  )
}