import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Server, Activity, CheckCircle, ShieldCheck, Clock, TrendingUp, TrendingDown, AlertTriangle, Download, Upload, Cpu, MemoryStick, PlayCircle, StopCircle } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { cn } from '@/lib/utils'
import { formatBytesPerSecond, formatBytes } from '@/utils/formatters'
import { Progress } from '@/components/ui/progress'

function formatMonitorAge(ts) {
  if (!ts) return 'n/a'
  const parsed = Date.parse(ts)
  if (Number.isNaN(parsed)) return 'n/a'
  const delta = Math.max(0, Math.floor((Date.now() - parsed) / 1000))
  if (delta < 60) return `${delta}s ago`
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`
  return `${Math.floor(delta / 3600)}h ago`
}

function StatCard({ title, value, icon: Icon, trend, trendValue, variant = 'default' }) {
  const variantClasses = {
    default: 'text-primary',
    success: 'text-green-600 dark:text-green-400',
    warning: 'text-yellow-600 dark:text-yellow-400',
    error: 'text-red-600 dark:text-red-400',
    info: 'text-blue-600 dark:text-blue-400',
  }

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
        <CardTitle className="text-sm font-medium">{title}</CardTitle>
        <div className={cn("rounded-md p-2 bg-accent", variantClasses[variant])}>
          <Icon className="h-4 w-4" />
        </div>
      </CardHeader>
      <CardContent>
        <div className="text-2xl font-bold">{value}</div>
        {trend && (
          <p className={cn("text-xs flex items-center gap-1 mt-1", trend === 'up' ? 'text-green-600 dark:text-green-400' : 'text-red-600 dark:text-red-400')}>
            {trend === 'up' ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
            <span>{trendValue}</span>
          </p>
        )}
      </CardContent>
    </Card>
  )
}

function QuickStats({ engines, streams, vpnStatus, healthyEngines }) {
  // Determine VPN status display for redundant mode
  const getVPNStatusDisplay = () => {
    if (!vpnStatus.enabled) {
      return { value: 'Disabled', variant: 'default' }
    }
    
    if (vpnStatus.mode === 'redundant') {
      const vpn1Connected = vpnStatus.vpn1?.connected
      const vpn2Connected = vpnStatus.vpn2?.connected
      
      if (vpn1Connected && vpn2Connected) {
        return { value: 'Both Connected', variant: 'success' }
      } else if (vpn1Connected || vpn2Connected) {
        return { value: '1 Connected', variant: 'warning' }
      } else {
        return { value: 'Both Down', variant: 'error' }
      }
    }
    
    // Single VPN mode
    return {
      value: vpnStatus.connected ? 'Connected' : 'Disconnected',
      variant: vpnStatus.connected ? 'success' : 'error'
    }
  }
  
  const vpnDisplay = getVPNStatusDisplay()
  
  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
      <StatCard
        title="Total Engines"
        value={engines.length}
        icon={Server}
        variant="default"
      />
      <StatCard
        title="Active Streams"
        value={streams.length}
        icon={Activity}
        variant="info"
      />
      <StatCard
        title="Healthy Engines"
        value={healthyEngines}
        icon={CheckCircle}
        variant="success"
      />
      <StatCard
        title="VPN Status"
        value={vpnDisplay.value}
        icon={ShieldCheck}
        variant={vpnDisplay.variant}
      />
    </div>
  )
}

function ResourceUsage({ orchUrl }) {
  const [totalStats, setTotalStats] = useState(null)

  useEffect(() => {
    const fetchTotalStats = async () => {
      try {
        const response = await fetch(`${orchUrl}/engines/stats/total`)
        if (response.ok) {
          const data = await response.json()
          setTotalStats(data)
        }
      } catch (err) {
        console.error('Failed to fetch total stats:', err)
      }
    }

    // Fetch immediately
    fetchTotalStats()

    // Refresh every second for near real-time panel updates
    const interval = setInterval(fetchTotalStats, 1000)

    return () => clearInterval(interval)
  }, [orchUrl])

  // Always show the cards, even when loading or no engines
  const cpuPercent = totalStats?.total_cpu_percent || 0
  const memoryUsage = totalStats?.total_memory_usage || 0
  const containerCount = totalStats?.container_count || 0

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">Resource Usage</h3>
      
      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <Cpu className="h-4 w-4" />
              Total CPU Usage
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              <div className="text-2xl font-bold">
                {cpuPercent.toFixed(1)}%
              </div>
              <Progress value={Math.min(cpuPercent, 100)} className="h-2" />
              <p className="text-xs text-muted-foreground">
                {containerCount === 0 
                  ? 'No engines running' 
                  : `Across ${containerCount} ${containerCount === 1 ? 'engine' : 'engines'}`
                }
              </p>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base flex items-center gap-2">
              <MemoryStick className="h-4 w-4" />
              Total Memory Usage
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              <div className="text-2xl font-bold">
                {formatBytes(memoryUsage)}
              </div>
              <p className="text-xs text-muted-foreground">
                {containerCount === 0 
                  ? 'No engines running' 
                  : `Across ${containerCount} ${containerCount === 1 ? 'engine' : 'engines'}`
                }
              </p>
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function SystemStatus({ vpnStatus, orchestratorStatus }) {
  const emergencyMode = vpnStatus?.emergency_mode

  return (
    <div className="space-y-4">
      <h3 className="text-lg font-semibold">System Status</h3>
      
      {emergencyMode?.active && (
        <Alert variant="warning">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Emergency Mode Active</AlertTitle>
          <AlertDescription>
            System is operating in emergency mode due to VPN failure. 
            Failed VPN: {emergencyMode.failed_vpn || 'N/A'}
          </AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 md:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Capacity</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Used</span>
                <span className="font-medium">{orchestratorStatus?.capacity?.used || 0}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Available</span>
                <span className="font-medium">{orchestratorStatus?.capacity?.available || 0}</span>
              </div>
              <div className="flex justify-between text-sm">
                <span className="text-muted-foreground">Total</span>
                <span className="font-medium">{orchestratorStatus?.capacity?.total || 0}</span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-secondary">
                <div
                  className="h-full bg-primary transition-all"
                  style={{
                    width: `${((orchestratorStatus?.capacity?.used || 0) / (orchestratorStatus?.capacity?.total || 1)) * 100}%`,
                  }}
                />
              </div>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-base">Provisioning Status</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Can Provision</span>
                <Badge variant={orchestratorStatus?.provisioning?.can_provision ? "success" : "destructive"}>
                  {orchestratorStatus?.provisioning?.can_provision ? 'Yes' : 'No'}
                </Badge>
              </div>
              <div className="flex items-center justify-between">
                <span className="text-sm text-muted-foreground">Circuit Breaker</span>
                <Badge variant={orchestratorStatus?.provisioning?.circuit_breaker_state === 'closed' ? "success" : "warning"}>
                  {orchestratorStatus?.provisioning?.circuit_breaker_state || 'Unknown'}
                </Badge>
              </div>
              {orchestratorStatus?.provisioning?.blocked_reason && (
                <p className="text-xs text-muted-foreground mt-2">
                  {orchestratorStatus.provisioning.blocked_reason}
                </p>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  )
}

function LegacyMonitorSessions({ orchUrl, apiKey }) {
  const [monitors, setMonitors] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [actionError, setActionError] = useState(null)
  const [starting, setStarting] = useState(false)
  const [stoppingById, setStoppingById] = useState({})
  const [newMonitor, setNewMonitor] = useState({
    content_id: '',
    interval_s: '1.0',
    run_seconds: '0',
  })

  useEffect(() => {
    let cancelled = false

    const fetchMonitors = async (showLoading = false) => {
      if (showLoading) {
        setLoading(true)
      }

      if (!apiKey) {
        if (!cancelled) {
          setMonitors([])
          setLoading(false)
          setError('Set API key in Settings to view legacy monitor sessions')
        }
        return
      }

      try {
        const response = await fetch(`${orchUrl}/ace/monitor/legacy`, {
          headers: {
            Authorization: `Bearer ${apiKey}`,
          },
        })

        if (!response.ok) {
          throw new Error(`${response.status} ${response.statusText}`)
        }

        const payload = await response.json()
        if (!cancelled) {
          setMonitors(Array.isArray(payload?.items) ? payload.items : [])
          setError(null)
        }
      } catch (err) {
        if (!cancelled) {
          setError(err?.message || 'Failed to fetch legacy monitor sessions')
        }
      } finally {
        if (!cancelled) {
          setLoading(false)
        }
      }
    }

    fetchMonitors(true)
    const interval = setInterval(() => fetchMonitors(false), 1000)
    return () => {
      cancelled = true
      clearInterval(interval)
    }
  }, [orchUrl, apiKey])

  const fetchMonitorsNow = async () => {
    if (!apiKey) {
      return
    }
    try {
      const response = await fetch(`${orchUrl}/ace/monitor/legacy`, {
        headers: {
          Authorization: `Bearer ${apiKey}`,
        },
      })
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`)
      }
      const payload = await response.json()
      setMonitors(Array.isArray(payload?.items) ? payload.items : [])
      setError(null)
    } catch (err) {
      setError(err?.message || 'Failed to fetch legacy monitor sessions')
    }
  }

  const handleStartMonitor = async () => {
    if (!apiKey) {
      setActionError('Set API key in Settings to start monitor sessions')
      return
    }

    const contentId = (newMonitor.content_id || '').trim()
    if (!contentId) {
      setActionError('content_id is required')
      return
    }

    setStarting(true)
    setActionError(null)

    try {
      const response = await fetch(`${orchUrl}/ace/monitor/legacy/start`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${apiKey}`,
        },
        body: JSON.stringify({
          content_id: contentId,
          interval_s: Number(newMonitor.interval_s || 1),
          run_seconds: Number(newMonitor.run_seconds || 0),
        }),
      })

      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`
        try {
          const errPayload = await response.json()
          detail = errPayload?.detail || detail
        } catch {
          // keep fallback detail string
        }
        throw new Error(detail)
      }

      await response.json()
      setNewMonitor((prev) => ({ ...prev, content_id: '' }))
      await fetchMonitorsNow()
    } catch (err) {
      setActionError(err?.message || 'Failed to start monitor session')
    } finally {
      setStarting(false)
    }
  }

  const handleStopMonitor = async (monitorId) => {
    if (!apiKey) {
      setActionError('Set API key in Settings to stop monitor sessions')
      return
    }

    setStoppingById((prev) => ({ ...prev, [monitorId]: true }))
    setActionError(null)

    try {
      const response = await fetch(`${orchUrl}/ace/monitor/legacy/${encodeURIComponent(monitorId)}`, {
        method: 'DELETE',
        headers: {
          Authorization: `Bearer ${apiKey}`,
        },
      })

      if (!response.ok) {
        let detail = `${response.status} ${response.statusText}`
        try {
          const errPayload = await response.json()
          detail = errPayload?.detail || detail
        } catch {
          // keep fallback detail string
        }
        throw new Error(detail)
      }

      await fetchMonitorsNow()
    } catch (err) {
      setActionError(err?.message || 'Failed to stop monitor session')
    } finally {
      setStoppingById((prev) => ({ ...prev, [monitorId]: false }))
    }
  }

  const activeCount = monitors.filter((m) => ['starting', 'running', 'reconnecting'].includes(m.status)).length

  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base flex items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <Activity className="h-4 w-4" />
            Legacy Monitor Sessions
          </span>
          <Badge variant={activeCount > 0 ? 'success' : 'secondary'}>
            {activeCount} active
          </Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="mb-3 grid gap-2 md:grid-cols-5">
          <Input
            className="md:col-span-3"
            placeholder="content_id / infohash"
            value={newMonitor.content_id}
            onChange={(e) => setNewMonitor((prev) => ({ ...prev, content_id: e.target.value }))}
          />
          <Input
            type="number"
            min="0.5"
            step="0.5"
            placeholder="interval_s"
            value={newMonitor.interval_s}
            onChange={(e) => setNewMonitor((prev) => ({ ...prev, interval_s: e.target.value }))}
          />
          <Input
            type="number"
            min="0"
            step="1"
            placeholder="run_seconds"
            value={newMonitor.run_seconds}
            onChange={(e) => setNewMonitor((prev) => ({ ...prev, run_seconds: e.target.value }))}
          />
        </div>
        <div className="mb-3 flex items-center gap-2">
          <Button onClick={handleStartMonitor} disabled={starting || !apiKey} size="sm">
            <PlayCircle className="mr-1 h-4 w-4" />
            {starting ? 'Starting...' : 'Start Monitor'}
          </Button>
          <span className="text-xs text-muted-foreground">interval default 1s, run_seconds 0 means continuous</span>
        </div>
        {actionError && (
          <p className="mb-3 text-xs text-red-600 dark:text-red-400">{actionError}</p>
        )}

        {loading ? (
          <p className="text-sm text-muted-foreground">Loading monitor sessions...</p>
        ) : error ? (
          <p className="text-sm text-muted-foreground">{error}</p>
        ) : monitors.length === 0 ? (
          <p className="text-sm text-muted-foreground">No legacy monitor sessions</p>
        ) : (
          <div className="space-y-2">
            {monitors.slice(0, 6).map((monitor) => {
              const latest = monitor.latest_status || {}
              const statusText = latest.status_text || latest.status || 'unknown'
              const peers = latest.peers ?? latest.http_peers ?? 0
              const speedDown = latest.speed_down ?? latest.http_speed_down ?? 0
              const progress = latest.progress ?? latest.immediate_progress ?? latest.total_progress ?? 0

              return (
                <div key={monitor.monitor_id} className="flex items-center justify-between rounded-md border p-2">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium truncate">{monitor.content_id}</p>
                    <div className="mt-1 flex flex-wrap gap-3 text-xs text-muted-foreground">
                      <span>status: {statusText}</span>
                      <span>peers: {peers}</span>
                      <span>down: {formatBytesPerSecond((speedDown || 0) * 1024)}</span>
                      <span>progress: {progress}%</span>
                    </div>
                  </div>
                  <div className="ml-3 flex flex-col items-end gap-1">
                    <Badge variant={monitor.status === 'running' ? 'success' : (monitor.status === 'reconnecting' ? 'warning' : 'secondary')}>
                      {monitor.status}
                    </Badge>
                    <span className="text-xs text-muted-foreground">{formatMonitorAge(monitor.last_collected_at)}</span>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleStopMonitor(monitor.monitor_id)}
                      disabled={Boolean(stoppingById[monitor.monitor_id]) || !apiKey}
                    >
                      <StopCircle className="mr-1 h-3 w-3" />
                      {stoppingById[monitor.monitor_id] ? 'Stopping...' : 'Stop'}
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

function StreamCard({ stream, orchUrl, apiKey }) {
  const [title, setTitle] = React.useState(null)
  const [loading, setLoading] = React.useState(true)

  React.useEffect(() => {
    const fetchTitle = async () => {
      try {
        const headers = {}
        if (apiKey) {
          headers['Authorization'] = `Bearer ${apiKey}`
        }
        
        const response = await fetch(
          `${orchUrl}/streams/${encodeURIComponent(stream.id)}/extended-stats`,
          { headers }
        )
        
        if (response.ok) {
          const data = await response.json()
          if (data.title) {
            setTitle(data.title)
          }
        }
      } catch (err) {
        console.debug('Failed to fetch title for stream:', err)
      } finally {
        setLoading(false)
      }
    }
    
    fetchTitle()
  }, [stream.id, orchUrl, apiKey])

  const displayText = loading ? 'Loading...' : (title || stream.key || 'N/A')

  return (
    <div className="flex items-center justify-between border-b pb-2 last:border-0">
      <div className="flex-1 truncate">
        <p className="text-sm font-medium truncate">{stream.id.slice(0, 16)}...</p>
        <p className="text-xs text-muted-foreground truncate" title={displayText}>
          {displayText}
        </p>
        <div className="flex gap-3 mt-1">
          <span className="text-xs flex items-center gap-1 text-green-600 dark:text-green-400">
            <Download className="h-3 w-3" />
            {formatBytesPerSecond((stream.speed_down || 0) * 1024)}
          </span>
          <span className="text-xs flex items-center gap-1 text-red-600 dark:text-red-400">
            <Upload className="h-3 w-3" />
            {formatBytesPerSecond((stream.speed_up || 0) * 1024)}
          </span>
        </div>
      </div>
      <Badge variant="success" className="ml-2">Active</Badge>
    </div>
  )
}

function RecentActivity({ streams, engines, orchUrl, apiKey }) {
  const recentStreams = streams.slice(0, 5)
  const recentEngines = engines.slice(0, 5)

  return (
    <div className="grid gap-4 md:grid-cols-2">
      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent Streams</CardTitle>
        </CardHeader>
        <CardContent>
          {recentStreams.length === 0 ? (
            <p className="text-sm text-muted-foreground">No active streams</p>
          ) : (
            <div className="space-y-2">
              {recentStreams.map((stream) => (
                <StreamCard 
                  key={stream.id} 
                  stream={stream} 
                  orchUrl={orchUrl}
                  apiKey={apiKey}
                />
              ))}
            </div>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Recent Engines</CardTitle>
        </CardHeader>
        <CardContent>
          {recentEngines.length === 0 ? (
            <p className="text-sm text-muted-foreground">No engines available</p>
          ) : (
            <div className="space-y-2">
              {recentEngines.map((engine) => (
                <div key={engine.container_id} className="flex items-center justify-between border-b pb-2 last:border-0">
                  <div className="flex-1 truncate">
                    <p className="text-sm font-medium truncate">
                      {engine.container_name || engine.container_id.slice(0, 12)}
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {engine.host}:{engine.port}
                    </p>
                  </div>
                  <Badge 
                    variant={engine.health_status === 'healthy' ? 'success' : 'destructive'}
                    className="ml-2"
                  >
                    {engine.health_status || 'unknown'}
                  </Badge>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}

export function OverviewPage({ engines, streams, vpnStatus, orchestratorStatus, orchUrl, apiKey }) {
  const healthyEngines = engines.filter(e => e.health_status === 'healthy').length

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Overview</h1>
          <p className="text-muted-foreground mt-1">Monitor your AceStream orchestrator system status</p>
        </div>
      </div>

      <QuickStats
        engines={engines}
        streams={streams}
        vpnStatus={vpnStatus}
        healthyEngines={healthyEngines}
      />

      <ResourceUsage orchUrl={orchUrl} />

      <SystemStatus vpnStatus={vpnStatus} orchestratorStatus={orchestratorStatus} />

      <RecentActivity 
        streams={streams} 
        engines={engines} 
        orchUrl={orchUrl}
        apiKey={apiKey}
      />
    </div>
  )
}
