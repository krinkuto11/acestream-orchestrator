import { AlertTriangle, GitBranch, Server, ShieldCheck, Users } from 'lucide-react'
import { Handle, Position, type NodeProps } from 'reactflow'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { TopologyNodeData } from '@/stores/topologyStore'

const formatBytes = (bytes: number, decimals = 2) => {
  if (bytes === 0) return '0 Bytes'
  const k = 1024
  const dm = decimals < 0 ? 0 : decimals
  const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i]
}

const iconByKind = {
  vpn: ShieldCheck,
  engine: Server,
  proxy: GitBranch,
  client: Users,
}

// Map country name/code to emoji flag
const countryToFlag = (country: string | null | undefined): string | null => {
  if (!country) return null
  const c = country.trim().toLowerCase()
  const nameToCode: Record<string, string> = {
    'united states': 'US', 'usa': 'US', 'us': 'US',
    'united kingdom': 'GB', 'uk': 'GB', 'gb': 'GB',
    'germany': 'DE', 'de': 'DE',
    'france': 'FR', 'fr': 'FR',
    'netherlands': 'NL', 'nl': 'NL',
    'switzerland': 'CH', 'ch': 'CH',
    'sweden': 'SE', 'se': 'SE',
    'spain': 'ES', 'es': 'ES',
    'italy': 'IT', 'it': 'IT',
    'canada': 'CA', 'ca': 'CA',
    'australia': 'AU', 'au': 'AU',
    'japan': 'JP', 'jp': 'JP',
    'singapore': 'SG', 'sg': 'SG',
    'ireland': 'IE', 'ie': 'IE',
    'romania': 'RO', 'ro': 'RO',
    'iceland': 'IS', 'is': 'IS',
    'norway': 'NO', 'no': 'NO',
    'finland': 'FI', 'fi': 'FI',
    'denmark': 'DK', 'dk': 'DK',
    'portugal': 'PT', 'pt': 'PT',
    'czech republic': 'CZ', 'czechia': 'CZ', 'cz': 'CZ',
    'poland': 'PL', 'pl': 'PL',
    'austria': 'AT', 'at': 'AT',
    'belgium': 'BE', 'be': 'BE',
    'brazil': 'BR', 'br': 'BR',
    'hong kong': 'HK', 'hk': 'HK',
    'india': 'IN', 'in': 'IN',
    'mexico': 'MX', 'mx': 'MX',
    'south korea': 'KR', 'kr': 'KR',
    'luxembourg': 'LU', 'lu': 'LU',
    'hungary': 'HU', 'hu': 'HU',
    'bulgaria': 'BG', 'bg_country': 'BG',
  }
  let code = nameToCode[c]
  if (!code && c.length === 2) {
    code = c.toUpperCase()
  }
  if (!code) return null
  const codePoints = [...code.toUpperCase()].map(
    (char) => 0x1f1e6 + char.charCodeAt(0) - 65
  )
  return String.fromCodePoint(...codePoints)
}

// Enterprise color mapping per section. 
// Uses a high-contrast background with sharp borders and highly readable text.
const themeByKind = {
  vpn: {
    wrapper: 'border-indigo-400 bg-indigo-950/90',
    title: 'text-indigo-50',
    subtitle: 'text-indigo-200',
    iconBg: 'bg-indigo-500/30 text-indigo-100',
    box: 'border-indigo-500/30 bg-indigo-900/60 text-indigo-100',
    label: 'text-indigo-300'
  },
  engine: {
    wrapper: 'border-blue-400 bg-blue-900/90',
    title: 'text-blue-50',
    subtitle: 'text-blue-200',
    iconBg: 'bg-blue-500/30 text-blue-100',
    box: 'border-blue-500/30 bg-blue-800/60 text-blue-100',
    label: 'text-blue-300'
  },
  proxy: {
    wrapper: 'border-fuchsia-400 bg-fuchsia-950/90',
    title: 'text-fuchsia-50',
    subtitle: 'text-fuchsia-200',
    iconBg: 'bg-fuchsia-500/30 text-fuchsia-100',
    box: 'border-fuchsia-500/30 bg-fuchsia-900/60 text-fuchsia-100',
    label: 'text-fuchsia-300'
  },
  client: {
    wrapper: 'border-teal-400 bg-teal-950/90',
    title: 'text-teal-50',
    subtitle: 'text-teal-200',
    iconBg: 'bg-teal-500/30 text-teal-100',
    box: 'border-teal-500/30 bg-teal-900/60 text-teal-100',
    label: 'text-teal-300'
  },
}

const healthClassByState = {
  healthy: '',
  degraded: 'ring-1 ring-amber-400',
  down: 'opacity-70 grayscale-[30%] ring-1 ring-rose-500',
}

const healthLabelByState = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  down: 'Down',
}

