import React from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Server, Activity, CheckCircle, ShieldCheck, Clock, TrendingUp, TrendingDown, AlertTriangle } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { cn } from '@/lib/utils'

function StatCard({ title, value, icon: Icon, trend, trendValue, variant = 'default' }) {
  const variantClasses = {
    default: 'text-primary',
    success: 'text-green-500',
    warning: 'text-yellow-500',
    error: 'text-red-500',
    info: 'text-blue-500',
  }

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center justify-between">
          <div className="space-y-1">
            <p className="text-sm text-muted-foreground">{title}</p>
            <div className="flex items-baseline gap-2">
              <p className="text-3xl font-bold">{value}</p>
              {trend && (
                <div className={cn("flex items-center gap-1 text-sm", trend === 'up' ? 'text-green-600' : 'text-red-600')}>
                  {trend === 'up' ? <TrendingUp className="h-4 w-4" /> : <TrendingDown className="h-4 w-4" />}
                  <span>{trendValue}</span>
                </div>
              )}
            </div>
          </div>
          <div className={cn("rounded-full p-3 bg-accent", variantClasses[variant])}>
            <Icon className="h-8 w-8" />
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function QuickStats({ engines, streams, vpnStatus, healthyEngines }) {
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
        value={vpnStatus.enabled ? (vpnStatus.connected ? 'Connected' : 'Disconnected') : 'Disabled'}
        icon={ShieldCheck}
        variant={vpnStatus.connected ? "success" : vpnStatus.enabled ? "error" : "default"}
      />
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

function RecentActivity({ streams, engines }) {
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
                <div key={stream.id} className="flex items-center justify-between border-b pb-2 last:border-0">
                  <div className="flex-1 truncate">
                    <p className="text-sm font-medium truncate">{stream.id.slice(0, 16)}...</p>
                    <p className="text-xs text-muted-foreground">{stream.content_key || 'N/A'}</p>
                  </div>
                  <Badge variant="success" className="ml-2">Active</Badge>
                </div>
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

export function OverviewPage({ engines, streams, vpnStatus, orchestratorStatus }) {
  const healthyEngines = engines.filter(e => e.health_status === 'healthy').length

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Overview</h1>
        <p className="text-muted-foreground">System status and recent activity</p>
      </div>

      <QuickStats
        engines={engines}
        streams={streams}
        vpnStatus={vpnStatus}
        healthyEngines={healthyEngines}
      />

      <SystemStatus vpnStatus={vpnStatus} orchestratorStatus={orchestratorStatus} />

      <RecentActivity streams={streams} engines={engines} />
    </div>
  )
}
