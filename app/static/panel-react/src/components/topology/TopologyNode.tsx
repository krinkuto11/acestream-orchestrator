import { AlertTriangle, GitBranch, Server, ShieldCheck, Users } from 'lucide-react'
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

// Enterprise color mapping per section. 
// Uses a muted background with sharp borders and highly readable text.
const themeByKind = {
  vpn: {
    wrapper: 'border-indigo-500/40 bg-indigo-950/50',
    title: 'text-indigo-50',
    subtitle: 'text-indigo-200/80',
    iconBg: 'bg-indigo-500/20 text-indigo-300',
    box: 'border-indigo-500/20 bg-indigo-900/30 text-indigo-100',
    label: 'text-indigo-300/80'
  },
  engine: {
    wrapper: 'border-blue-500/40 bg-blue-950/50',
    title: 'text-blue-50',
    subtitle: 'text-blue-200/80',
    iconBg: 'bg-blue-500/20 text-blue-300',
    box: 'border-blue-500/20 bg-blue-900/30 text-blue-100',
    label: 'text-blue-300/80'
  },
  proxy: {
    wrapper: 'border-fuchsia-500/40 bg-fuchsia-950/50',
    title: 'text-fuchsia-50',
    subtitle: 'text-fuchsia-200/80',
    iconBg: 'bg-fuchsia-500/20 text-fuchsia-300',
    box: 'border-fuchsia-500/20 bg-fuchsia-900/30 text-fuchsia-100',
    label: 'text-fuchsia-300/80'
  },
  client: {
    wrapper: 'border-teal-500/40 bg-teal-950/50',
    title: 'text-teal-50',
    subtitle: 'text-teal-200/80',
    iconBg: 'bg-teal-500/20 text-teal-300',
    box: 'border-teal-500/20 bg-teal-900/30 text-teal-100',
    label: 'text-teal-300/80'
  },
}

const healthClassByState = {
  healthy: '',
  degraded: 'ring-1 ring-amber-500/50',
  down: 'opacity-70 grayscale-[30%] ring-1 ring-rose-500/50',
}

const healthLabelByState = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  down: 'Down',
}

export function TopologyNode({ data, selected }: NodeProps<TopologyNodeData>) {
  const Icon = iconByKind[data.kind] || Server
  const theme = themeByKind[data.kind] || themeByKind.engine

  return (
    <div
      className={cn(
        'relative min-w-[210px] rounded-xl border p-3 shadow-sm backdrop-blur-md transition-all',
        theme.wrapper,
        healthClassByState[data.health],
        selected && 'ring-2 ring-sky-400/90 shadow-md',
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2.5 !w-2.5 !border-2 !border-slate-800 !bg-slate-300"
      />

      {/* Floating Proxy/Mux Ingress Bitrate Block (Outside of Node) */}
      {data.kind === 'proxy' && data.bandwidthMbps > 0 && (
        <div className="absolute -top-6 left-1/2 -translate-x-1/2 px-2 py-0.5 rounded-md border border-slate-700 bg-slate-900 text-[11px] font-medium text-slate-400 shadow-sm whitespace-nowrap z-10">
          {data.bandwidthMbps.toFixed(1)} <span className="text-[10px] text-slate-500 font-normal ml-0.5">Mbps</span>
        </div>
      )}

      {/* Node Header (IP & User-Agent for clients sit ONLY here now) */}
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className={cn("rounded-md p-1.5 shadow-sm", theme.iconBg)}>
            <Icon className="h-4 w-4" />
          </div>
          <div className="max-w-[130px]">
            {/* Title usually holds the IP for clients */}
            <p className={cn("text-sm font-semibold leading-tight truncate", theme.title)} title={data.title}>
              {data.title}
            </p>
            {/* Subtitle holds the User Agent or Port data */}
            <p className={cn("text-[10px] font-medium truncate", theme.subtitle)} title={data.subtitle}>
              {data.subtitle}
            </p>
          </div>
        </div>

        <Badge
          variant={data.health === 'down' ? 'destructive' : data.health === 'degraded' ? 'warning' : 'outline'}
          className={cn(
            "text-[10px] font-medium uppercase",
            data.health === 'healthy' && `border-transparent bg-transparent ${theme.title}`
          )}
        >
          {healthLabelByState[data.health]}
        </Badge>
      </div>

      <div className="flex flex-col gap-1.5">
        <div className={cn("flex items-center justify-between rounded-md border p-1.5 px-2", theme.box)}>
          <span className={cn("text-[10px] uppercase font-semibold", theme.label)}>Streams</span>
          <span className="text-xs font-semibold">{data.streamCount}</span>
        </div>

        {/* Standard Bandwidth Block - Excludes Proxy since it was moved outside above */}
        {data.kind !== 'engine' && data.kind !== 'proxy' && (
          <div className={cn("flex items-center justify-between rounded-md border p-1.5 px-2", theme.box)}>
            <span className={cn("text-[10px] uppercase font-semibold", theme.label)}>Bandwidth</span>
            <span className="text-xs font-semibold">
              {data.bandwidthMbps.toFixed(1)} <span className="text-[9px] font-normal opacity-80">Mbps</span>
            </span>
          </div>
        )}
      </div>

      {data.failoverActive && (
        <div className="mt-2 flex items-center gap-1.5 rounded-md border border-amber-500/30 bg-amber-500/10 px-2 py-1.5 text-[11px] font-medium text-amber-400">
          <AlertTriangle className="h-3.5 w-3.5" />
          <span>Failover Active</span>
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        className="!h-2.5 !w-2.5 !border-2 !border-slate-800 !bg-slate-300"
      />
    </div>
  )
}