export function TopologyNode({ data, selected }: NodeProps<TopologyNodeData>) {
  const Icon = iconByKind[data.kind] || Server
  const theme = themeByKind[data.kind] || themeByKind.engine

  const vpnIp = data.kind === 'vpn' ? String(data.metadata?.publicIp || '') : null
  const vpnCountry = data.kind === 'vpn' ? String(data.metadata?.country || '') : null
  const vpnProvider = data.kind === 'vpn' ? String(data.metadata?.provider || '') : null
  const flag = countryToFlag(vpnCountry)

  return (
    <div
      className={cn(
        'relative min-w-[210px] rounded-xl border p-3 shadow-2xl transition-all',
        theme.wrapper,
        healthClassByState[data.health],
        selected && 'ring-2 ring-sky-400 shadow-sky-500/20',
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2.5 !w-2.5 !border-2 !border-slate-800 !bg-slate-300"
      />

      {/* Floating Proxy/Mux Ingress Bitrate Block (Outside of Node) */}
      {data.kind === 'proxy' && data.bandwidthMbps > 0 && (
        <div className="absolute -top-6 left-1/2 -translate-x-1/2 px-2 py-0.5 rounded-md border border-slate-600 bg-slate-900 text-[11px] font-semibold text-slate-100 shadow-lg whitespace-nowrap z-10">
          {data.bandwidthMbps.toFixed(1)} <span className="text-[10px] text-slate-400 font-normal ml-0.5">Mbps</span>
        </div>
      )}

      {/* Node Header */}
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="flex items-center gap-2">
          <div className={cn("rounded-md p-1.5 shadow-sm", theme.iconBg)}>
            <Icon className="h-4 w-4" />
          </div>
          <div className="max-w-[130px]">
            <p className={cn("text-sm font-semibold leading-tight truncate", theme.title)} title={data.title}>
              {data.title}
            </p>
            <p className={cn("text-[10px] font-medium truncate", theme.subtitle)} title={data.subtitle}>
              {data.subtitle}
            </p>
          </div>
        </div>

        <Badge
          variant={data.health === 'down' ? 'destructive' : data.health === 'degraded' ? 'warning' : 'outline'}
          className={cn(
            "text-[10px] font-semibold uppercase",
            data.health === 'healthy' && `border-emerald-500/40 text-emerald-400 bg-emerald-500/10`
          )}
        >
          {healthLabelByState[data.health]}
        </Badge>
      </div>

      <div className="flex flex-col gap-1.5">
        {/* VPN Specific Details Restored */}
        {data.kind === 'vpn' && (
          <div className="rounded-lg border border-indigo-500/30 bg-indigo-900/40 p-2 space-y-1.5 mb-1.5">
            {vpnIp && (
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] font-semibold text-indigo-100">{vpnIp}</span>
                {flag && <span className="text-sm shadow-sm">{flag}</span>}
              </div>
            )}
            {vpnProvider && (
              <p className="text-[10px] font-medium text-indigo-300/90 leading-none">
                {vpnProvider}{vpnCountry ? ` · ${vpnCountry}` : ''}
              </p>
            )}
          </div>
        )}

        {data.kind !== 'client' && (
          <div className={cn("flex items-center justify-between rounded-md border p-1.5 px-2 shadow-sm", theme.box)}>
            <span className={cn("text-[10px] uppercase font-semibold", theme.label)}>Streams</span>
            <span className="text-xs font-semibold">{data.streamCount}</span>
          </div>
        )}

        {/* Standard Bandwidth Block - Restored Upload/Download for VPN */}
        {data.kind === 'vpn' ? (
          <div className={cn("grid grid-cols-2 gap-1.5", theme.box)}>
            <div className="flex flex-col p-1 px-1.5 rounded bg-black/20">
              <span className="text-[8px] text-emerald-400 font-bold uppercase leading-none mb-1">Down</span>
              <div className="flex items-baseline gap-0.5">
                <span className="text-xs font-semibold">{data.bandwidthMbps.toFixed(1)}</span>
                <span className="text-[8px] text-slate-400">Mbps</span>
              </div>
            </div>
            <div className="flex flex-col p-1 px-1.5 rounded bg-black/20">
              <span className="text-[8px] text-rose-400 font-bold uppercase leading-none mb-1">Up</span>
              <div className="flex items-baseline gap-0.5">
                <span className="text-xs font-semibold">{(data.uploadMbps || 0).toFixed(1)}</span>
                <span className="text-[8px] text-slate-400">Mbps</span>
              </div>
            </div>
          </div>
        ) : data.kind === 'client' && (
          <div className={cn("flex items-center justify-between rounded-md border p-1.5 px-2 shadow-sm", theme.box)}>
            <span className={cn("text-[10px] uppercase font-semibold", theme.label)}>Total Sent</span>
            <span className="text-xs font-semibold truncate max-w-[100px] text-right">
              {formatBytes(Number(data.metadata?.totalBytes || 0))}
            </span>
          </div>
        )}
      </div>

      {data.failoverActive && (
        <div className="mt-2 flex items-center gap-1.5 rounded-md border border-amber-400/40 bg-amber-500/20 px-2 py-1.5 text-[11px] font-semibold text-amber-300 shadow-md">
          <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
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
