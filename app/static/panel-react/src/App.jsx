import React, { useState, useEffect, useCallback } from 'react'
import Header from './components/Header'
import KPICards from './components/KPICards'
import EngineList from './components/EngineList'
import StreamList from './components/StreamList'
import VPNStatus from './components/VPNStatus'
import StreamDetail from './components/StreamDetail'
import { useLocalStorage } from './hooks/useLocalStorage'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertCircle } from 'lucide-react'

function App() {
  const [orchUrl, setOrchUrl] = useLocalStorage('orch_url', 'http://localhost:8000')
  const [apiKey, setApiKey] = useLocalStorage('orch_apikey', '')
  const [refreshInterval, setRefreshInterval] = useLocalStorage('refresh_interval', 5000)
  
  const [engines, setEngines] = useState([])
  const [streams, setStreams] = useState([])
  const [vpnStatus, setVpnStatus] = useState({ enabled: false })
  const [selectedStream, setSelectedStream] = useState(null)
  const [error, setError] = useState(null)
  const [lastUpdate, setLastUpdate] = useState(null)
  const [isConnected, setIsConnected] = useState(false)

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
      setError(null)
      const [enginesData, streamsData, vpnData] = await Promise.all([
        fetchJSON(`${orchUrl}/engines`),
        fetchJSON(`${orchUrl}/streams?status=started`),
        fetchJSON(`${orchUrl}/vpn/status`).catch(() => ({ enabled: false }))
      ])
      
      // Fetch VPN public IP if VPN is enabled and connected
      let vpnDataWithIp = vpnData
      if (vpnData.enabled && vpnData.connected) {
        try {
          const publicIpData = await fetchJSON(`${orchUrl}/vpn/publicip`)
          vpnDataWithIp = { ...vpnData, public_ip: publicIpData.public_ip }
        } catch (err) {
          // If public IP fetch fails, continue without it
          console.warn('Failed to fetch VPN public IP:', err)
        }
      }
      
      setEngines(enginesData)
      setStreams(streamsData)
      setVpnStatus(vpnDataWithIp)
      setLastUpdate(new Date())
      setIsConnected(true)
    } catch (err) {
      setError(err.message || String(err))
      setIsConnected(false)
    }
  }, [orchUrl, fetchJSON])

  useEffect(() => {
    // Initial load
    fetchData()
    
    // Set up polling
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
      await fetchData()
    } catch (err) {
      setError(`Failed to delete engine: ${err.message}`)
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
      setSelectedStream(null)
      await fetchData()
    } catch (err) {
      setError(`Failed to stop stream: ${err.message}`)
    }
  }, [orchUrl, fetchJSON, fetchData])

  const healthyEngines = engines.filter(e => e.health_status === 'healthy').length

  return (
    <div className="min-h-screen bg-background">
      <Header
        orchUrl={orchUrl}
        setOrchUrl={setOrchUrl}
        apiKey={apiKey}
        setApiKey={setApiKey}
        refreshInterval={refreshInterval}
        setRefreshInterval={setRefreshInterval}
        isConnected={isConnected}
      />
      
      <div className="container mx-auto px-4 py-6 space-y-6">
        {/* KPI Cards */}
        <KPICards
          totalEngines={engines.length}
          activeStreams={streams.length}
          healthyEngines={healthyEngines}
          vpnStatus={vpnStatus}
          lastUpdate={lastUpdate}
        />

        {/* VPN Status */}
        {vpnStatus.enabled && (
          <VPNStatus vpnStatus={vpnStatus} />
        )}

        {/* Engines and Streams Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
          {/* Engines - Left Side */}
          <div>
            <EngineList
              engines={engines}
              onDeleteEngine={handleDeleteEngine}
              vpnStatus={vpnStatus}
            />
          </div>

          {/* Streams - Right Side */}
          <div>
            <StreamList
              streams={streams}
              selectedStream={selectedStream}
              onSelectStream={setSelectedStream}
            />
          </div>
        </div>

        {/* Stream Detail */}
        {selectedStream && (
          <StreamDetail
            stream={selectedStream}
            orchUrl={orchUrl}
            apiKey={apiKey}
            onStopStream={handleStopStream}
            onDeleteEngine={handleDeleteEngine}
            onClose={() => setSelectedStream(null)}
          />
        )}

        {/* Error Alert */}
        {error && (
          <div className="fixed bottom-4 right-4 max-w-md z-50">
            <Alert variant="destructive" className="shadow-lg">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>{error}</AlertDescription>
              <button 
                onClick={() => setError(null)}
                className="absolute top-2 right-2 rounded-sm opacity-70 ring-offset-background transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-ring focus:ring-offset-2"
              >
                <svg className="h-4 w-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </Alert>
          </div>
        )}
      </div>
    </div>
  )
}

export default App
