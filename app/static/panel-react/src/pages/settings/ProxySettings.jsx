import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { AnimatePresence, motion } from 'framer-motion'
import { AlertCircle, EyeOff, FlaskConical, Loader2 } from 'lucide-react'
import { getLifecycleCopy, InteractiveStreamLifecycle } from '@/components/settings/InteractiveStreamLifecycle'
import { SettingRow } from '@/components/settings/SettingRow'
import { useSettingsForm } from '@/context/SettingsFormContext'

const DEFAULTS = {
  initial_data_wait_timeout: 10,
  initial_data_check_interval: 0.2,
  no_data_timeout_checks: 60,
  no_data_check_interval: 1,
  connection_timeout: 30,
  upstream_connect_timeout: 3,
  upstream_read_timeout: 90,
  stream_timeout: 60,
  channel_shutdown_delay: 5,
  proxy_prebuffer_seconds: 0,
  ace_live_edge_delay: 0,
  max_streams_per_engine: 3,
  stream_mode: 'TS',
  control_mode: 'api',
  hls_max_segments: 20,
  hls_initial_segments: 3,
  hls_window_size: 6,
  hls_buffer_ready_timeout: 30,
  hls_first_segment_timeout: 30,
  hls_initial_buffer_seconds: 10,
  hls_max_initial_segments: 10,
  hls_segment_fetch_interval: 0.5,
}

const PREFLIGHT_INPUT_OPTIONS = {
  content_id: { label: 'Content ID', param: 'id', placeholder: 'PID or acestream content_id' },
  infohash: { label: 'Infohash', param: 'infohash', placeholder: '40-char infohash' },
  torrent_url: { label: 'Torrent URL', param: 'torrent_url', placeholder: 'https://example.com/file.torrent' },
  direct_url: { label: 'Direct URL', param: 'direct_url', placeholder: 'magnet:?xt=... or https://media.example/stream' },
  raw_data: { label: 'Raw Torrent Data', param: 'raw_data', placeholder: 'Base64/raw torrent payload' },
}

const LIFECYCLE_HELPER_HIDDEN_KEY = 'proxy.settings.lifecycleHelper.hidden'

const toNumber = (value, fallback = 0) => {
  const next = Number(value)
  return Number.isFinite(next) ? next : fallback
}

const normalizeControlMode = (value) => {
  const normalized = String(value || '').trim().toLowerCase()
  if (normalized === 'legacy_api' || normalized === 'api') return 'api'
  return 'http'
}

const extractLoadRespFiles = (payload) => {
  const files = payload?.result?.loadresp?.files
  if (!Array.isArray(files)) return []

  return files.map((entry, idx) => {
    const label = typeof entry === 'string' ? entry : (entry?.filename || entry?.name || `File ${idx}`)
    const indexRaw = entry?.index ?? entry?.file_index ?? idx
    const index = Number.isFinite(Number(indexRaw)) ? Number(indexRaw) : idx
    return { index, label }
  })
}

