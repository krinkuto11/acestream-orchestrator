import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { AlertCircle, CheckCircle2, Info } from 'lucide-react'

export function ProxySettings({ apiKey, orchUrl }) {
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const [error, setError] = useState(null)
  
  // Proxy config state
  const [initialDataWaitTimeout, setInitialDataWaitTimeout] = useState(10)
  const [initialDataCheckInterval, setInitialDataCheckInterval] = useState(0.2)
  const [noDataTimeoutChecks, setNoDataTimeoutChecks] = useState(30)
  const [noDataCheckInterval, setNoDataCheckInterval] = useState(0.1)
  const [connectionTimeout, setConnectionTimeout] = useState(10)
  const [streamTimeout, setStreamTimeout] = useState(60)
  const [channelShutdownDelay, setChannelShutdownDelay] = useState(5)
  
  // Read-only config for display
  const [vlcUserAgent, setVlcUserAgent] = useState('')
  const [chunkSize, setChunkSize] = useState(0)
  const [bufferChunkSize, setBufferChunkSize] = useState(0)
  
  useEffect(() => {
    fetchProxyConfig()
  }, [orchUrl])
  
  const fetchProxyConfig = async () => {
    try {
      const response = await fetch(`${orchUrl}/proxy/config`)
      if (response.ok) {
        const data = await response.json()
        setInitialDataWaitTimeout(data.initial_data_wait_timeout)
        setInitialDataCheckInterval(data.initial_data_check_interval)
        setNoDataTimeoutChecks(data.no_data_timeout_checks)
        setNoDataCheckInterval(data.no_data_check_interval)
        setConnectionTimeout(data.connection_timeout)
        setStreamTimeout(data.stream_timeout)
        setChannelShutdownDelay(data.channel_shutdown_delay)
        setVlcUserAgent(data.vlc_user_agent)
        setChunkSize(data.chunk_size)
        setBufferChunkSize(data.buffer_chunk_size)
      }
    } catch (err) {
      console.error('Failed to fetch proxy config:', err)
    }
  }
  
  const saveProxyConfig = async () => {
    if (!apiKey) {
      setError('API Key is required to update settings')
      return
    }
    
    setLoading(true)
    setMessage(null)
    setError(null)
    
    try {
      const params = new URLSearchParams()
      params.append('initial_data_wait_timeout', initialDataWaitTimeout)
      params.append('initial_data_check_interval', initialDataCheckInterval)
      params.append('no_data_timeout_checks', noDataTimeoutChecks)
      params.append('no_data_check_interval', noDataCheckInterval)
      params.append('connection_timeout', connectionTimeout)
      params.append('stream_timeout', streamTimeout)
      params.append('channel_shutdown_delay', channelShutdownDelay)
      
      const response = await fetch(`${orchUrl}/proxy/config?${params}`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${apiKey}`
        }
      })
      
      if (response.ok) {
        const data = await response.json()
        setMessage(data.message)
        await fetchProxyConfig()
      } else {
        const errorData = await response.json()
        setError(errorData.detail || 'Failed to update configuration')
      }
    } catch (err) {
      setError('Failed to save configuration: ' + err.message)
    } finally {
      setLoading(false)
    }
  }
  
  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Stream Buffer Settings</CardTitle>
          <CardDescription>
            Configure how the proxy handles stream buffering and initial data waiting
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="initial-data-wait-timeout">Initial Data Wait Timeout (seconds)</Label>
            <Input
              id="initial-data-wait-timeout"
              type="number"
              min="1"
              max="60"
              value={initialDataWaitTimeout}
              onChange={(e) => setInitialDataWaitTimeout(parseInt(e.target.value) || 10)}
            />
            <p className="text-xs text-muted-foreground">
              Maximum time to wait for initial data before starting client streaming. 
              This prevents "no data" errors when clients connect before the HTTP streamer has fetched data.
              <br /><strong>Range:</strong> 1-60 seconds. <strong>Default:</strong> 10 seconds.
            </p>
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="initial-data-check-interval">Initial Data Check Interval (seconds)</Label>
            <Input
              id="initial-data-check-interval"
              type="number"
              min="0.1"
              max="2.0"
              step="0.1"
              value={initialDataCheckInterval}
              onChange={(e) => setInitialDataCheckInterval(parseFloat(e.target.value) || 0.2)}
            />
            <p className="text-xs text-muted-foreground">
              How often to check if initial data has arrived in the buffer.
              <br /><strong>Range:</strong> 0.1-2.0 seconds. <strong>Default:</strong> 0.2 seconds.
            </p>
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="no-data-timeout-checks">No Data Timeout Checks</Label>
            <Input
              id="no-data-timeout-checks"
              type="number"
              min="5"
              max="600"
              value={noDataTimeoutChecks}
              onChange={(e) => setNoDataTimeoutChecks(parseInt(e.target.value) || 30)}
            />
            <p className="text-xs text-muted-foreground">
              Number of consecutive empty buffer checks before declaring stream ended.
              Total timeout = checks × interval. Example: 30 checks × 0.1s = 3s timeout.
              <br /><strong>Range:</strong> 5-600 checks. <strong>Default:</strong> 30 checks.
            </p>
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="no-data-check-interval">No Data Check Interval (seconds)</Label>
            <Input
              id="no-data-check-interval"
              type="number"
              min="0.01"
              max="1.0"
              step="0.01"
              value={noDataCheckInterval}
              onChange={(e) => setNoDataCheckInterval(parseFloat(e.target.value) || 0.1)}
            />
            <p className="text-xs text-muted-foreground">
              Seconds between buffer checks when no data is available during streaming.
              For unstable streams, increase timeout checks or interval. Example: 100 checks × 0.1s = 10s tolerance.
              <br /><strong>Range:</strong> 0.01-1.0 seconds. <strong>Default:</strong> 0.1 seconds.
            </p>
          </div>
        </CardContent>
      </Card>
      
      <Card>
        <CardHeader>
          <CardTitle>Connection & Timeout Settings</CardTitle>
          <CardDescription>
            Configure connection timeouts and stream behavior
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="connection-timeout">Connection Timeout (seconds)</Label>
            <Input
              id="connection-timeout"
              type="number"
              min="5"
              max="60"
              value={connectionTimeout}
              onChange={(e) => setConnectionTimeout(parseInt(e.target.value) || 10)}
            />
            <p className="text-xs text-muted-foreground">
              Timeout for establishing connections to AceStream engines.
              <br /><strong>Range:</strong> 5-60 seconds. <strong>Default:</strong> 10 seconds.
            </p>
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="stream-timeout">Stream Timeout (seconds)</Label>
            <Input
              id="stream-timeout"
              type="number"
              min="10"
              max="300"
              value={streamTimeout}
              onChange={(e) => setStreamTimeout(parseInt(e.target.value) || 60)}
            />
            <p className="text-xs text-muted-foreground">
              Overall stream timeout for inactive streams.
              <br /><strong>Range:</strong> 10-300 seconds. <strong>Default:</strong> 60 seconds.
            </p>
          </div>
          
          <div className="space-y-2">
            <Label htmlFor="channel-shutdown-delay">Idle Stream Shutdown Delay (seconds)</Label>
            <Input
              id="channel-shutdown-delay"
              type="number"
              min="1"
              max="60"
              value={channelShutdownDelay}
              onChange={(e) => setChannelShutdownDelay(parseInt(e.target.value) || 5)}
            />
            <p className="text-xs text-muted-foreground">
              Delay before shutting down streams with no active clients.
              <br /><strong>Range:</strong> 1-60 seconds. <strong>Default:</strong> 5 seconds.
            </p>
          </div>
        </CardContent>
      </Card>
      
      <Card>
        <CardHeader>
          <CardTitle>Read-Only Configuration</CardTitle>
          <CardDescription>
            Current proxy configuration (cannot be changed via UI)
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label>User Agent</Label>
            <div className="px-3 py-2 bg-muted rounded-md font-mono text-sm">
              {vlcUserAgent || 'Loading...'}
            </div>
            <p className="text-xs text-muted-foreground">
              User agent used when fetching streams from AceStream engines
            </p>
          </div>
          
          <div className="grid grid-cols-2 gap-4">
            <div className="space-y-2">
              <Label>Chunk Size</Label>
              <div className="px-3 py-2 bg-muted rounded-md font-mono text-sm">
                {chunkSize} bytes
              </div>
            </div>
            
            <div className="space-y-2">
              <Label>Buffer Chunk Size</Label>
              <div className="px-3 py-2 bg-muted rounded-md font-mono text-sm">
                {bufferChunkSize > 0 ? (bufferChunkSize / 1024).toFixed(0) : '0'} KB
              </div>
            </div>
          </div>
        </CardContent>
      </Card>
      
      <div className="pt-4">
        <Button 
          onClick={saveProxyConfig}
          disabled={loading || !apiKey}
        >
          {loading ? 'Saving...' : 'Save Proxy Settings'}
        </Button>
        {!apiKey && (
          <p className="text-xs text-destructive mt-2">
            API Key is required to update settings
          </p>
        )}
      </div>
      
      {message && (
        <div className="flex items-center gap-2 p-3 bg-success/10 border border-success rounded-md">
          <CheckCircle2 className="h-4 w-4 text-success" />
          <span className="text-sm text-success">{message}</span>
        </div>
      )}
      
      {error && (
        <div className="flex items-center gap-2 p-3 bg-destructive/10 border border-destructive rounded-md">
          <AlertCircle className="h-4 w-4 text-destructive" />
          <span className="text-sm text-destructive">{error}</span>
        </div>
      )}
      
      <div className="flex items-start gap-2 p-3 bg-blue-500/10 border border-blue-500/20 rounded-md">
        <Info className="h-4 w-4 text-blue-500 mt-0.5 flex-shrink-0" />
        <div className="text-xs text-blue-500">
          <strong>Note:</strong> Changes to proxy settings affect new streams only. 
          Existing active streams will continue using their original settings. 
          Settings are persisted to a JSON file and will be restored on restart.
        </div>
      </div>
    </div>
  )
}
