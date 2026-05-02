import React, { useState, useEffect, useCallback } from 'react'
import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom'
import { ModernSidebar } from './components/ModernSidebar'
import { ThemeProvider, useTheme } from './components/ThemeProvider'
import { NotificationProvider, useNotifications } from './context/NotificationContext'

import { StreamingCentralPage } from './pages/StreamingCentralPage'
import { EnginesPage } from './pages/EnginesPage'
import { StreamsPage } from './pages/StreamsPage'
import { EventsPage } from './pages/EventsPage'
import { MetricsPage } from './pages/MetricsPage'
import { StreamMonitoringPage } from './pages/StreamMonitoringPage'
import { RoutingTopologyPage } from './pages/RoutingTopologyPage'
import { SettingsPage } from './pages/SettingsPage'
import { useLocalStorage } from './hooks/useLocalStorage'
import { useFavicon } from './hooks/useFavicon'

// ── Page title map ──────────────────────────────────────────────────────────
const PAGE_TITLES = {
  '/':                  ['Overview',   'signal-room'],
  '/engines':           ['Engines',    'rack-view'],
  '/streams':           ['Streams',    'active-sessions'],
  '/stream-monitoring': ['Monitor',    'stream-detail'],
  '/routing-topology':  ['Topology',   'mesh-graph'],
  '/events':            ['Events',     'audit-log'],
  '/metrics':           ['Dashboard',  'telemetry'],
  '/settings':          ['Settings',   'runtime-config'],
}

// ── Topbar ──────────────────────────────────────────────────────────────────
function Topbar({ pathname, isConnected, lastUpdate }) {
  const { resolvedTheme, setTheme } = useTheme()
  const [title, breadcrumb] = PAGE_TITLES[pathname] || ['–', '']

  const tick = lastUpdate
    ? new Date(lastUpdate).toLocaleTimeString([], { hour12: false })
    : '–'

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      height: 44,
      padding: '0 16px',
      borderBottom: '1px solid var(--line-soft)',
      background: 'var(--bg-1)',
      flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span style={{
          fontFamily: 'var(--font-display)',
          fontSize: 14.5, fontWeight: 600,
          color: 'var(--fg-0)',
        }}>{title}</span>
        {breadcrumb && (
          <>
            <span style={{ color: 'var(--fg-3)' }}>/</span>
            <span style={{ fontSize: 12.5, color: 'var(--fg-2)' }}>{breadcrumb}</span>
          </>
        )}
      </div>

      <div style={{ flex: 1 }}/>

      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <span style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11.5, color: 'var(--fg-2)' }}>
          <span className="dot pulse" style={{ color: isConnected ? 'var(--acc-green)' : 'var(--acc-red)' }}/>
          <span style={{ color: isConnected ? 'var(--fg-2)' : 'var(--acc-red)' }}>
            {isConnected ? 'SSE LIVE' : 'DISCONNECTED'}
          </span>
        </span>
        <span style={{ fontSize: 11.5, color: 'var(--fg-3)' }}>{tick}</span>
        <button
          onClick={() => setTheme(resolvedTheme === 'dark' ? 'light' : 'dark')}
          style={{
            background: 'none',
            border: '1px solid var(--line)',
            color: 'var(--fg-2)',
            cursor: 'pointer',
            fontSize: 11.5,
            fontFamily: 'var(--font-mono)',
            padding: '2px 8px',
            lineHeight: 1.4,
          }}
        >
          {resolvedTheme === 'dark' ? '◑ LIGHT' : '◐ DARK'}
        </button>
      </div>
    </div>
  )
}

