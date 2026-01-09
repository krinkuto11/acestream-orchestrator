import React, { useState, useEffect, useCallback } from 'react'
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import { ModernSidebar } from './components/ModernSidebar'
import { ModernHeader } from './components/ModernHeader'
import { ThemeProvider, useTheme } from './components/ThemeProvider'
import { OverviewPage } from './pages/OverviewPage'
import { EnginesPage } from './pages/EnginesPage'
import { StreamsPage } from './pages/StreamsPage'
import { EventsPage } from './pages/EventsPage'
import { HealthPage } from './pages/HealthPage'
import { VPNPage } from './pages/VPNPage'
import { MetricsPage } from './pages/MetricsPage'
import { SettingsPage } from './pages/SettingsPage'
import { useLocalStorage } from './hooks/useLocalStorage'
import { useFavicon } from './hooks/useFavicon'
import { Toaster } from '@/components/ui/sonner'
import { toast } from 'sonner'

function AppContent() {
  const { resolvedTheme } = useTheme()
  useFavicon(resolvedTheme)
  // Always use the current browser origin as URL so the UI works regardless of which IP/host is used to access it
  const orchUrl = typeof window !== 'undefined' && window.location ? window.location.origin : 'http://localhost:8000'
  const [apiKey, setApiKey] = useLocalStorage('orch_apikey', '')
  const [refreshInterval, setRefreshInterval] = useLocalStorage('refresh_interval', 5000)
  const [maxEventsDisplay, setMaxEventsDisplay] = useLocalStorage('max_events_display', 100)
  
  const [engines, setEngines] = useState([])
  const [streams, setStreams] = useState([])
  const [vpnStatus, setVpnStatus] = useState({ enabled: false })
  const [orchestratorStatus, setOrchestratorStatus] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)
  const [isConnected, setIsConnected] = useState(false)
  const [isInitialLoad, setIsInitialLoad] = useState(true)

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
      const [enginesData, streamsData, vpnData, orchStatus] = await Promise.all([
        fetchJSON(`${orchUrl}/engines`),
        fetchJSON(`${orchUrl}/streams?status=started`),
        fetchJSON(`${orchUrl}/vpn/status`).catch(() => ({ enabled: false })),
        fetchJSON(`${orchUrl}/orchestrator/status`).catch(() => null)
      ])
      
      // Fetch VPN public IP if VPN is enabled and connected
      let vpnDataWithIp = vpnData
      if (vpnData.enabled && vpnData.connected) {
        try {
          const publicIpData = await fetchJSON(`${orchUrl}/vpn/publicip`)
          vpnDataWithIp = { ...vpnData, public_ip: publicIpData.public_ip }
        } catch (err) {
          console.warn('Failed to fetch VPN public IP:', err)
        }
      }
      
      setEngines(enginesData)
      setStreams(streamsData)
      setVpnStatus(vpnDataWithIp)
      setOrchestratorStatus(orchStatus)
      setLastUpdate(new Date())
      setIsConnected(true)
      setIsInitialLoad(false)
    } catch (err) {
      const errorMessage = err.message || String(err)
      toast.error(`Connection error: ${errorMessage}`)
      setIsConnected(false)
      setIsInitialLoad(false)
    }
  }, [orchUrl, fetchJSON])

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, refreshInterval)
    return () => clearInterval(interval)
  }, [fetchData, refreshInterval])

  const handleDeleteEngine = useCallback(async (containerId) => {
    if (!window.confirm('Are you sure you want to delete this engine?')) {
      return
    }
    
    try {
      await fetchJSON(`${orchUrl}/containers/${encodeURIComponent(containerId)}`, {
        method: 'DELETE'
      })
      toast.success('Engine deleted successfully')
      await fetchData()
    } catch (err) {
      toast.error(`Failed to delete engine: ${err.message}`)
    }
  }, [orchUrl, fetchJSON, fetchData])

  const handleStopStream = useCallback(async (streamId, containerId) => {
    if (!window.confirm('Are you sure you want to stop this stream?')) {
      return
    }
    
    try {
      await fetchJSON(`${orchUrl}/streams/${encodeURIComponent(streamId)}`, {
        method: 'DELETE'
      })
      toast.success('Stream stopped successfully')
      await fetchData()
    } catch (err) {
      toast.error(`Failed to stop stream: ${err.message}`)
    }
  }, [orchUrl, fetchJSON, fetchData])

  return (
    <BrowserRouter basename="/panel">
      <div className="min-h-screen bg-background">
        <ModernSidebar />
        
        <div className="flex flex-col min-h-screen transition-all duration-300" style={{ marginLeft: 'var(--sidebar-width, 16rem)' }}>
          <ModernHeader 
            isConnected={isConnected}
            lastUpdate={lastUpdate}
          />
          
          <main className="flex-1 overflow-y-auto p-6">
            {isInitialLoad ? (
              <div className="flex items-center justify-center h-full">
                <div className="text-center">
                  <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-primary mb-4"></div>
                  <p className="text-muted-foreground">Loading...</p>
                </div>
              </div>
            ) : (
              <Routes>
              <Route 
                path="/" 
                element={
                  <OverviewPage
                    engines={engines}
                    streams={streams}
                    vpnStatus={vpnStatus}
                    orchestratorStatus={orchestratorStatus}
                    orchUrl={orchUrl}
                    apiKey={apiKey}
                  />
                } 
              />
              <Route 
                path="/engines" 
                element={
                  <EnginesPage
                    engines={engines}
                    onDeleteEngine={handleDeleteEngine}
                    vpnStatus={vpnStatus}
                    orchUrl={orchUrl}
                    apiKey={apiKey}
                    fetchJSON={fetchJSON}
                  />
                } 
              />
              <Route 
                path="/streams" 
                element={
                  <StreamsPage
                    streams={streams}
                    orchUrl={orchUrl}
                    apiKey={apiKey}
                    onStopStream={handleStopStream}
                    onDeleteEngine={handleDeleteEngine}
                    debugMode={orchestratorStatus?.config?.debug_mode || false}
                  />
                } 
              />
              <Route 
                path="/events" 
                element={
                  <EventsPage
                    orchUrl={orchUrl}
                    apiKey={apiKey}
                    maxEventsDisplay={maxEventsDisplay}
                  />
                } 
              />
              <Route 
                path="/health" 
                element={
                  <HealthPage
                    apiKey={apiKey}
                    orchUrl={orchUrl}
                  />
                } 
              />
              <Route 
                path="/vpn" 
                element={
                  <VPNPage vpnStatus={vpnStatus} />
                } 
              />
              <Route 
                path="/metrics" 
                element={
                  <MetricsPage
                    apiKey={apiKey}
                    orchUrl={orchUrl}
                  />
                } 
              />
              <Route 
                path="/settings" 
                element={
                  <SettingsPage
                    apiKey={apiKey}
                    setApiKey={setApiKey}
                    refreshInterval={refreshInterval}
                    setRefreshInterval={setRefreshInterval}
                    maxEventsDisplay={maxEventsDisplay}
                    setMaxEventsDisplay={setMaxEventsDisplay}
                    orchUrl={orchUrl}
                  />
                } 
              />
            </Routes>
            )}
          </main>
        </div>
      </div>
      
      <Toaster />
    </BrowserRouter>
  )
}

function App() {
  return (
    <ThemeProvider defaultTheme="light">
      <AppContent />
    </ThemeProvider>
  )
}

export default App
