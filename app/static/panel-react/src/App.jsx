import React, { useState, useEffect, useCallback } from 'react'
import {
  Box,
  Container,
  Grid,
  Paper,
  Alert,
  Snackbar
} from '@mui/material'
import Header from './components/Header'
import KPICards from './components/KPICards'
import EngineList from './components/EngineList'
import StreamList from './components/StreamList'
import VPNStatus from './components/VPNStatus'
import StreamDetail from './components/StreamDetail'
import { useLocalStorage } from './hooks/useLocalStorage'

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
      
      setEngines(enginesData)
      setStreams(streamsData)
      setVpnStatus(vpnData)
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
    <Box sx={{ display: 'flex', flexDirection: 'column', minHeight: '100vh' }}>
      <Header
        orchUrl={orchUrl}
        setOrchUrl={setOrchUrl}
        apiKey={apiKey}
        setApiKey={setApiKey}
        refreshInterval={refreshInterval}
        setRefreshInterval={setRefreshInterval}
        onRefresh={fetchData}
        isConnected={isConnected}
      />
      
      <Container maxWidth="xl" sx={{ mt: 3, mb: 3, flex: 1 }}>
        <Grid container spacing={3}>
          {/* KPI Cards */}
          <Grid item xs={12}>
            <KPICards
              totalEngines={engines.length}
              activeStreams={streams.length}
              healthyEngines={healthyEngines}
              vpnStatus={vpnStatus}
              lastUpdate={lastUpdate}
            />
          </Grid>

          {/* VPN Status */}
          {vpnStatus.enabled && (
            <Grid item xs={12}>
              <VPNStatus vpnStatus={vpnStatus} />
            </Grid>
          )}

          {/* Engines */}
          <Grid item xs={12} md={selectedStream ? 6 : 12}>
            <EngineList
              engines={engines}
              onDeleteEngine={handleDeleteEngine}
            />
          </Grid>

          {/* Streams */}
          <Grid item xs={12} md={selectedStream ? 6 : 12}>
            <StreamList
              streams={streams}
              selectedStream={selectedStream}
              onSelectStream={setSelectedStream}
            />
          </Grid>

          {/* Stream Detail */}
          {selectedStream && (
            <Grid item xs={12}>
              <StreamDetail
                stream={selectedStream}
                orchUrl={orchUrl}
                apiKey={apiKey}
                onStopStream={handleStopStream}
                onDeleteEngine={handleDeleteEngine}
                onClose={() => setSelectedStream(null)}
              />
            </Grid>
          )}
        </Grid>
      </Container>

      <Snackbar
        open={!!error}
        autoHideDuration={6000}
        onClose={() => setError(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'center' }}
      >
        <Alert onClose={() => setError(null)} severity="error" sx={{ width: '100%' }}>
          {error}
        </Alert>
      </Snackbar>
    </Box>
  )
}

export default App