// ── Footer ──────────────────────────────────────────────────────────────────
function Footer({ orchestratorStatus, isConnected }) {
  const running = orchestratorStatus?.engines?.running ?? 0
  const capacity = orchestratorStatus?.capacity?.used ?? 0
  const health = isConnected
    ? (orchestratorStatus?.status === 'healthy' ? 'healthy' : 'degraded')
    : 'offline'
  const healthColor = health === 'healthy'
    ? 'var(--acc-green)'
    : health === 'degraded'
    ? 'var(--acc-amber)'
    : 'var(--acc-red)'

  return (
    <div style={{
      display: 'flex', alignItems: 'center',
      height: 22,
      padding: '0 12px',
      borderTop: '1px solid var(--line-soft)',
      background: 'var(--bg-1)',
      fontSize: 11.5,
      color: 'var(--fg-2)',
      flexShrink: 0,
      fontFamily: 'var(--font-mono)',
    }}>
      <Sep/><span>{window.location.hostname}:{window.location.port || '80'}</span>
      <Sep/><a href="/api/v1/docs" target="_blank" style={{ color: 'inherit', textDecoration: 'none' }}>API v1</a>
      <Sep/><span>engines {running}</span>
      <Sep/><span>capacity {capacity}</span>
      <div style={{ flex: 1 }}/>
      <span style={{ color: healthColor }}>● {health}</span>
    </div>
  )
}

function Sep() {
  return <span style={{ color: 'var(--fg-3)', padding: '0 8px' }}>│</span>
}

