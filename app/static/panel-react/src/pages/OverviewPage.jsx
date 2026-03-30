import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Server, Activity, CheckCircle, ShieldCheck, TrendingUp, TrendingDown, AlertTriangle, Download, Upload, Cpu, MemoryStick } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { cn } from '@/lib/utils'
import { formatBytesPerSecond, formatBytes } from '@/utils/formatters'
import { Progress } from '@/components/ui/progress'
import { MetricTile } from '@/components/MetricTile'

function StatCard({ title, value, icon: Icon, trend, trendValue, status = 'default' }) {
  const iconColor = {
    default: 'text-muted-foreground',
    success: 'text-emerald-500',
    warning: 'text-amber-500',
    error:   'text-rose-500',
    info:    'text-sky-500',
  }

  return (
    <MetricTile title={title} value={value} icon={Icon} status={status}>
      {trend && (
        <p className={cn(
          'text-xs flex items-center gap-1 mt-2',
          trend === 'up' ? 'text-emerald-600 dark:text-emerald-400' : 'text-rose-600 dark:text-rose-400',
        )}>
          {trend === 'up' ? <TrendingUp className="h-3 w-3" /> : <TrendingDown className="h-3 w-3" />}
          <span>{trendValue}</span>
        </p>
      )}
    </MetricTile>
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
    <div className="grid grid-cols-12 gap-4">
      <div className="col-span-12 sm:col-span-6 lg:col-span-3">
        <StatCard
          title="Total Engines"
          value={engines.length}
          icon={Server}
          status="default"
        />
      </div>
      <div className="col-span-12 sm:col-span-6 lg:col-span-3">
        <StatCard
          title="Active Streams"
          value={streams.length}
          icon={Activity}
          status="info"
        />
      </div>
      <div className="col-span-12 sm:col-span-6 lg:col-span-3">
        <StatCard
          title="Healthy Engines"
          value={healthyEngines}
          icon={CheckCircle}
          status="success"
        />
      </div>
      <div className="col-span-12 sm:col-span-6 lg:col-span-3">
        <StatCard
          title="VPN Status"
          value={vpnDisplay.value}
          icon={ShieldCheck}
          status={vpnDisplay.variant}
        />
      </div>
    </div>
  )
}

function ResourceUsage({ orchUrl }) {
  const [totalStats, setTotalStats] = useState(null)

  useEffect(() => {
    const fetchTotalStats = async () => {
      try {
        const response = await fetch(`${orchUrl}/api/v1/engines/stats/total`)
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
      <h3 className="text-base font-semibold text-foreground">Resource Usage</h3>

      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 md:col-span-6">
          <MetricTile
            title="Total CPU Usage"
            value={`${cpuPercent.toFixed(1)}%`}
            icon={Cpu}
            status={cpuPercent > 80 ? 'error' : cpuPercent > 60 ? 'warning' : 'default'}
          >
            <Progress value={Math.min(cpuPercent, 100)} className="mt-2 h-1.5" />
            <p className="mt-1 text-xs text-muted-foreground">
              {containerCount === 0
                ? 'No engines running'
                : `Across ${containerCount} ${containerCount === 1 ? 'engine' : 'engines'}`}
            </p>
          </MetricTile>
        </div>

        <div className="col-span-12 md:col-span-6">
          <MetricTile
            title="Total Memory Usage"
            value={formatBytes(memoryUsage)}
            icon={MemoryStick}
            status="default"
          >
            <p className="mt-2 text-xs text-muted-foreground">
              {containerCount === 0
                ? 'No engines running'
                : `Across ${containerCount} ${containerCount === 1 ? 'engine' : 'engines'}`}
            </p>
          </MetricTile>
        </div>
      </div>
    </div>
  )
}

function SystemStatus({ vpnStatus, orchestratorStatus }) {
  const emergencyMode = vpnStatus?.emergency_mode

  return (
    <div className="space-y-4">
      <h3 className="text-base font-semibold text-foreground">System Status</h3>

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

      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 md:col-span-6">
          <Card className="h-full shadow-sm">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Capacity
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <div className="space-y-2">
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Used</span>
                  <span className="font-medium text-foreground">{orchestratorStatus?.capacity?.used || 0}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Available</span>
                  <span className="font-medium text-foreground">{orchestratorStatus?.capacity?.available || 0}</span>
                </div>
                <div className="flex justify-between text-sm">
                  <span className="text-muted-foreground">Total</span>
                  <span className="font-medium text-foreground">{orchestratorStatus?.capacity?.total || 0}</span>
                </div>
                <div className="h-1.5 w-full overflow-hidden rounded-full bg-secondary">
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
        </div>

        <div className="col-span-12 md:col-span-6">
          <Card className="h-full shadow-sm">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Provisioning Status
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Can Provision</span>
                  <Badge variant={orchestratorStatus?.provisioning?.can_provision ? 'success' : 'destructive'}>
                    {orchestratorStatus?.provisioning?.can_provision ? 'Yes' : 'No'}
                  </Badge>
                </div>
                <div className="flex items-center justify-between">
                  <span className="text-sm text-muted-foreground">Circuit Breaker</span>
                  <Badge variant={orchestratorStatus?.provisioning?.circuit_breaker_state === 'closed' ? 'success' : 'warning'}>
                    {orchestratorStatus?.provisioning?.circuit_breaker_state || 'Unknown'}
                  </Badge>
                </div>
                {orchestratorStatus?.provisioning?.blocked_reason && (
                  <p className="mt-2 text-xs text-muted-foreground">
                    {orchestratorStatus.provisioning.blocked_reason}
                  </p>
                )}
              </div>
            </CardContent>
          </Card>
        </div>
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
          `${orchUrl}/api/v1/streams/${encodeURIComponent(stream.id)}/extended-stats`,
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
          <span className="text-xs flex items-center gap-1 text-emerald-600 dark:text-emerald-400">
            <Download className="h-3 w-3" />
            {formatBytesPerSecond((stream.speed_down || 0) * 1024)}
          </span>
          <span className="text-xs flex items-center gap-1 text-rose-600 dark:text-rose-400">
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
    <div className="grid grid-cols-12 gap-4">
      <div className="col-span-12 md:col-span-6">
        <Card className="h-full shadow-sm">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Recent Streams
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
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
      </div>

      <div className="col-span-12 md:col-span-6">
        <Card className="h-full shadow-sm">
          <CardHeader className="p-4 pb-2">
            <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Recent Engines
            </CardTitle>
          </CardHeader>
          <CardContent className="p-4 pt-0">
            {recentEngines.length === 0 ? (
              <p className="text-sm text-muted-foreground">No engines available</p>
            ) : (
              <div className="space-y-2">
                {recentEngines.map((engine) => (
                  <div key={engine.container_id} className="flex items-center justify-between border-b border-border pb-2 last:border-0 hover:bg-muted/50 -mx-1 px-1 rounded-sm transition-colors">
                    <div className="flex-1 truncate">
                      <p className="text-sm font-medium truncate text-foreground">
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
    </div>
  )
}

export function OverviewPage({ engines, streams, vpnStatus, orchestratorStatus, orchUrl, apiKey }) {
  const healthyEngines = engines.filter(e => e.health_status === 'healthy').length

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Overview</h1>
          <p className="text-sm text-muted-foreground mt-1">Monitor your AceStream orchestrator system status</p>
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
