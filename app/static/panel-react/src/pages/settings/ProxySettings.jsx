import React, { useEffect, useMemo, useState } from 'react'
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
import { AlertCircle, FlaskConical, Loader2 } from 'lucide-react'
import { SettingRow } from '@/components/settings/SettingRow'
import { useSettingsForm } from '@/context/SettingsFormContext'

const DEFAULTS = {
  initial_data_wait_timeout: 10,
  initial_data_check_interval: 0.2,
  no_data_timeout_checks: 60,
  no_data_check_interval: 1,
  connection_timeout: 10,
  stream_timeout: 60,
  channel_shutdown_delay: 5,
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

  const [diagOpen, setDiagOpen] = useState(false)
  const [diagType, setDiagType] = useState('content_id')
  const [diagInput, setDiagInput] = useState('')
  const [diagFileIndexes, setDiagFileIndexes] = useState('0')
  const [diagTier, setDiagTier] = useState('light')
  const [diagRunning, setDiagRunning] = useState(false)
  const [diagError, setDiagError] = useState('')
  const [diagResult, setDiagResult] = useState(null)

  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(initialState),
    [draft, initialState],
  )

  useEffect(() => {
    const fetchConfig = async () => {
      setLoading(true)
      setError('')
      try {
        const response = await fetch(`${orchUrl}/api/v1/proxy/config`)
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        const payload = await response.json()
        const normalized = {
          ...DEFAULTS,
          ...payload,
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
        params.set('stream_timeout', String(toNumber(draft.stream_timeout, DEFAULTS.stream_timeout)))
        params.set('channel_shutdown_delay', String(toNumber(draft.channel_shutdown_delay, DEFAULTS.channel_shutdown_delay)))
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

  const update = (field, value) => {
    setDraft((prev) => ({ ...prev, [field]: value }))
    setError('')
    setMessage('')
  }

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

  const preflightFiles = extractLoadRespFiles(diagResult)

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
          <CardTitle>Timeout and Buffering</CardTitle>
          <CardDescription>Startup wait, no-data detection, and shutdown grace controls.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <SettingRow label="Initial Data Wait Timeout (s)" description="Maximum wait for first bytes.">
            <Input type="number" min={1} max={60} value={draft.initial_data_wait_timeout} onChange={(e) => update('initial_data_wait_timeout', toNumber(e.target.value, DEFAULTS.initial_data_wait_timeout))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Initial Data Check Interval (s)" description="Poll cadence while waiting for data.">
            <Input type="number" min={0.1} max={2} step={0.1} value={draft.initial_data_check_interval} onChange={(e) => update('initial_data_check_interval', toNumber(e.target.value, DEFAULTS.initial_data_check_interval))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="No Data Timeout Checks" description="Consecutive misses before stream termination.">
            <Input type="number" min={5} max={600} value={draft.no_data_timeout_checks} onChange={(e) => update('no_data_timeout_checks', toNumber(e.target.value, DEFAULTS.no_data_timeout_checks))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="No Data Check Interval (s)" description="Poll cadence after no-data state.">
            <Input type="number" min={0.01} max={1} step={0.01} value={draft.no_data_check_interval} onChange={(e) => update('no_data_check_interval', toNumber(e.target.value, DEFAULTS.no_data_check_interval))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Connection Timeout (s)" description="Upstream socket connect timeout.">
            <Input type="number" min={5} max={60} value={draft.connection_timeout} onChange={(e) => update('connection_timeout', toNumber(e.target.value, DEFAULTS.connection_timeout))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Stream Timeout (s)" description="Overall stream request timeout.">
            <Input type="number" min={10} max={300} value={draft.stream_timeout} onChange={(e) => update('stream_timeout', toNumber(e.target.value, DEFAULTS.stream_timeout))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Idle Channel Shutdown Delay (s)" description="Grace delay before terminating idle channel.">
            <Input type="number" min={1} max={60} value={draft.channel_shutdown_delay} onChange={(e) => update('channel_shutdown_delay', toNumber(e.target.value, DEFAULTS.channel_shutdown_delay))} className="max-w-xs" />
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
              <div className="rounded-md border p-3 text-sm">
                <p className="font-semibold">Availability: {diagResult?.result?.available ? 'Available' : 'Unavailable'}</p>
                <p className="text-xs text-muted-foreground">Control Mode: {diagResult?.control_mode || draft.control_mode}</p>
                <p className="text-xs text-muted-foreground break-all">Infohash: {diagResult?.result?.infohash || 'N/A'}</p>
                {preflightFiles.length > 0 && (
                  <div className="mt-2 text-xs text-muted-foreground">
                    <p className="font-medium text-foreground">Files:</p>
                    <ul className="space-y-1">
                      {preflightFiles.slice(0, 8).map((entry) => (
                        <li key={`${entry.index}-${entry.label}`}>[{entry.index}] {entry.label}</li>
                      ))}
                    </ul>
                  </div>
                )}
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