// ── Main app content ─────────────────────────────────────────────────────────
function AppContent() {
  const { resolvedTheme } = useTheme()
  const { addNotification } = useNotifications()
  const location = useLocation()
  useFavicon(resolvedTheme)

  const orchUrl = typeof window !== 'undefined' && window.location
    ? window.location.origin
    : 'http://localhost:8000'

  const [apiKey, setApiKey] = useLocalStorage('orch_apikey', '')
  const [refreshInterval, setRefreshInterval] = useLocalStorage('refresh_interval', 1000)
  const [maxEventsDisplay, setMaxEventsDisplay] = useLocalStorage('max_events_display', 100)

  const [engines, setEngines] = useState([])
  const [streams, setStreams] = useState([])
  const [vpnStatus, setVpnStatus] = useState({ enabled: false })
  const [orchestratorStatus, setOrchestratorStatus] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)
  const [isConnected, setIsConnected] = useState(false)
  const [isInitialLoad, setIsInitialLoad] = useState(true)

  const isTopologyPage = location.pathname === '/routing-topology'

  const fetchJSON = useCallback(async (url, options = {}) => {
    const headers = { ...options.headers }
    if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`
    const response = await fetch(url, { ...options, headers })
    if (!response.ok) throw new Error(`${response.status} ${response.statusText}`)
    return response.json()
  }, [apiKey])

  const fetchData = useCallback(async () => {
    try {
      const [enginesData, startedStreamsData, pendingFailoverStreamsData, vpnData, orchStatus, engineStatsData] = await Promise.all([
        fetchJSON(`${orchUrl}/api/v1/engines`),
        fetchJSON(`${orchUrl}/api/v1/streams?status=started`),
        fetchJSON(`${orchUrl}/api/v1/streams?status=pending_failover`).catch(() => []),
        fetchJSON(`${orchUrl}/api/v1/vpn/status`).catch(() => ({ enabled: false })),
        fetchJSON(`${orchUrl}/api/v1/orchestrator/status`).catch(() => null),
        fetchJSON(`${orchUrl}/api/v1/engines/stats/all`).catch(() => ({})),
      ])

      const streamsById = new Map()
      ;(Array.isArray(startedStreamsData) ? startedStreamsData : []).forEach(s => streamsById.set(String(s?.id || ''), s))
      ;(Array.isArray(pendingFailoverStreamsData) ? pendingFailoverStreamsData : []).forEach(s => {
        if (!streamsById.has(String(s?.id || ''))) streamsById.set(String(s?.id || ''), s)
      })
      const streamsData = Array.from(streamsById.values())

      const mergedEngines = (Array.isArray(enginesData) ? enginesData : []).map(engine => {
        const fromMap = engineStatsData?.[engine.container_id] || null
        const docker_stats = fromMap || (
          engine.cpu_percent != null || engine.memory_usage != null
            ? { cpu_percent: engine.cpu_percent ?? 0, memory_usage: engine.memory_usage ?? 0, memory_percent: engine.memory_percent ?? 0 }
            : null
        )
        return { ...engine, docker_stats }
      })

      let vpnDataWithIp = vpnData
      if (vpnData.enabled && vpnData.connected) {
        try {
          const publicIpData = await fetchJSON(`${orchUrl}/api/v1/vpn/publicip`)
          vpnDataWithIp = { ...vpnData, public_ip: publicIpData.public_ip }
        } catch { /* best-effort */ }
      }

      setEngines(mergedEngines)
      setStreams(streamsData)
      setVpnStatus(vpnDataWithIp)
      setOrchestratorStatus(orchStatus)
      setLastUpdate(new Date())
      setIsConnected(true)
      setIsInitialLoad(false)
    } catch (err) {
      addNotification(`Connection error: ${err.message || String(err)}`, 'error')
      setIsConnected(false)
      setIsInitialLoad(false)
    }
  }, [orchUrl, fetchJSON, addNotification])

  useEffect(() => {
    let eventSource = null
    let reconnectTimer = null
    let closed = false

    const applyPayload = (payload = {}) => {
      const nextEngines = Array.isArray(payload.engines) ? payload.engines : []
      const nextStreams = Array.isArray(payload.streams) ? payload.streams : []
      const nextEngineStats = payload.engine_docker_stats || {}

      const mergedEngines = nextEngines.map(engine => {
        const fromMap = nextEngineStats?.[engine.container_id] || null
        const docker_stats = fromMap || (
          engine.cpu_percent != null || engine.memory_usage != null
            ? { cpu_percent: engine.cpu_percent ?? 0, memory_usage: engine.memory_usage ?? 0, memory_percent: engine.memory_percent ?? 0 }
            : null
        )
        return { ...engine, docker_stats }
      })

      setEngines(mergedEngines)
      setStreams(nextStreams)
      if (Array.isArray(payload.vpn_nodes)) {
        const nodes = payload.vpn_nodes
        setVpnStatus({
          vpn_enabled: nodes.length > 0,
          vpn_nodes: nodes,
          nodes_total: nodes.length,
          nodes_healthy: nodes.filter(n => n.healthy).length,
        })
      } else if (payload.vpn_status) {
        setVpnStatus(payload.vpn_status)
      }
      setOrchestratorStatus(payload.orchestrator_status || null)
      setLastUpdate(new Date())
      setIsConnected(true)
      setIsInitialLoad(false)
    }

    const connect = () => {
      if (closed) return
      if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
        fetchData()
        return
      }

      const streamUrl = new URL(`${orchUrl}/api/v1/events/stream`)
      if (apiKey) streamUrl.searchParams.set('api_key', apiKey)

      eventSource = new EventSource(streamUrl.toString())
      eventSource.onopen = () => setIsConnected(true)

      const handleSsePayload = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          applyPayload(parsed?.payload || {})
        } catch { /* ignore */ }
      }

      eventSource.addEventListener('full_sync', handleSsePayload)
      eventSource.onmessage = handleSsePayload
      eventSource.onerror = () => {
        setIsConnected(false)
        setIsInitialLoad(false)
        if (eventSource) { eventSource.close(); eventSource = null }
        if (!closed) reconnectTimer = window.setTimeout(connect, 2000)
      }
    }

    connect()
    return () => {
      closed = true
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      if (eventSource) eventSource.close()
    }
  }, [orchUrl, apiKey, fetchData])

  const handleDeleteEngine = useCallback(async (containerId) => {
    if (!window.confirm('Are you sure you want to delete this engine?')) return
    try {
      await fetchJSON(`${orchUrl}/api/v1/containers/${encodeURIComponent(containerId)}`, { method: 'DELETE' })
      addNotification('Engine deleted successfully', 'success')
      await fetchData()
    } catch (err) {
      addNotification(`Failed to delete engine: ${err.message}`, 'error')
    }
  }, [orchUrl, fetchJSON, fetchData, addNotification])

  const handleStopStream = useCallback(async (streamId) => {
    if (!window.confirm('Are you sure you want to stop this stream?')) return
    try {
      await fetchJSON(`${orchUrl}/api/v1/streams/${encodeURIComponent(streamId)}`, { method: 'DELETE' })
      addNotification('Stream stopped successfully', 'success')
      await fetchData()
    } catch (err) {
      addNotification(`Failed to stop stream: ${err.message}`, 'error')
    }
  }, [orchUrl, fetchJSON, fetchData, addNotification])

  return (
    <div style={{
      display: 'flex',
      height: '100vh',
      overflow: 'hidden',
      background: 'var(--bg-0)',
      fontFamily: 'var(--font-mono)',
    }}>
      <ModernSidebar orchestratorStatus={orchestratorStatus} isConnected={isConnected}/>

      <div style={{
        display: 'flex', flexDirection: 'column',
        flex: 1, minWidth: 0, overflow: 'hidden',
        marginLeft: 200,
      }}>
        <Topbar pathname={location.pathname} isConnected={isConnected} lastUpdate={lastUpdate}/>

        <main style={{
          flex: 1,
          overflowY: 'auto',
          overflowX: 'hidden',
          padding: isTopologyPage ? 0 : 16,
          minHeight: 0,
        }}>
          {isInitialLoad ? (
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              height: '100%',
              flexDirection: 'column', gap: 12,
              color: 'var(--fg-2)',
              fontFamily: 'var(--font-mono)',
            }}>
              <div style={{
                width: 20, height: 20,
                border: '2px solid var(--line)',
                borderTopColor: 'var(--acc-green)',
                borderRadius: '50%',
                animation: 'spin 0.8s linear infinite',
              }}/>
              <span className="label">LOADING</span>
            </div>
          ) : (
            <Routes>
              <Route path="/" element={
                <StreamingCentralPage
                  engines={engines} streams={streams}
                  vpnStatus={vpnStatus} orchestratorStatus={orchestratorStatus}
                  orchUrl={orchUrl} apiKey={apiKey}
                />
              }/>
              <Route path="/engines" element={
                <EnginesPage
                  engines={engines} onDeleteEngine={handleDeleteEngine}
                  vpnStatus={vpnStatus} orchUrl={orchUrl} apiKey={apiKey} fetchJSON={fetchJSON}
                />
              }/>
              <Route path="/streams" element={
                <StreamsPage
                  streams={streams} orchUrl={orchUrl} apiKey={apiKey}
                  onStopStream={handleStopStream} onDeleteEngine={handleDeleteEngine}
                  debugMode={orchestratorStatus?.config?.debug_mode || false}
                />
              }/>
              <Route path="/events" element={
                <EventsPage orchUrl={orchUrl} apiKey={apiKey} maxEventsDisplay={maxEventsDisplay}/>
              }/>
              <Route path="/metrics" element={
                <MetricsPage apiKey={apiKey} orchUrl={orchUrl}/>
              }/>
              <Route path="/stream-monitoring" element={
                <StreamMonitoringPage apiKey={apiKey} orchUrl={orchUrl} streams={streams}/>
              }/>
              <Route path="/routing-topology" element={
                <RoutingTopologyPage
                  engines={engines} streams={streams}
                  vpnStatus={vpnStatus} orchestratorStatus={orchestratorStatus}
                />
              }/>
              <Route path="/settings" element={
                <SettingsPage
                  apiKey={apiKey} setApiKey={setApiKey}
                  refreshInterval={refreshInterval} setRefreshInterval={setRefreshInterval}
                  maxEventsDisplay={maxEventsDisplay} setMaxEventsDisplay={setMaxEventsDisplay}
                  orchUrl={orchUrl}
                />
              }/>
            </Routes>
          )}
        </main>

        <Footer orchestratorStatus={orchestratorStatus} isConnected={isConnected}/>
      </div>

      <style>{`
        @keyframes spin { to { transform: rotate(360deg); } }
      `}</style>
    </div>
  )
}

function App() {
  return (
    <BrowserRouter basename="/panel">
      <ThemeProvider defaultTheme="dark">
        <NotificationProvider>
          <AppContent/>
        </NotificationProvider>
      </ThemeProvider>
    </BrowserRouter>
  )
}

export default App
