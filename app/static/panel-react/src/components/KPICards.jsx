import React from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Server, PlayCircle, CheckCircle, ShieldCheck, Clock } from 'lucide-react'
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

function KPICards({ totalEngines, activeStreams, healthyEngines, vpnStatus, lastUpdate }) {
  const vpnStatusText = vpnStatus.enabled
    ? (vpnStatus.connected ? 'Connected' : 'Disconnected')
    : 'Disabled'

  const vpnStatusVariant = !vpnStatus.enabled ? 'default' : vpnStatus.connected ? 'success' : 'error'

  return (
    <div className="grid grid-cols-12 gap-4">
      <div className="col-span-12 sm:col-span-6 xl:col-span-2">
        <KPICard icon={Server} value={totalEngines} label="Engines" status="default" />
      </div>
      <div className="col-span-12 sm:col-span-6 xl:col-span-3">
        <KPICard icon={PlayCircle} value={activeStreams} label="Active Streams" status="info" />
      </div>
      <div className="col-span-12 sm:col-span-6 xl:col-span-3">
        <KPICard icon={CheckCircle} value={healthyEngines} label="Healthy Engines" status="success" />
      </div>
      <div className="col-span-12 sm:col-span-6 xl:col-span-2">
        <KPICard icon={ShieldCheck} value={vpnStatusText} label="VPN Status" status={vpnStatusVariant} />
      </div>
      <div className="col-span-12 xl:col-span-2">
        <KPICard
          icon={Clock}
          value={lastUpdate ? lastUpdate.toLocaleTimeString() : 'Never'}
          label="Last Update"
          status="default"
        />
      </div>
    </div>
  )
}

export default KPICards
