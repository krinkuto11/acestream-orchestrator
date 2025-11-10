import React from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Server, PlayCircle, CheckCircle, ShieldCheck, Clock } from 'lucide-react'

function KPICard({ icon: Icon, value, label, variant = 'default' }) {
  const variantClasses = {
    default: 'text-primary',
    secondary: 'text-green-500',
    success: 'text-emerald-500',
    warning: 'text-yellow-500',
    info: 'text-blue-500',
  }

  return (
    <Card>
      <CardContent className="pt-6">
        <div className="flex items-center gap-3">
          <Icon className={`h-10 w-10 ${variantClasses[variant]}`} />
          <div>
            <div className="text-3xl font-bold">{value}</div>
            <p className="text-sm text-muted-foreground">{label}</p>
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

  return (
    <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
      <KPICard icon={Server} value={totalEngines} label="Engines" variant="default" />
      <KPICard icon={PlayCircle} value={activeStreams} label="Active Streams" variant="secondary" />
      <KPICard icon={CheckCircle} value={healthyEngines} label="Healthy Engines" variant="success" />
      <KPICard icon={ShieldCheck} value={vpnStatusText} label="VPN Status" variant={vpnStatus.connected ? "success" : "warning"} />
      <KPICard 
        icon={Clock} 
        value={lastUpdate ? lastUpdate.toLocaleTimeString() : 'Never'} 
        label="Last Update" 
        variant="info" 
      />
    </div>
  )
}

export default KPICards
