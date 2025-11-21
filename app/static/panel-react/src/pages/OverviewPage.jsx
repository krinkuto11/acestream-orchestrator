import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Server, Activity, CheckCircle, ShieldCheck, Clock, TrendingUp, TrendingDown, AlertTriangle, Download, Upload, Cpu, MemoryStick } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { cn } from '@/lib/utils'
import { formatBytesPerSecond, formatBytes } from '@/utils/formatters'
import { Progress } from '@/components/ui/progress'

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
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchTotalStats = async () => {
      try {
        // Use the /engines/stats/total endpoint which uses cached stats from the background collector
        const response = await fetch(`${orchUrl}/engines/stats/total`)
        if (response.ok) {
          const data = await response.json()
          setTotalStats(data)
        } else {
          console.error('Failed to fetch total stats: HTTP', response.status)
        }
      } catch (err) {
        console.error('Failed to fetch total stats:', err)
      } finally {
        setLoading(false)
      }
    }

    // Fetch immediately
    fetchTotalStats()

    // Refresh every 5 seconds
    const interval = setInterval(fetchTotalStats, 5000)

    return () => clearInterval(interval)
  }, [orchUrl])

  // Don't show anything while loading initially
  if (loading) {
    return null
  }

  if (!totalStats || totalStats.container_count === 0) {
    return (
      <div className="space-y-4">
        <h3 className="text-lg font-semibold">Resource Usage</h3>
        <Card>
          <CardContent className="pt-6">
            <p className="text-sm text-muted-foreground">
              No engines running - resource usage unavailable
            </p>
          </CardContent>
        </Card>
      </div>
    )
  }

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
                {totalStats.total_cpu_percent.toFixed(2)}%
              </div>
              <Progress value={Math.min(totalStats.total_cpu_percent, 100)} className="h-2" />
              <p className="text-xs text-muted-foreground">
                Across {totalStats.container_count} {totalStats.container_count === 1 ? 'engine' : 'engines'}
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
                {formatBytes(totalStats.total_memory_usage)}
              </div>
              <p className="text-xs text-muted-foreground">
                Across {totalStats.container_count} {totalStats.container_count === 1 ? 'engine' : 'engines'}
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
