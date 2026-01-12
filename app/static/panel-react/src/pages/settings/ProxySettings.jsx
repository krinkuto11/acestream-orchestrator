import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { AlertCircle, CheckCircle2, Info } from 'lucide-react'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

export function ProxySettings({ apiKey, orchUrl }) {
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const [error, setError] = useState(null)
  
  // Default values
  const DEFAULT_MAX_STREAMS_PER_ENGINE = 3
  
  // Proxy config state
  const [initialDataWaitTimeout, setInitialDataWaitTimeout] = useState(10)
  const [initialDataCheckInterval, setInitialDataCheckInterval] = useState(0.2)
  const [noDataTimeoutChecks, setNoDataTimeoutChecks] = useState(60)
  const [noDataCheckInterval, setNoDataCheckInterval] = useState(1)
  const [connectionTimeout, setConnectionTimeout] = useState(10)
  const [streamTimeout, setStreamTimeout] = useState(60)
  const [channelShutdownDelay, setChannelShutdownDelay] = useState(5)
  const [maxStreamsPerEngine, setMaxStreamsPerEngine] = useState(DEFAULT_MAX_STREAMS_PER_ENGINE)
  const [streamMode, setStreamMode] = useState('TS')
  const [engineVariant, setEngineVariant] = useState('')
  
  // HLS-specific state
  const [hlsMaxSegments, setHlsMaxSegments] = useState(20)
  const [hlsInitialSegments, setHlsInitialSegments] = useState(3)
  const [hlsWindowSize, setHlsWindowSize] = useState(6)
  const [hlsBufferReadyTimeout, setHlsBufferReadyTimeout] = useState(30)
  const [hlsFirstSegmentTimeout, setHlsFirstSegmentTimeout] = useState(30)
  const [hlsInitialBufferSeconds, setHlsInitialBufferSeconds] = useState(10)
  const [hlsMaxInitialSegments, setHlsMaxInitialSegments] = useState(10)
  const [hlsSegmentFetchInterval, setHlsSegmentFetchInterval] = useState(0.5)
  
  // Read-only config for display
  const [vlcUserAgent, setVlcUserAgent] = useState('')
  const [chunkSize, setChunkSize] = useState(0)
  const [bufferChunkSize, setBufferChunkSize] = useState(0)
  
  // Custom variant state
  const [customVariantEnabled, setCustomVariantEnabled] = useState(false)
  const [customVariantCacheType, setCustomVariantCacheType] = useState('')
  const [variantDisplayName, setVariantDisplayName] = useState('')
  
  // Check if HLS is supported - double check both variant and cache type
  const isKrinkutoVariant = engineVariant.startsWith('krinkuto11-amd64')
  const hasCompatibleCache = !customVariantEnabled || (customVariantCacheType !== 'memory')
  const hlsSupported = isKrinkutoVariant && hasCompatibleCache
  
  useEffect(() => {
    fetchProxyConfig()
    fetchCustomVariantInfo()
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
        setMaxStreamsPerEngine(data.max_streams_per_engine || DEFAULT_MAX_STREAMS_PER_ENGINE)
        setStreamMode(data.stream_mode || 'TS')
        setEngineVariant(data.engine_variant || '')
        setVlcUserAgent(data.vlc_user_agent)
        setChunkSize(data.chunk_size)
        setBufferChunkSize(data.buffer_chunk_size)
        // HLS-specific settings
        setHlsMaxSegments(data.hls_max_segments || 20)
        setHlsInitialSegments(data.hls_initial_segments || 3)
        setHlsWindowSize(data.hls_window_size || 6)
        setHlsBufferReadyTimeout(data.hls_buffer_ready_timeout || 30)
        setHlsFirstSegmentTimeout(data.hls_first_segment_timeout || 30)
        setHlsInitialBufferSeconds(data.hls_initial_buffer_seconds || 10)
        setHlsMaxInitialSegments(data.hls_max_initial_segments || 10)
        setHlsSegmentFetchInterval(data.hls_segment_fetch_interval || 0.5)
      }
    } catch (err) {
      console.error('Failed to fetch proxy config:', err)
    }
  }
  
  const fetchCustomVariantInfo = async () => {
    try {
      const response = await fetch(`${orchUrl}/custom-variant/config`)
      if (response.ok) {
        const data = await response.json()
        setCustomVariantEnabled(data.enabled || false)
        
        // Find live-cache-type parameter
        const liveCacheParam = data.parameters?.find(p => p.name === '--live-cache-type')
        const cacheType = liveCacheParam?.enabled ? liveCacheParam.value : ''
        setCustomVariantCacheType(cacheType)
        
        // Determine variant display name
        if (data.enabled) {
          setVariantDisplayName('custom variant')
        } else {
          setVariantDisplayName(engineVariant)
        }
        
        // Auto-switch to TS if custom variant has memory-only cache and HLS is selected
        if (data.enabled && cacheType === 'memory' && streamMode === 'HLS') {
          setStreamMode('TS')
          setMessage('Stream mode automatically switched to MPEG-TS because custom variant uses memory-only cache (HLS requires disk or hybrid cache)')
        }
      }
    } catch (err) {
      console.error('Failed to fetch custom variant info:', err)
      // Fallback to using engineVariant
      setVariantDisplayName(engineVariant)
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
      params.append('max_streams_per_engine', maxStreamsPerEngine)
      params.append('stream_mode', streamMode)
      // HLS-specific parameters
      params.append('hls_max_segments', hlsMaxSegments)
      params.append('hls_initial_segments', hlsInitialSegments)
      params.append('hls_window_size', hlsWindowSize)
      params.append('hls_buffer_ready_timeout', hlsBufferReadyTimeout)
      params.append('hls_first_segment_timeout', hlsFirstSegmentTimeout)
      params.append('hls_initial_buffer_seconds', hlsInitialBufferSeconds)
      params.append('hls_max_initial_segments', hlsMaxInitialSegments)
      params.append('hls_segment_fetch_interval', hlsSegmentFetchInterval)
      
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
          <CardTitle>Stream Mode</CardTitle>
          <CardDescription>
            Choose between MPEG-TS and HLS streaming modes
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="stream-mode">Stream Mode</Label>
            <Select 
              value={streamMode} 
              onValueChange={(value) => {
                // Prevent switching to HLS if not supported
                if (value === 'HLS' && !hlsSupported) {
                  setError('HLS mode is not available with current engine configuration. Use krinkuto11-amd64 variant with disk or hybrid cache.')
                  return
                }
                setStreamMode(value)
                setError(null)
              }}
              disabled={!hlsSupported && streamMode === 'TS'}
            >
              <SelectTrigger id="stream-mode">
                <SelectValue placeholder="Select stream mode" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="TS">MPEG-TS (Transport Stream)</SelectItem>
                <SelectItem value="HLS" disabled={!hlsSupported}>
                  HLS (HTTP Live Streaming) {!hlsSupported && '- Not available'}
                </SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              The /ace/getstream endpoint will return streams in the selected mode.
              {!hlsSupported && (
                <>
                  <br />
                  <span className="text-amber-600 dark:text-amber-500 font-semibold">
                    ⚠️ HLS mode is not available. Requirements:
                    <br />
                    • Engine variant must be krinkuto11-amd64 (current: {variantDisplayName || engineVariant || 'Unknown'})
                    {customVariantEnabled && customVariantCacheType === 'memory' && (
                      <>
                        <br />
                        • Live cache type must be disk or hybrid (current: memory)
                      </>
                    )}
                  </span>
                </>
              )}
              {hlsSupported && (
                <>
                  <br />
                  <span className="text-green-600 dark:text-green-500 font-semibold">
                    ✓ HLS mode is supported (variant: {variantDisplayName || engineVariant}, cache: {customVariantCacheType || 'disk/hybrid'})
                  </span>
                </>
              )}
            </p>
          </div>
        </CardContent>
      </Card>
      
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
              Total timeout = checks × interval. Example: 60 checks × 1s = 60s timeout.
              <br /><strong>Range:</strong> 5-600 checks. <strong>Default:</strong> 60 checks.
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
              For unstable streams, increase timeout checks or interval. Example: 100 checks × 1s = 100s tolerance.
              <br /><strong>Range:</strong> 0.01-1.0 seconds. <strong>Default:</strong> 1 second.
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
          <CardTitle>Engine Provisioning Settings</CardTitle>
          <CardDescription>
            Configure how the orchestrator provisions new engines based on stream load
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="max-streams-per-engine">Maximum Streams Per Engine</Label>
            <Input
              id="max-streams-per-engine"
              type="number"
              min="1"
              max="20"
              value={maxStreamsPerEngine}
              onChange={(e) => setMaxStreamsPerEngine(parseInt(e.target.value) || 3)}
            />
            <p className="text-xs text-muted-foreground">
              Maximum number of streams per engine before provisioning a new engine.
              When all engines reach this threshold minus one (e.g., 2 streams for max of 3), 
              the orchestrator will automatically provision a new engine.
              <br /><strong>Range:</strong> 1-20 streams. <strong>Default:</strong> 3 streams.
            </p>
          </div>
        </CardContent>
      </Card>
      
      {streamMode === 'HLS' && (
        <Card>
          <CardHeader>
            <CardTitle>HLS Buffering Settings</CardTitle>
            <CardDescription>
              Configure HLS segment buffering and playback parameters
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="hls-max-segments">Max Segments to Buffer</Label>
                <Input
                  id="hls-max-segments"
                  type="number"
                  min="5"
                  max="100"
                  value={hlsMaxSegments}
                  onChange={(e) => setHlsMaxSegments(parseInt(e.target.value) || 20)}
                />
                <p className="text-xs text-muted-foreground">
                  Maximum number of segments to keep in buffer.
                  <br /><strong>Range:</strong> 5-100. <strong>Default:</strong> 20.
                </p>
              </div>
              
              <div className="space-y-2">
                <Label htmlFor="hls-initial-segments">Initial Segments</Label>
                <Input
                  id="hls-initial-segments"
                  type="number"
                  min="1"
                  max="10"
                  value={hlsInitialSegments}
                  onChange={(e) => setHlsInitialSegments(parseInt(e.target.value) || 3)}
                />
                <p className="text-xs text-muted-foreground">
                  Minimum segments before starting playback.
                  <br /><strong>Range:</strong> 1-10. <strong>Default:</strong> 3.
                </p>
              </div>
            </div>
            
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="hls-window-size">Manifest Window Size</Label>
                <Input
                  id="hls-window-size"
                  type="number"
                  min="3"
                  max="20"
                  value={hlsWindowSize}
                  onChange={(e) => setHlsWindowSize(parseInt(e.target.value) || 6)}
                />
                <p className="text-xs text-muted-foreground">
                  Number of segments in manifest window.
                  <br /><strong>Range:</strong> 3-20. <strong>Default:</strong> 6.
                </p>
              </div>
              
              <div className="space-y-2">
                <Label htmlFor="hls-initial-buffer-seconds">Initial Buffer Duration (sec)</Label>
                <Input
                  id="hls-initial-buffer-seconds"
                  type="number"
                  min="5"
                  max="60"
                  value={hlsInitialBufferSeconds}
                  onChange={(e) => setHlsInitialBufferSeconds(parseInt(e.target.value) || 10)}
                />
                <p className="text-xs text-muted-foreground">
                  Target duration for initial buffer.
                  <br /><strong>Range:</strong> 5-60s. <strong>Default:</strong> 10s.
                </p>
              </div>
            </div>
            
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="hls-buffer-ready-timeout">Buffer Ready Timeout (sec)</Label>
                <Input
                  id="hls-buffer-ready-timeout"
                  type="number"
                  min="5"
                  max="120"
                  value={hlsBufferReadyTimeout}
                  onChange={(e) => setHlsBufferReadyTimeout(parseInt(e.target.value) || 30)}
                />
                <p className="text-xs text-muted-foreground">
                  Timeout for initial buffer to be ready.
                  <br /><strong>Range:</strong> 5-120s. <strong>Default:</strong> 30s.
                </p>
              </div>
              
              <div className="space-y-2">
                <Label htmlFor="hls-first-segment-timeout">First Segment Timeout (sec)</Label>
                <Input
                  id="hls-first-segment-timeout"
                  type="number"
                  min="5"
                  max="120"
                  value={hlsFirstSegmentTimeout}
                  onChange={(e) => setHlsFirstSegmentTimeout(parseInt(e.target.value) || 30)}
                />
                <p className="text-xs text-muted-foreground">
                  Timeout for first segment to be available.
                  <br /><strong>Range:</strong> 5-120s. <strong>Default:</strong> 30s.
                </p>
              </div>
            </div>
            
            <div className="grid grid-cols-2 gap-4">
              <div className="space-y-2">
                <Label htmlFor="hls-max-initial-segments">Max Initial Segments</Label>
                <Input
                  id="hls-max-initial-segments"
                  type="number"
                  min="1"
                  max="20"
                  value={hlsMaxInitialSegments}
                  onChange={(e) => setHlsMaxInitialSegments(parseInt(e.target.value) || 10)}
                />
                <p className="text-xs text-muted-foreground">
                  Maximum segments to fetch during initial buffering.
                  <br /><strong>Range:</strong> 1-20. <strong>Default:</strong> 10.
                </p>
              </div>
              
              <div className="space-y-2">
                <Label htmlFor="hls-segment-fetch-interval">Fetch Interval Multiplier</Label>
                <Input
                  id="hls-segment-fetch-interval"
                  type="number"
                  min="0.1"
                  max="2.0"
                  step="0.1"
                  value={hlsSegmentFetchInterval}
                  onChange={(e) => setHlsSegmentFetchInterval(parseFloat(e.target.value) || 0.5)}
                />
                <p className="text-xs text-muted-foreground">
                  Multiplier for manifest fetch interval (× segment duration).
                  <br /><strong>Range:</strong> 0.1-2.0. <strong>Default:</strong> 0.5.
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
      
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
          <br />
          <strong>Stream Mode:</strong> The /ace/getstream endpoint will return streams in {streamMode} format.
          {streamMode === 'HLS' && ' HLS manifests (.m3u8) and segments will be served.'}
          {streamMode === 'TS' && ' MPEG-TS (video/mp2t) streams will be served.'}
        </div>
      </div>
    </div>
  )
}
