import React from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Server, PlayCircle, CheckCircle, ShieldCheck, Clock, KeyRound, AlertTriangle } from 'lucide-react'
import { cn } from '@/lib/utils'

const STATUS_ICON_COLOR = {
  default: 'text-muted-foreground',
  success: 'text-emerald-500',
  warning: 'text-amber-500',
  error:   'text-rose-500',
  info:    'text-sky-500',
}

function KPICard({ icon: Icon, value, label, status = 'default' }) {
  return (
    <Card className="shadow-sm">
      <CardContent className="p-4">
        <div className="flex items-center gap-3">
          <Icon className={cn('h-9 w-9 shrink-0', STATUS_ICON_COLOR[status])} />
          <div className="min-w-0">
            <div className="text-3xl font-bold tracking-tight text-foreground">{value}</div>
            <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</p>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function KPICards({
  totalEngines,
  activeStreams,
  healthyEngines,
  vpnStatus,
  orchestratorStatus,
  vpnLeaseSummary,
  lastUpdate,
}) {
  const runningEngines = Number(orchestratorStatus?.engines?.running ?? totalEngines ?? 0)
  const desiredEngines = Number(orchestratorStatus?.capacity?.used ?? runningEngines)
  const maxReplicas = Number(orchestratorStatus?.capacity?.max_replicas ?? orchestratorStatus?.capacity?.total ?? 0)

  const engineCapacityText = `${runningEngines} / ${desiredEngines}${maxReplicas > 0 ? ` (${maxReplicas})` : ''}`

  const leasePoolFromStatus = vpnStatus?.lease_summary || vpnStatus?.credentials_summary || null
  const leasePool = vpnLeaseSummary || leasePoolFromStatus || {}
  const leasedCredentials = Number(leasePool?.leased ?? leasePool?.active_leases ?? 0)
  const totalCredentials = Number(
    leasePool?.max_vpn_capacity
    ?? leasePool?.total
    ?? leasePool?.pool_size
    ?? leasePool?.credentials_total
    ?? 0,
  )
  const normalizedTotalCredentials = Math.max(totalCredentials, leasedCredentials)

  const canProvision = orchestratorStatus?.provisioning?.can_provision !== false
  const blockedReason = orchestratorStatus?.provisioning?.blocked_reason
  const recoveryEta = orchestratorStatus?.provisioning?.blocked_reason_details?.recovery_eta_seconds

  const vpnEnabled = Boolean(vpnStatus?.enabled)
  const vpnConnected = Boolean(vpnStatus?.connected)
  const vpnStatusText = vpnEnabled
    ? (vpnConnected ? 'Connected' : 'Disconnected')
    : 'Disabled'

  const vpnStatusVariant = !vpnEnabled ? 'default' : vpnConnected ? 'success' : 'error'

  return (
    <div className="space-y-4">
      {!canProvision && blockedReason && (
        <Alert variant="warning" className="border-amber-400/70 bg-amber-500/10">
          <AlertTriangle className="h-4 w-4 text-amber-400" />
          <AlertTitle>Provisioning Blocked</AlertTitle>
          <AlertDescription>
            {blockedReason}
            {typeof recoveryEta === 'number' ? ` Recovery ETA: ${recoveryEta}s.` : ''}
          </AlertDescription>
        </Alert>
      )}

      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-12 sm:col-span-6 xl:col-span-3">
          <KPICard icon={Server} value={engineCapacityText} label="Engine Capacity (Actual / Desired (Max))" status="default" />
        </div>
        <div className="col-span-12 sm:col-span-6 xl:col-span-2">
          <KPICard icon={PlayCircle} value={activeStreams} label="Active Streams" status="info" />
        </div>
        <div className="col-span-12 sm:col-span-6 xl:col-span-2">
          <KPICard icon={CheckCircle} value={healthyEngines} label="Healthy Engines" status="success" />
        </div>
        <div className="col-span-12 sm:col-span-6 xl:col-span-2">
          <KPICard icon={ShieldCheck} value={vpnStatusText} label="VPN Status" status={vpnStatusVariant} />
        </div>
        <div className="col-span-12 sm:col-span-6 xl:col-span-1">
          <KPICard icon={KeyRound} value={`${leasedCredentials} / ${normalizedTotalCredentials}`} label="VPN Credentials" status={leasedCredentials > 0 ? 'warning' : 'default'} />
        </div>
        <div className="col-span-12 sm:col-span-6 xl:col-span-2">
          <KPICard
            icon={Clock}
            value={lastUpdate ? lastUpdate.toLocaleTimeString() : 'Never'}
            label="Last Update"
            status="default"
          />
        </div>
      </div>
    </div>
  )
}

export default KPICards