export function ProxySettings({ apiKey, orchUrl, authRequired }) {
  const sectionId = 'proxy'
  const { registerSection, unregisterSection, setSectionDirty, setSectionSaving } = useSettingsForm()

  const [loading, setLoading] = useState(true)
  const [initialState, setInitialState] = useState(DEFAULTS)
  const [draft, setDraft] = useState(DEFAULTS)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [activePhase, setActivePhase] = useState(null)
  const lifecycleHideTimeoutRef = useRef(null)
  const lifecycleWindowHoveredRef = useRef(false)
  const [lifecycleHidden, setLifecycleHidden] = useState(() => {
    if (typeof window === 'undefined') return false
    try {
      return window.localStorage.getItem(LIFECYCLE_HELPER_HIDDEN_KEY) === '1'
    } catch {
      return false
    }
  })

  const [diagOpen, setDiagOpen] = useState(false)
  const [diagType, setDiagType] = useState('content_id')
  const [diagInput, setDiagInput] = useState('')
  const [diagFileIndexes, setDiagFileIndexes] = useState('0')
  const [diagTier, setDiagTier] = useState('light')
  const [diagRunning, setDiagRunning] = useState(false)
  const [diagError, setDiagError] = useState('')
  const [diagResult, setDiagResult] = useState(null)
  const [showDiagRaw, setShowDiagRaw] = useState(false)

  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(initialState),
    [draft, initialState],
  )

  const preflightMetrics = useMemo(() => {
    const probe = diagResult?.result?.status_probe
    if (!probe) return null

    const livepos = probe.livepos
    let runway = null
    if (livepos?.pos && (livepos?.last_ts || livepos?.live_last)) {
      const pos = Number(livepos.pos)
      const last = Number(livepos.last_ts || livepos.live_last)
      if (Number.isFinite(pos) && Number.isFinite(last)) {
        runway = Math.max(0, last - pos)
      }
    }

    return {
      status: probe.status_text || probe.status || 'N/A',
      peers: probe.peers ?? 0,
      httpPeers: probe.http_peers ?? 0,
      speed: probe.speed_down ?? 0,
      runway,
      checks: diagResult?.result?.availability_checks || {},
    }
  }, [diagResult])

  const preflightFiles = useMemo(() => extractLoadRespFiles(diagResult), [diagResult])

  useEffect(() => {
    const fetchConfig = async () => {
      setLoading(true)
      setError('')
      try {
        let payload = null

        const consolidated = await fetch(`${orchUrl}/api/v1/settings`)
        if (consolidated.ok) {
          const settingsBundle = await consolidated.json().catch(() => ({}))
          payload = settingsBundle?.proxy_settings || null
        }

        if (!payload) {
          const response = await fetch(`${orchUrl}/api/v1/proxy/config`)
          if (!response.ok) throw new Error(`HTTP ${response.status}`)
          payload = await response.json()
        }

        const normalized = {
          ...DEFAULTS,
          ...payload,
          ace_live_edge_delay: toNumber(payload?.ace_live_edge_delay, DEFAULTS.ace_live_edge_delay),
          control_mode: normalizeControlMode(payload?.control_mode),
        }
        setInitialState(normalized)
        setDraft(normalized)
        setSectionDirty(sectionId, false)
      } catch (fetchError) {
        setError(`Failed to load proxy settings: ${fetchError.message || String(fetchError)}`)
      } finally {
        setLoading(false)
      }
    }

    fetchConfig()
  }, [orchUrl])

  useEffect(() => {
    const save = async () => {
      if (authRequired && !String(apiKey || '').trim()) {
        throw new Error('API key required by server for proxy settings updates')
      }

      setSectionSaving(sectionId, true)
      setError('')
      setMessage('')

      try {
        const headers = {}
        if (String(apiKey || '').trim()) {
          headers.Authorization = `Bearer ${String(apiKey).trim()}`
        }

        const params = new URLSearchParams()
        params.set('initial_data_wait_timeout', String(toNumber(draft.initial_data_wait_timeout, DEFAULTS.initial_data_wait_timeout)))
        params.set('initial_data_check_interval', String(toNumber(draft.initial_data_check_interval, DEFAULTS.initial_data_check_interval)))
        params.set('no_data_timeout_checks', String(toNumber(draft.no_data_timeout_checks, DEFAULTS.no_data_timeout_checks)))
        params.set('no_data_check_interval', String(toNumber(draft.no_data_check_interval, DEFAULTS.no_data_check_interval)))
        params.set('connection_timeout', String(toNumber(draft.connection_timeout, DEFAULTS.connection_timeout)))
        params.set('upstream_connect_timeout', String(toNumber(draft.upstream_connect_timeout, DEFAULTS.upstream_connect_timeout)))
        params.set('upstream_read_timeout', String(toNumber(draft.upstream_read_timeout, DEFAULTS.upstream_read_timeout)))
        params.set('stream_timeout', String(toNumber(draft.stream_timeout, DEFAULTS.stream_timeout)))
        params.set('channel_shutdown_delay', String(toNumber(draft.channel_shutdown_delay, DEFAULTS.channel_shutdown_delay)))
        params.set('proxy_prebuffer_seconds', String(Math.max(0, toNumber(draft.proxy_prebuffer_seconds, DEFAULTS.proxy_prebuffer_seconds))))
        params.set('ace_live_edge_delay', String(toNumber(draft.ace_live_edge_delay, DEFAULTS.ace_live_edge_delay)))
        params.set('max_streams_per_engine', String(toNumber(draft.max_streams_per_engine, DEFAULTS.max_streams_per_engine)))
        params.set('stream_mode', String(draft.stream_mode || DEFAULTS.stream_mode))
        params.set('control_mode', String(normalizeControlMode(draft.control_mode)))
        params.set('hls_max_segments', String(toNumber(draft.hls_max_segments, DEFAULTS.hls_max_segments)))
        params.set('hls_initial_segments', String(toNumber(draft.hls_initial_segments, DEFAULTS.hls_initial_segments)))
        params.set('hls_window_size', String(toNumber(draft.hls_window_size, DEFAULTS.hls_window_size)))
        params.set('hls_buffer_ready_timeout', String(toNumber(draft.hls_buffer_ready_timeout, DEFAULTS.hls_buffer_ready_timeout)))
        params.set('hls_first_segment_timeout', String(toNumber(draft.hls_first_segment_timeout, DEFAULTS.hls_first_segment_timeout)))
        params.set('hls_initial_buffer_seconds', String(toNumber(draft.hls_initial_buffer_seconds, DEFAULTS.hls_initial_buffer_seconds)))
        params.set('hls_max_initial_segments', String(toNumber(draft.hls_max_initial_segments, DEFAULTS.hls_max_initial_segments)))
        params.set('hls_segment_fetch_interval', String(toNumber(draft.hls_segment_fetch_interval, DEFAULTS.hls_segment_fetch_interval)))

        const response = await fetch(`${orchUrl}/api/v1/proxy/config?${params.toString()}`, {
          method: 'POST',
          headers,
        })

        if (!response.ok) {
          const failure = await response.json().catch(() => ({}))
          throw new Error(failure?.detail || `HTTP ${response.status}`)
        }

        const payload = await response.json().catch(() => ({}))
        setInitialState({ ...draft })
        setSectionDirty(sectionId, false)
        setMessage(payload?.message || 'Proxy settings saved')
      } finally {
        setSectionSaving(sectionId, false)
      }
    }

    const discard = () => {
      setDraft(initialState)
      setSectionDirty(sectionId, false)
      setError('')
      setMessage('')
    }

    registerSection(sectionId, {
      title: 'Proxy',
      requiresAuth: true,
      save,
      discard,
    })

    return () => unregisterSection(sectionId)
  }, [
    apiKey,
    authRequired,
    draft,
    initialState,
    orchUrl,
    registerSection,
    setSectionDirty,
    setSectionSaving,
    unregisterSection,
  ])

  useEffect(() => {
    setSectionDirty(sectionId, dirty)
  }, [dirty, setSectionDirty])

  useEffect(() => {
    try {
      window.localStorage.setItem(LIFECYCLE_HELPER_HIDDEN_KEY, lifecycleHidden ? '1' : '0')
    } catch {
      // Ignore storage failures in constrained browser contexts.
    }
  }, [lifecycleHidden])

  useEffect(() => {
    if (lifecycleHidden) {
      setActivePhase(null)
    }
  }, [lifecycleHidden])

  useEffect(() => {
    return () => {
      if (lifecycleHideTimeoutRef.current) {
        window.clearTimeout(lifecycleHideTimeoutRef.current)
      }
    }
  }, [])

  const update = (field, value) => {
    setDraft((prev) => ({ ...prev, [field]: value }))
    setError('')
    setMessage('')
  }

  const isLifecycleWindowTarget = (target) => {
    if (!(target instanceof Element)) return false
    return Boolean(target.closest('[data-lifecycle-window="true"]'))
  }

  const isLifecycleFieldFocused = () => {
    if (typeof document === 'undefined') return false
    return Boolean(document.activeElement?.getAttribute('data-lifecycle-phase'))
  }

  const clearLifecycleHideTimeout = () => {
    if (!lifecycleHideTimeoutRef.current) return
    window.clearTimeout(lifecycleHideTimeoutRef.current)
    lifecycleHideTimeoutRef.current = null
  }

  const scheduleLifecycleHide = () => {
    clearLifecycleHideTimeout()
    lifecycleHideTimeoutRef.current = window.setTimeout(() => {
      if (lifecycleWindowHoveredRef.current) return
      if (isLifecycleFieldFocused()) return
      setActivePhase(null)
    }, 220)
  }

  const bindPhase = (phase) => ({
    'data-lifecycle-phase': phase,
    onFocus: () => {
      clearLifecycleHideTimeout()
      if (!lifecycleHidden) setActivePhase(phase)
    },
    onBlur: (event) => {
      if (isLifecycleWindowTarget(event.relatedTarget)) return
      scheduleLifecycleHide()
    },
    onMouseEnter: () => {
      clearLifecycleHideTimeout()
      if (!lifecycleHidden) setActivePhase(phase)
    },
    onMouseLeave: (event) => {
      if (isLifecycleWindowTarget(event.relatedTarget)) return
      scheduleLifecycleHide()
    },
  })

  const runDiagnostics = async () => {
    const selected = PREFLIGHT_INPUT_OPTIONS[diagType] || PREFLIGHT_INPUT_OPTIONS.content_id
    const normalizedInput = String(diagInput || '').trim()
    if (!normalizedInput) {
      setDiagError(`${selected.label} is required`)
      setDiagResult(null)
      return
    }

    setDiagRunning(true)
    setDiagError('')
    setDiagResult(null)

    try {
      const headers = {}
      if (String(apiKey || '').trim()) {
        headers.Authorization = `Bearer ${String(apiKey).trim()}`
      }

      const params = new URLSearchParams()
      params.set(selected.param, normalizedInput)
      params.set('file_indexes', String(diagFileIndexes || '0').trim() || '0')
      params.set('tier', diagTier)

      const response = await fetch(`${orchUrl}/api/v1/ace/preflight?${params.toString()}`, { headers })
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(payload?.detail || `HTTP ${response.status}`)
      }
      setDiagResult(payload)
      setShowDiagRaw(false)
    } catch (diagFailure) {
      setDiagError(diagFailure.message || String(diagFailure))
    } finally {
      setDiagRunning(false)
    }
  }

  if (loading) {
    return (
      <Card>
        <CardContent className="py-10 text-sm text-muted-foreground">Loading proxy settings...</CardContent>
      </Card>
    )
  }

  const lifecycleCopy = getLifecycleCopy(activePhase)

  return (
    <div className="space-y-5">
      {message && <p className="text-sm text-emerald-600 dark:text-emerald-400">{message}</p>}
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>Routing Controls</CardTitle>
              <CardDescription>Proxy mode, engine control path, and stream density limits.</CardDescription>
            </div>
            <Button type="button" variant="outline" onClick={() => setDiagOpen(true)}>
              <FlaskConical className="mr-2 h-4 w-4" />
              Preflight Diagnostics
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <SettingRow label="Stream Mode" description="Output protocol for playback endpoint.">
            <Select value={String(draft.stream_mode)} onValueChange={(value) => update('stream_mode', value)}>
              <SelectTrigger className="max-w-sm"><SelectValue placeholder="Select stream mode" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="TS">MPEG-TS</SelectItem>
                <SelectItem value="HLS">HLS</SelectItem>
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow label="Engine Control Mode" description="Command plane for engine lifecycle control.">
            <Select value={String(draft.control_mode)} onValueChange={(value) => update('control_mode', normalizeControlMode(value))}>
              <SelectTrigger className="max-w-sm"><SelectValue placeholder="Select control mode" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="http">HTTP Mode</SelectItem>
                <SelectItem value="api">API Mode</SelectItem>
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow label="Max Streams per Engine" description="Scale-out threshold per engine replica.">
            <Input type="number" min={1} max={20} value={draft.max_streams_per_engine} onChange={(e) => update('max_streams_per_engine', toNumber(e.target.value, DEFAULTS.max_streams_per_engine))} className="max-w-xs" />
          </SettingRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>Timeout and Buffering</CardTitle>
              <CardDescription>Startup wait, no-data detection, and shutdown grace controls.</CardDescription>
            </div>
            {lifecycleHidden && (
              <Button type="button" size="sm" variant="outline" onClick={() => setLifecycleHidden(false)}>
                Show Timeline Helper
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <SettingRow label="Initial Data Wait Timeout (s)" description="Maximum wait for first bytes.">
            <Input type="number" min={1} max={60} value={draft.initial_data_wait_timeout} onChange={(e) => update('initial_data_wait_timeout', toNumber(e.target.value, DEFAULTS.initial_data_wait_timeout))} className="max-w-xs" {...bindPhase('initial_data_wait')} />
          </SettingRow>
          <SettingRow label="Initial Data Check Interval (s)" description="Poll cadence while waiting for data.">
            <Input type="number" min={0.1} max={2} step={0.1} value={draft.initial_data_check_interval} onChange={(e) => update('initial_data_check_interval', toNumber(e.target.value, DEFAULTS.initial_data_check_interval))} className="max-w-xs" {...bindPhase('initial_data_wait')} />
          </SettingRow>
          <SettingRow label="No Data Timeout Checks" description="Consecutive misses before stream termination.">
            <Input type="number" min={5} max={600} value={draft.no_data_timeout_checks} onChange={(e) => update('no_data_timeout_checks', toNumber(e.target.value, DEFAULTS.no_data_timeout_checks))} className="max-w-xs" {...bindPhase('no_data_detection')} />
          </SettingRow>
          <SettingRow label="No Data Check Interval (s)" description="Poll cadence after no-data state.">
            <Input type="number" min={0.01} max={1} step={0.01} value={draft.no_data_check_interval} onChange={(e) => update('no_data_check_interval', toNumber(e.target.value, DEFAULTS.no_data_check_interval))} className="max-w-xs" {...bindPhase('no_data_detection')} />
          </SettingRow>
          <SettingRow label="Connection Timeout (s)" description="Health monitor inactivity threshold before forcing reconnect.">
            <Input type="number" min={5} max={60} value={draft.connection_timeout} onChange={(e) => update('connection_timeout', toNumber(e.target.value, DEFAULTS.connection_timeout))} className="max-w-xs" {...bindPhase('health_monitor_reconnect')} />
          </SettingRow>
          <SettingRow label="Upstream Connect Timeout (s)" description="Timeout for connecting to upstream playback/API endpoints.">
            <Input type="number" min={1} max={60} value={draft.upstream_connect_timeout} onChange={(e) => update('upstream_connect_timeout', toNumber(e.target.value, DEFAULTS.upstream_connect_timeout))} className="max-w-xs" {...bindPhase('upstream_connect')} />
          </SettingRow>
          <SettingRow label="Upstream Read Timeout (s)" description="Per-read timeout while receiving upstream stream data.">
            <Input type="number" min={1} max={120} value={draft.upstream_read_timeout} onChange={(e) => update('upstream_read_timeout', toNumber(e.target.value, DEFAULTS.upstream_read_timeout))} className="max-w-xs" {...bindPhase('upstream_read')} />
          </SettingRow>
          <SettingRow label="Stream Timeout (s)" description="Overall stream request timeout.">
            <Input type="number" min={10} max={300} value={draft.stream_timeout} onChange={(e) => update('stream_timeout', toNumber(e.target.value, DEFAULTS.stream_timeout))} className="max-w-xs" {...bindPhase('overall_stream_timeout')} />
          </SettingRow>
          <SettingRow
            label="Proxy Prebuffer (Seconds)"
            description="How many seconds of video the proxy should hold in memory before sending data to the player. Higher values provide a safety net for seamless engine failovers, but increase stream startup time. Set to 0 to disable."
          >
            <Input
              type="number"
              min={0}
              max={300}
              value={draft.proxy_prebuffer_seconds}
              onChange={(e) => update('proxy_prebuffer_seconds', Math.max(0, toNumber(e.target.value, DEFAULTS.proxy_prebuffer_seconds)))}
              className="max-w-xs"
              {...bindPhase('proxy_prebuffer')}
            />
          </SettingRow>
          <SettingRow
            label="Live Edge Delay"
            description="Default live edge offset used to stabilize live playback."
            warning="Potentially unstable with AceStream P2P live sources: swarm peers are often at the live head, which can starve the engine."
          >
            <Input type="number" min={0} max={1200} value={draft.ace_live_edge_delay} onChange={(e) => update('ace_live_edge_delay', toNumber(e.target.value, DEFAULTS.ace_live_edge_delay))} className="max-w-xs" {...bindPhase('live_edge_delay')} />
          </SettingRow>
          <SettingRow label="Idle Channel Shutdown Delay (s)" description="Grace delay before terminating idle channel.">
            <Input type="number" min={1} max={60} value={draft.channel_shutdown_delay} onChange={(e) => update('channel_shutdown_delay', toNumber(e.target.value, DEFAULTS.channel_shutdown_delay))} className="max-w-xs" {...bindPhase('idle_shutdown')} />
          </SettingRow>
        </CardContent>
      </Card>

      {String(draft.stream_mode) === 'HLS' && (
        <Card>
          <CardHeader>
            <CardTitle>HLS Parameters</CardTitle>
            <CardDescription>Segment window and startup buffering controls.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <SettingRow label="Max Segments" description="Maximum retained HLS segments.">
              <Input type="number" min={5} max={100} value={draft.hls_max_segments} onChange={(e) => update('hls_max_segments', toNumber(e.target.value, DEFAULTS.hls_max_segments))} className="max-w-xs" />
            </SettingRow>
            <SettingRow label="Initial Segments" description="Segments required before client playback.">
              <Input type="number" min={1} max={10} value={draft.hls_initial_segments} onChange={(e) => update('hls_initial_segments', toNumber(e.target.value, DEFAULTS.hls_initial_segments))} className="max-w-xs" />
            </SettingRow>
            <SettingRow label="Manifest Window Size" description="Segments advertised in active playlist window.">
              <Input type="number" min={3} max={20} value={draft.hls_window_size} onChange={(e) => update('hls_window_size', toNumber(e.target.value, DEFAULTS.hls_window_size))} className="max-w-xs" />
            </SettingRow>
          </CardContent>
        </Card>
      )}

      <AnimatePresence>
        {activePhase && !lifecycleHidden && (
          <motion.div
            data-lifecycle-window="true"
            key="lifecycle-floating-window"
            initial={{ opacity: 0, y: 18, scale: 0.97 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 16, scale: 0.98 }}
            transition={{ type: 'spring', stiffness: 260, damping: 24 }}
            className="fixed bottom-4 right-4 z-50 w-[min(760px,calc(100vw-1.5rem))]"
            onMouseEnter={() => {
              lifecycleWindowHoveredRef.current = true
              clearLifecycleHideTimeout()
            }}
            onMouseLeave={() => {
              lifecycleWindowHoveredRef.current = false
              if (!isLifecycleFieldFocused()) setActivePhase(null)
            }}
          >
            <Card className="border-slate-200/70 bg-white/95 shadow-2xl backdrop-blur-sm dark:border-slate-800 dark:bg-slate-950/95">
              <CardHeader className="pb-2">
                <div className="flex items-start justify-between gap-3">
                  <div className="space-y-1">
                    <p className="text-sm font-semibold text-foreground">{lifecycleCopy.title}</p>
                    <p className="text-xs text-muted-foreground">{lifecycleCopy.description}</p>
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() => setLifecycleHidden(true)}
                  >
                    <EyeOff className="mr-2 h-4 w-4" />
                    Hide Timeline
                  </Button>
                </div>
              </CardHeader>
              <CardContent className="pt-2">
                <InteractiveStreamLifecycle activePhase={activePhase} showDescriptionCard={false} />
              </CardContent>
            </Card>
          </motion.div>
        )}
      </AnimatePresence>

      <Dialog open={diagOpen} onOpenChange={setDiagOpen}>
        <DialogContent className="max-w-3xl">
          <DialogHeader>
            <DialogTitle>Proxy Preflight Diagnostics</DialogTitle>
            <DialogDescription>
              Operational tool. Runs immediately and does not participate in global settings dirty state.
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-3">
            <SettingRow label="Input Type" description="Content identifier format used for diagnostics.">
              <Select value={diagType} onValueChange={setDiagType}>
                <SelectTrigger className="max-w-sm"><SelectValue placeholder="Select input type" /></SelectTrigger>
                <SelectContent>
                  {Object.entries(PREFLIGHT_INPUT_OPTIONS).map(([value, option]) => (
                    <SelectItem key={value} value={value}>{option.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </SettingRow>

            <SettingRow label={(PREFLIGHT_INPUT_OPTIONS[diagType] || PREFLIGHT_INPUT_OPTIONS.content_id).label} description="Resource selector for this preflight request.">
              <Input value={diagInput} onChange={(e) => setDiagInput(e.target.value)} placeholder={(PREFLIGHT_INPUT_OPTIONS[diagType] || PREFLIGHT_INPUT_OPTIONS.content_id).placeholder} />
            </SettingRow>

            <SettingRow label="File Index" description="Index for multi-file torrents.">
              <Input value={diagFileIndexes} type="number" min={0} step={1} onChange={(e) => setDiagFileIndexes(e.target.value)} className="max-w-xs" />
            </SettingRow>

            <SettingRow label="Tier" description="light resolves only; deep performs start/status/stop.">
              <Select value={diagTier} onValueChange={setDiagTier}>
                <SelectTrigger className="max-w-sm"><SelectValue placeholder="Select tier" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="light">light</SelectItem>
                  <SelectItem value="deep">deep</SelectItem>
                </SelectContent>
              </Select>
            </SettingRow>

            {diagError && <p className="text-sm text-red-600 dark:text-red-400">{diagError}</p>}

            {diagResult && (
              <div className="space-y-4">
                <div className="rounded-md border p-3 text-sm space-y-3 bg-muted/20">
                  <div className="flex items-start justify-between">
                    <div>
                      <p className="font-semibold flex items-center gap-2">
                        Availability: 
                        <span className={diagResult?.result?.available ? 'text-emerald-600' : 'text-amber-600'}>
                          {diagResult?.result?.available ? 'Available' : 'Unavailable'}
                        </span>
                      </p>
                      <p className="text-xs text-muted-foreground">Control Mode: {diagResult?.control_mode || draft.control_mode}</p>
                    </div>
                    <div className="text-right">
                      <p className="text-xs font-medium">Engine Context</p>
                      <p className="text-[10px] text-muted-foreground font-mono">
                        {diagResult?.engine?.container_id?.slice(0, 12) || 'local'} 
                        ({diagResult?.engine?.host}:{diagResult?.engine?.port})
                      </p>
                    </div>
                  </div>

                  <div className="grid grid-cols-2 md:grid-cols-4 gap-3 border-t pt-3">
                    <div>
                      <p className="text-[10px] uppercase text-muted-foreground font-bold">Peers</p>
                      <p className="text-sm font-mono">
                        {preflightMetrics?.peers || 0}
                        {preflightMetrics?.httpPeers > 0 && <span className="text-[10px] ml-1 opacity-70">+{preflightMetrics.httpPeers}h</span>}
                      </p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase text-muted-foreground font-bold">Down Speed</p>
                      <p className="text-sm font-mono">{(preflightMetrics?.speed || 0).toLocaleString()} KB/s</p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase text-muted-foreground font-bold">Status</p>
                      <p className="text-sm font-medium truncate">{preflightMetrics?.status || 'N/A'}</p>
                    </div>
                    <div>
                      <p className="text-[10px] uppercase text-muted-foreground font-bold">Initial Runway</p>
                      <p className="text-sm font-mono text-blue-600 dark:text-blue-400">
                        {preflightMetrics?.runway != null ? `${preflightMetrics.runway}s` : 'N/A'}
                      </p>
                    </div>
                  </div>

                  {preflightMetrics?.checks && (
                    <div className="flex gap-4 border-t pt-2 mt-1">
                      <div className="flex items-center gap-1.5">
                        <div className={`h-1.5 w-1.5 rounded-full ${preflightMetrics.checks.transport_signal ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                        <span className="text-[10px] text-muted-foreground">Transport Signal</span>
                      </div>
                      <div className="flex items-center gap-1.5">
                        <div className={`h-1.5 w-1.5 rounded-full ${preflightMetrics.checks.progression_signal ? 'bg-emerald-500' : 'bg-slate-300'}`} />
                        <span className="text-[10px] text-muted-foreground">Progression Signal</span>
                      </div>
                    </div>
                  )}

                  <div className="border-t pt-2">
                    <p className="text-[10px] uppercase text-muted-foreground font-bold">Infohash</p>
                    <p className="text-xs font-mono break-all opacity-80">{diagResult?.result?.infohash || 'N/A'}</p>
                  </div>

                  {preflightFiles.length > 0 && (
                    <div className="mt-2 text-xs text-muted-foreground bg-background/50 rounded p-2">
                      <p className="font-medium text-foreground mb-1">Files ({preflightFiles.length}):</p>
                      <ul className="space-y-1">
                        {preflightFiles.slice(0, 20).map((entry) => (
                          <li key={`${entry.index}-${entry.label}`} className="truncate">
                            <span className="opacity-50 mr-1">[{entry.index}]</span> {entry.label}
                          </li>
                        ))}
                        {preflightFiles.length > 20 && (
                          <li className="italic pt-1">... and {preflightFiles.length - 20} more files</li>
                        )}
                      </ul>
                    </div>
                  )}
                </div>

                <div className="space-y-2">
                  <div className="flex items-center justify-between">
                    <p className="text-xs font-medium text-muted-foreground">Technical Investigation</p>
                    <Button 
                      variant="ghost" 
                      size="sm" 
                      className="h-7 text-[10px]" 
                      onClick={() => setShowDiagRaw(!showDiagRaw)}
                    >
                      {showDiagRaw ? 'Hide Raw JSON' : 'Show Raw JSON'}
                    </Button>
                  </div>
                  
                  {showDiagRaw && (
                    <div className="relative group">
                      <pre className="text-[10px] p-3 rounded bg-slate-950 text-slate-50 overflow-auto max-h-[300px] font-mono leading-relaxed">
                        {JSON.stringify(diagResult, null, 2)}
                      </pre>
                      <div className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <Button 
                          variant="secondary" 
                          size="sm" 
                          className="h-6 text-[10px]"
                          onClick={() => navigator.clipboard.writeText(JSON.stringify(diagResult, null, 2))}
                        >
                          Copy JSON
                        </Button>
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
          </div>

          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setDiagOpen(false)}>Close</Button>
            <Button type="button" onClick={runDiagnostics} disabled={diagRunning}>
              {diagRunning ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <AlertCircle className="mr-2 h-4 w-4" />}
              {diagRunning ? 'Running...' : 'Run Preflight'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
