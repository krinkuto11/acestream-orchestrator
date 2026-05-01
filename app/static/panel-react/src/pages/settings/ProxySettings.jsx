import React, { useEffect, useMemo, useRef, useState } from 'react'
import { SettingRow } from '@/components/settings/SettingRow'
import { useSettingsForm } from '@/context/SettingsFormContext'
import { InteractiveStreamLifecycle, getLifecycleCopy } from '@/components/settings/InteractiveStreamLifecycle'

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

const inputStyle = {
  background: 'var(--bg-0)', border: '1px solid var(--line)', color: 'var(--fg-0)',
  padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 11, outline: 'none', width: 120,
}
const selectStyle = {
  background: 'var(--bg-0)', border: '1px solid var(--line)', color: 'var(--fg-0)',
  padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 11, outline: 'none', cursor: 'pointer', minWidth: 140,
}

function Pane({ title, description, children, actions }) {
  return (
    <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-soft)' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 8 }}>
        <div style={{ flex: 1 }}>
          <span className="label">{title}</span>
          {description && <div style={{ fontSize: 10, color: 'var(--fg-2)', marginTop: 2 }}>{description}</div>}
        </div>
        {actions}
      </div>
      <div style={{ padding: '12px 14px' }}>{children}</div>
    </div>
  )
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
    try { return window.localStorage.getItem(LIFECYCLE_HELPER_HIDDEN_KEY) === '1' } catch { return false }
  })

  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(initialState),
    [draft, initialState],
  )

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
          ...DEFAULTS, ...payload,
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
        if (String(apiKey || '').trim()) headers.Authorization = `Bearer ${String(apiKey).trim()}`
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
        const response = await fetch(`${orchUrl}/api/v1/proxy/config?${params.toString()}`, { method: 'POST', headers })
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

    registerSection(sectionId, { title: 'Proxy', requiresAuth: true, save, discard })
    return () => unregisterSection(sectionId)
  }, [apiKey, authRequired, draft, initialState, orchUrl, registerSection, setSectionDirty, setSectionSaving, unregisterSection])

  useEffect(() => { setSectionDirty(sectionId, dirty) }, [dirty, setSectionDirty])

  useEffect(() => {
    try { window.localStorage.setItem(LIFECYCLE_HELPER_HIDDEN_KEY, lifecycleHidden ? '1' : '0') } catch { /* ignore */ }
  }, [lifecycleHidden])

  useEffect(() => {
    if (lifecycleHidden) setActivePhase(null)
  }, [lifecycleHidden])

  useEffect(() => {
    return () => { if (lifecycleHideTimeoutRef.current) window.clearTimeout(lifecycleHideTimeoutRef.current) }
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
    onFocus: () => { clearLifecycleHideTimeout(); if (!lifecycleHidden) setActivePhase(phase) },
    onBlur: (event) => { if (isLifecycleWindowTarget(event.relatedTarget)) return; scheduleLifecycleHide() },
    onMouseEnter: () => { clearLifecycleHideTimeout(); if (!lifecycleHidden) setActivePhase(phase) },
    onMouseLeave: (event) => { if (isLifecycleWindowTarget(event.relatedTarget)) return; scheduleLifecycleHide() },
  })

  if (loading) {
    return (
      <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-soft)', padding: '32px 14px', textAlign: 'center', fontSize: 11, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
        loading proxy settings...
      </div>
    )
  }

  const lifecycleCopy = getLifecycleCopy(activePhase)

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {message && <div style={{ fontSize: 11, color: 'var(--acc-green)', fontFamily: 'var(--font-mono)', padding: '4px 0' }}>{message}</div>}
      {error && <div style={{ fontSize: 11, color: 'var(--acc-red)', fontFamily: 'var(--font-mono)', padding: '4px 0' }}>{error}</div>}

      <Pane title="ROUTING CONTROLS" description="Proxy mode, engine control path, and stream density limits.">
        <SettingRow label="Stream Mode" description="Output protocol for playback endpoint.">
          <select value={String(draft.stream_mode)} onChange={(e) => update('stream_mode', e.target.value)} style={selectStyle}>
            <option value="TS">MPEG-TS</option>
            <option value="HLS">HLS</option>
          </select>
        </SettingRow>
        <SettingRow label="Engine Control Mode" description="Command plane for engine lifecycle control.">
          <select value={String(draft.control_mode)} onChange={(e) => update('control_mode', normalizeControlMode(e.target.value))} style={selectStyle}>
            <option value="http">HTTP Mode</option>
            <option value="api">API Mode</option>
          </select>
        </SettingRow>
        <SettingRow label="Max Streams per Engine" description="Scale-out threshold per engine replica.">
          <input type="number" min={1} max={20} value={draft.max_streams_per_engine} style={inputStyle} onChange={(e) => update('max_streams_per_engine', toNumber(e.target.value, DEFAULTS.max_streams_per_engine))}/>
        </SettingRow>
      </Pane>

      <Pane
        title="TIMEOUT &amp; BUFFERING"
        description="Startup wait, no-data detection, and shutdown grace controls."
        actions={lifecycleHidden ? (
          <button
            type="button"
            onClick={() => setLifecycleHidden(false)}
            style={{ background: 'none', border: '1px solid var(--line)', color: 'var(--fg-2)', padding: '3px 10px', fontFamily: 'var(--font-mono)', fontSize: 10, cursor: 'pointer' }}
          >
            Show Timeline
          </button>
        ) : null}
      >
        <SettingRow label="Initial Data Wait Timeout (s)" description="Maximum wait for first bytes.">
          <input type="number" min={1} max={60} value={draft.initial_data_wait_timeout} style={inputStyle} onChange={(e) => update('initial_data_wait_timeout', toNumber(e.target.value, DEFAULTS.initial_data_wait_timeout))} {...bindPhase('initial_data_wait')}/>
        </SettingRow>
        <SettingRow label="Initial Data Check Interval (s)" description="Poll cadence while waiting for data.">
          <input type="number" min={0.1} max={2} step={0.1} value={draft.initial_data_check_interval} style={inputStyle} onChange={(e) => update('initial_data_check_interval', toNumber(e.target.value, DEFAULTS.initial_data_check_interval))} {...bindPhase('initial_data_wait')}/>
        </SettingRow>
        <SettingRow label="No Data Timeout Checks" description="Consecutive misses before stream termination.">
          <input type="number" min={5} max={600} value={draft.no_data_timeout_checks} style={inputStyle} onChange={(e) => update('no_data_timeout_checks', toNumber(e.target.value, DEFAULTS.no_data_timeout_checks))} {...bindPhase('no_data_detection')}/>
        </SettingRow>
        <SettingRow label="No Data Check Interval (s)" description="Poll cadence after no-data state.">
          <input type="number" min={0.01} max={1} step={0.01} value={draft.no_data_check_interval} style={inputStyle} onChange={(e) => update('no_data_check_interval', toNumber(e.target.value, DEFAULTS.no_data_check_interval))} {...bindPhase('no_data_detection')}/>
        </SettingRow>
        <SettingRow label="Connection Timeout (s)" description="Health monitor inactivity threshold before forcing reconnect.">
          <input type="number" min={5} max={60} value={draft.connection_timeout} style={inputStyle} onChange={(e) => update('connection_timeout', toNumber(e.target.value, DEFAULTS.connection_timeout))} {...bindPhase('health_monitor_reconnect')}/>
        </SettingRow>
        <SettingRow label="Upstream Connect Timeout (s)" description="Timeout for connecting to upstream playback/API endpoints.">
          <input type="number" min={1} max={60} value={draft.upstream_connect_timeout} style={inputStyle} onChange={(e) => update('upstream_connect_timeout', toNumber(e.target.value, DEFAULTS.upstream_connect_timeout))} {...bindPhase('upstream_connect')}/>
        </SettingRow>
        <SettingRow label="Upstream Read Timeout (s)" description="Per-read timeout while receiving upstream stream data.">
          <input type="number" min={1} max={120} value={draft.upstream_read_timeout} style={inputStyle} onChange={(e) => update('upstream_read_timeout', toNumber(e.target.value, DEFAULTS.upstream_read_timeout))} {...bindPhase('upstream_read')}/>
        </SettingRow>
        <SettingRow label="Stream Timeout (s)" description="Overall stream request timeout.">
          <input type="number" min={10} max={300} value={draft.stream_timeout} style={inputStyle} onChange={(e) => update('stream_timeout', toNumber(e.target.value, DEFAULTS.stream_timeout))} {...bindPhase('overall_stream_timeout')}/>
        </SettingRow>
        <SettingRow label="Proxy Prebuffer (s)" description="Seconds of video held in memory before sending to player. Set to 0 to disable.">
          <input type="number" min={0} max={300} value={draft.proxy_prebuffer_seconds} style={inputStyle} onChange={(e) => update('proxy_prebuffer_seconds', Math.max(0, toNumber(e.target.value, DEFAULTS.proxy_prebuffer_seconds)))} {...bindPhase('proxy_prebuffer')}/>
        </SettingRow>
        <SettingRow label="Live Edge Delay" description="Default live edge offset for live playback." warning="Potentially unstable with AceStream P2P live sources.">
          <input type="number" min={0} max={1200} value={draft.ace_live_edge_delay} style={inputStyle} onChange={(e) => update('ace_live_edge_delay', toNumber(e.target.value, DEFAULTS.ace_live_edge_delay))} {...bindPhase('live_edge_delay')}/>
        </SettingRow>
        <SettingRow label="Idle Channel Shutdown Delay (s)" description="Grace delay before terminating idle channel.">
          <input type="number" min={1} max={60} value={draft.channel_shutdown_delay} style={inputStyle} onChange={(e) => update('channel_shutdown_delay', toNumber(e.target.value, DEFAULTS.channel_shutdown_delay))} {...bindPhase('idle_shutdown')}/>
        </SettingRow>
      </Pane>

      {String(draft.stream_mode) === 'HLS' && (
        <Pane title="HLS PARAMETERS" description="Segment window and startup buffering controls.">
          <SettingRow label="Max Segments" description="Maximum retained HLS segments.">
            <input type="number" min={5} max={100} value={draft.hls_max_segments} style={inputStyle} onChange={(e) => update('hls_max_segments', toNumber(e.target.value, DEFAULTS.hls_max_segments))}/>
          </SettingRow>
          <SettingRow label="Initial Segments" description="Segments required before client playback.">
            <input type="number" min={1} max={10} value={draft.hls_initial_segments} style={inputStyle} onChange={(e) => update('hls_initial_segments', toNumber(e.target.value, DEFAULTS.hls_initial_segments))}/>
          </SettingRow>
          <SettingRow label="Manifest Window Size" description="Segments advertised in active playlist window.">
            <input type="number" min={3} max={20} value={draft.hls_window_size} style={inputStyle} onChange={(e) => update('hls_window_size', toNumber(e.target.value, DEFAULTS.hls_window_size))}/>
          </SettingRow>
        </Pane>
      )}

      {/* Lifecycle floating helper */}
      {activePhase && !lifecycleHidden && (
        <div
          data-lifecycle-window="true"
          style={{
            position: 'fixed', bottom: 16, right: 16, zIndex: 40,
            width: 'min(760px, calc(100vw - 1.5rem))',
            background: 'var(--bg-1)', border: '1px solid var(--line)',
            boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
          }}
          onMouseEnter={() => { lifecycleWindowHoveredRef.current = true; clearLifecycleHideTimeout() }}
          onMouseLeave={() => { lifecycleWindowHoveredRef.current = false; if (!isLifecycleFieldFocused()) setActivePhase(null) }}
        >
          <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'flex-start', gap: 8 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontSize: 11, color: 'var(--fg-0)', fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{lifecycleCopy.title}</div>
              <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 2 }}>{lifecycleCopy.description}</div>
            </div>
            <button
              type="button"
              onClick={() => setLifecycleHidden(true)}
              style={{ background: 'none', border: '1px solid var(--line)', color: 'var(--fg-2)', padding: '3px 10px', fontFamily: 'var(--font-mono)', fontSize: 10, cursor: 'pointer' }}
            >
              Hide
            </button>
          </div>
          <div style={{ padding: '12px 14px' }}>
            <InteractiveStreamLifecycle activePhase={activePhase} showDescriptionCard={false}/>
          </div>
        </div>
      )}
    </div>
  )
}
