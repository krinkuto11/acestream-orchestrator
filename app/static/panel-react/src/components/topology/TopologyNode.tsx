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

// Kind-based color themes (opaque hex backgrounds for full pipe occlusion)
const kindTheme = {
  vpn: {
    bg: '#1e1b4b',        // indigo-950
    border: '#7c3aed',    // violet-500
    iconBg: 'bg-violet-500/25',
    iconText: 'text-violet-200',
  },
  engine: {
    bg: '#0c1a3d',        // custom deep blue
    border: '#3b82f6',    // blue-500
    iconBg: 'bg-blue-500/25',
    iconText: 'text-blue-200',
  },
  proxy: {
    bg: '#042f2e',        // teal-950
    border: '#14b8a6',    // teal-500
    iconBg: 'bg-teal-500/25',
    iconText: 'text-teal-200',
  },
  client: {
    bg: '#271505',        // custom deep orange
    border: '#f59e0b',    // amber-500
    iconBg: 'bg-amber-500/25',
    iconText: 'text-amber-200',
  },
}

const healthLabelByState = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  down: 'Down',
}

export function TopologyNode({ data, selected }: NodeProps<TopologyNodeData>) {
  const Icon = iconByKind[data.kind]
  const theme = kindTheme[data.kind]

  return (
    <div
      className={cn(
        'relative min-w-[210px] rounded-xl p-3 shadow-lg transition-all',
        selected && 'ring-2 ring-sky-400/90',
      )}
      style={{
        backgroundColor: theme.bg,
        border: `1.5px solid ${theme.border}`,
        borderLeftWidth: '4px',
        borderLeftColor: theme.border,
      }}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2.5 !w-2.5 !border-2 !border-slate-100 !bg-slate-900"
      />

      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className={cn(
            "rounded-md p-1.5 shadow-sm",
            theme.iconBg,
            theme.iconText,
          )}>
            <Icon className="h-4 w-4" />
          </div>
          <div>
            <p className="text-sm font-black leading-tight text-white drop-shadow-sm">{data.title}</p>
            <p className="text-[10px] font-bold text-slate-200 tracking-tight">{data.subtitle}</p>
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
        <div className="mt-2 rounded-md border border-white/10 bg-black/20 p-2">
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
