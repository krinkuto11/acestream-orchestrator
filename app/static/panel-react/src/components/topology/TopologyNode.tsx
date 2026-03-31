import { Activity, AlertTriangle, GitBranch, Server, ShieldCheck, Users } from 'lucide-react'
import { Handle, Position, type NodeProps } from 'reactflow'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { TopologyNodeData } from '@/stores/topologyStore'

const iconByKind = {
  vpn: ShieldCheck,
  engine: Server,
  proxy: GitBranch,
  client: Users,
}

const healthClassByState = {
  healthy: 'border-emerald-500/60 bg-emerald-500/10 text-emerald-100',
  degraded: 'border-amber-500/60 bg-amber-500/15 text-amber-100',
  down: 'border-rose-500/70 bg-rose-500/15 text-rose-100',
}

const healthLabelByState = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  down: 'Down',
}

const kindLabelByNode = {
  vpn: 'VPN',
  engine: 'Engine',
  proxy: 'Proxy',
  client: 'Client',
}

export function TopologyNode({ data, selected }: NodeProps<TopologyNodeData>) {
  const Icon = iconByKind[data.kind]

  return (
    <div
      className={cn(
        'relative min-w-[210px] rounded-xl border bg-slate-950/80 p-3 shadow-lg backdrop-blur-sm transition-all',
        healthClassByState[data.health],
        selected && 'ring-2 ring-sky-400/90',
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2.5 !w-2.5 !border-2 !border-slate-100 !bg-slate-900"
      />

      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className="rounded-md bg-black/35 p-1.5">
            <Icon className="h-4 w-4" />
          </div>
          <div>
            <p className="text-sm font-semibold leading-tight">{data.title}</p>
            <p className="text-xs text-slate-300">{data.subtitle}</p>
          </div>
        </div>

        <Badge
          variant={data.health === 'down' ? 'destructive' : data.health === 'degraded' ? 'warning' : 'success'}
          className="text-[10px] uppercase tracking-wide"
        >
          {healthLabelByState[data.health]}
        </Badge>
      </div>

        <div className="rounded-md border border-white/10 bg-white/5 p-2">
          <p className="mb-0.5 text-[10px] uppercase text-slate-300 font-semibold tracking-tight">Streams</p>
          <p className="font-bold text-slate-100">{data.streamCount}</p>
        </div>

      {data.kind !== 'engine' && (
        <div className="mt-2 rounded-md border border-white/10 bg-slate-900/60 p-2">
          <div className="mb-1 flex items-center gap-1 text-[10px] uppercase text-slate-300">
            <Activity className="h-3 w-3" />
            <span>Bandwidth</span>
          </div>
          <p className="text-sm font-semibold text-slate-50">{data.bandwidthMbps.toFixed(1)} Mbps</p>
        </div>
      )}

      {data.failoverActive && (
        <div className="mt-2 flex items-center gap-1 rounded-md border border-amber-500/40 bg-amber-500/10 px-2 py-1 text-[11px] text-amber-200">
          <AlertTriangle className="h-3.5 w-3.5" />
          <span>Failover route active</span>
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        className="!h-2.5 !w-2.5 !border-2 !border-slate-100 !bg-slate-900"
      />
    </div>
  )
}
