import React, { useState, useEffect, useCallback } from 'react'
import { BrowserRouter, Routes, Route, useLocation } from 'react-router-dom'
import { ModernSidebar } from './components/ModernSidebar'
import { ModernHeader } from './components/ModernHeader'
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
import { cn } from './lib/utils'

function AppContent() {
  const { resolvedTheme } = useTheme()
  const { addNotification } = useNotifications()
  const location = useLocation()
  useFavicon(resolvedTheme)

  // Always use the current browser origin as URL
  const orchUrl = typeof window !== 'undefined' && window.location ? window.location.origin : 'http://localhost:8000'
  
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
    if (apiKey) {
      headers['Authorization'] = `Bearer ${apiKey}`
    }
    
    const response = await fetch(url, { ...options, headers })
    if (!response.ok) {
      throw new Error(`${response.status} ${response.statusText}`)
    }
    return response.json()
  }, [apiKey])

  const fetchData = useCallback(async () => {
    try {
      const [enginesData, streamsData, vpnData, orchStatus, engineStatsData] = await Promise.all([
        fetchJSON(`${orchUrl}/api/v1/engines`),
        fetchJSON(`${orchUrl}/api/v1/streams?status=started`),
        fetchJSON(`${orchUrl}/api/v1/vpn/status`).catch(() => ({ enabled: false })),
        fetchJSON(`${orchUrl}/api/v1/orchestrator/status`).catch(() => null),
        fetchJSON(`${orchUrl}/api/v1/engines/stats/all`).catch(() => ({}))
      ])

      const mergedEngines = (Array.isArray(enginesData) ? enginesData : []).map((engine) => ({
        ...engine,
        docker_stats: engineStatsData?.[engine.container_id] || null,
      }))
      
      let vpnDataWithIp = vpnData
      if (vpnData.enabled && vpnData.connected) {
        try {
          const publicIpData = await fetchJSON(`${orchUrl}/api/v1/vpn/publicip`)
          vpnDataWithIp = { ...vpnData, public_ip: publicIpData.public_ip }
        } catch (err) {
          console.warn('Failed to fetch VPN public IP:', err)
        }
      }
      
      setEngines(mergedEngines)
      setStreams(streamsData)
      setVpnStatus(vpnDataWithIp)
      setOrchestratorStatus(orchStatus)
      setLastUpdate(new Date())
      setIsConnected(true)
      setIsInitialLoad(false)
    } catch (err) {
      const errorMessage = err.message || String(err)
      addNotification(`Connection error: ${errorMessage}`, 'error')
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

      const mergedEngines = nextEngines.map((engine) => ({
        ...engine,
        docker_stats: nextEngineStats?.[engine.container_id] || null,
      }))

      setEngines(mergedEngines)
      setStreams(nextStreams)
      setVpnStatus(payload.vpn_status || { enabled: false })
      setOrchestratorStatus(payload.orchestrator_status || null)
      setLastUpdate(new Date())
      setIsConnected(true)
      setIsInitialLoad(false)
    }

    const connect = () => {
      if (closed) {
        return
      }

      if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
        fetchData()
        return
      }

      const streamUrl = new URL(`${orchUrl}/api/v1/events/stream`)
      if (apiKey) {
        streamUrl.searchParams.set('api_key', apiKey)
      }

      eventSource = new EventSource(streamUrl.toString())

      eventSource.onopen = () => {
        setIsConnected(true)
      }

      const handleSsePayload = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          applyPayload(parsed?.payload || {})
        } catch (err) {
          console.warn('Failed to parse SSE payload:', err)
        }
      }

      eventSource.addEventListener('full_sync', handleSsePayload)
      eventSource.onmessage = handleSsePayload

      eventSource.onerror = () => {
        setIsConnected(false)
        setIsInitialLoad(false)
        if (eventSource) {
          eventSource.close()
          eventSource = null
        }

        if (!closed) {
          reconnectTimer = window.setTimeout(connect, 2000)
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
  }, [orchUrl, apiKey, fetchData])

  const handleDeleteEngine = useCallback(async (containerId) => {
    if (!window.confirm('Are you sure you want to delete this engine?')) {
      return
    }
    try {
      await fetchJSON(`${orchUrl}/api/v1/containers/${encodeURIComponent(containerId)}`, {
        method: 'DELETE'
      })
      addNotification('Engine deleted successfully', 'success')
      await fetchData()
    } catch (err) {
      addNotification(`Failed to delete engine: ${err.message}`, 'error')
    }
  }, [orchUrl, fetchJSON, fetchData, addNotification])

  const handleStopStream = useCallback(async (streamId) => {
    if (!window.confirm('Are you sure you want to stop this stream?')) {
      return
    }
    try {
      await fetchJSON(`${orchUrl}/api/v1/streams/${encodeURIComponent(streamId)}`, {
        method: 'DELETE'
      })
      addNotification('Stream stopped successfully', 'success')
      await fetchData()
    } catch (err) {
      addNotification(`Failed to stop stream: ${err.message}`, 'error')
    }
  }, [orchUrl, fetchJSON, fetchData, addNotification])

  return (
    <div className="min-h-screen bg-white relative dark:bg-slate-950">
      <ModernSidebar />
      
      <div className="flex flex-col min-h-screen transition-all duration-300" style={{ marginLeft: 'var(--sidebar-width, 16rem)' }}>
        <main className={cn(
          "flex-1 flex flex-col min-h-0 overflow-y-auto",
          isTopologyPage ? "p-0" : "p-6 md:p-8"
        )}>
          {isInitialLoad ? (
            <div className="flex items-center justify-center h-full">
              <div className="text-center">
                <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary mb-4"></div>
                <p className="text-muted-foreground">Loading...</p>
              </div>
            </div>
          ) : (
            <Routes>
              <Route path="/" element={
                <StreamingCentralPage
                  engines={engines}
                  streams={streams}
                  vpnStatus={vpnStatus}
                  orchestratorStatus={orchestratorStatus}
                  orchUrl={orchUrl}
                  apiKey={apiKey}
                />
              } />
              <Route path="/engines" element={
                <EnginesPage
                  engines={engines}
                  onDeleteEngine={handleDeleteEngine}
                  vpnStatus={vpnStatus}
                  orchUrl={orchUrl}
                  apiKey={apiKey}
                  fetchJSON={fetchJSON}
                />
              } />
              <Route path="/streams" element={
                <StreamsPage
                  streams={streams}
                  orchUrl={orchUrl}
                  apiKey={apiKey}
                  onStopStream={handleStopStream}
                  onDeleteEngine={handleDeleteEngine}
                  debugMode={orchestratorStatus?.config?.debug_mode || false}
                />
              } />
              <Route path="/events" element={
                <EventsPage orchUrl={orchUrl} apiKey={apiKey} maxEventsDisplay={maxEventsDisplay} />
              } />
              <Route path="/metrics" element={
                <MetricsPage apiKey={apiKey} orchUrl={orchUrl} />
              } />
              <Route path="/stream-monitoring" element={
                <StreamMonitoringPage apiKey={apiKey} orchUrl={orchUrl} streams={streams} />
              } />
              <Route path="/routing-topology" element={
                <RoutingTopologyPage
                  engines={engines}
                  streams={streams}
                  vpnStatus={vpnStatus}
                  orchestratorStatus={orchestratorStatus}
                />
              } />
              <Route path="/settings" element={
                <SettingsPage
                  apiKey={apiKey}
                  setApiKey={setApiKey}
                  refreshInterval={refreshInterval}
                  setRefreshInterval={setRefreshInterval}
                  maxEventsDisplay={maxEventsDisplay}
                  setMaxEventsDisplay={setMaxEventsDisplay}
                  orchUrl={orchUrl}
                />
              } />
            </Routes>
          )}
        </main>
      </div>
    </div>
  )
}

function App() {
  return (
    <BrowserRouter basename="/panel">
      <ThemeProvider defaultTheme="dark">
        <NotificationProvider>
          <AppContent />
        </NotificationProvider>
      </ThemeProvider>
    </BrowserRouter>
  )
}

export default App
