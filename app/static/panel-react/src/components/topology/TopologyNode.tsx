import { Activity, AlertTriangle, GitBranch, Globe, Server, ShieldCheck, Users } from 'lucide-react'
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

// Enterprise-style color themes (professional slates and muted accents)
const kindTheme = {
  vpn: {
    bg: 'bg-slate-900',
    border: 'border-indigo-500/30',
    iconBg: 'bg-indigo-500/10',
    iconText: 'text-indigo-400',
  },
  engine: {
    bg: 'bg-slate-900',
    border: 'border-blue-500/30',
    iconBg: 'bg-blue-500/10',
    iconText: 'text-blue-400',
  },
  proxy: {
    bg: 'bg-slate-900',
    border: 'border-teal-500/30',
    iconBg: 'bg-teal-500/10',
    iconText: 'text-teal-400',
  },
  client: {
    bg: 'bg-slate-900',
    border: 'border-amber-500/30',
    iconBg: 'bg-amber-500/10',
    iconText: 'text-amber-400',
  },
}

const healthLabelByState = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  down: 'Down',
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

export function TopologyNode({ data, selected }: NodeProps<TopologyNodeData>) {
  const Icon = iconByKind[data.kind]
  const theme = kindTheme[data.kind]

  const vpnIp = data.kind === 'vpn' ? String(data.metadata?.publicIp || '') : null
  const vpnCountry = data.kind === 'vpn' ? String(data.metadata?.country || '') : null
  const vpnProvider = data.kind === 'vpn' ? String(data.metadata?.provider || '') : null
  const flag = countryToFlag(vpnCountry)

  return (
    <div
      className={cn(
        'relative min-w-[220px] rounded-xl border p-4 shadow-xl transition-all duration-300',
        theme.bg,
        theme.border,
        selected ? 'ring-2 ring-blue-500/50 scale-[1.02]' : 'hover:border-slate-400/40',
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2.5 !w-2.5 !border-2 !border-slate-800 !bg-slate-400"
      />
      
      {/* Professional Floating Aggregate Label for Proxy node */}
      {data.kind === 'proxy' && (
        <div className="absolute -left-[145px] top-1/2 -translate-y-[calc(100%+8px)] flex flex-col items-center gap-1.5 nodrag nopan">
          <div className="px-2 py-1 rounded-md border border-teal-500/30 bg-slate-900 shadow-lg flex items-center gap-2">
            <span className="text-[12px] font-semibold text-teal-400 tabular-nums">
              {data.bandwidthMbps.toFixed(1)}
            </span>
            <span className="text-[10px] text-slate-500 font-medium">Mbps</span>
          </div>
          <span className="text-[9px] font-semibold text-slate-500 uppercase tracking-wider bg-slate-800/50 px-1.5 py-0.5 rounded border border-slate-700/50">
            Total Ingress
          </span>
        </div>
      )}

      <div className="mb-4 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className={cn(
            "rounded-lg p-2 shadow-sm transition-colors",
            theme.iconBg,
            theme.iconText
          )}>
            <Icon className="h-4 w-4" />
          </div>
          <div>
            <p className="text-sm font-semibold text-slate-100">{data.title}</p>
            <p className="text-[11px] font-medium text-slate-400 line-clamp-1">{data.subtitle}</p>
          </div>
        </div>

        <Badge
          variant={data.health === 'down' ? 'destructive' : data.health === 'degraded' ? 'warning' : 'outline'}
          className={cn(
            "text-[10px] font-semibold px-1.5 py-0",
            data.health === 'healthy' && "border-emerald-500/30 text-emerald-400 bg-emerald-500/5"
          )}
        >
          {healthLabelByState[data.health]}
        </Badge>
      </div>

      {/* VPN Details */}
      {data.kind === 'vpn' && (
        <div className="rounded-lg border border-slate-700/50 bg-slate-800/30 p-3 space-y-2">
          {vpnIp && (
            <div className="flex items-center gap-2">
              <span className="font-mono text-[12px] font-semibold text-slate-200">{vpnIp}</span>
              {flag && <span className="text-lg">{flag}</span>}
            </div>
          )}
          {vpnProvider && (
            <p className="text-[11px] font-medium text-slate-400">
              {vpnProvider}{vpnCountry ? ` · ${vpnCountry}` : ''}
            </p>
          )}

          <div className="flex items-center gap-6 pt-2 border-t border-slate-700/50">
            <div className="flex items-baseline gap-1.5">
              <span className="text-[10px] text-slate-500 font-bold">↓</span>
              <span className="text-lg font-semibold text-emerald-400 tabular-nums">{data.bandwidthMbps.toFixed(1)}</span>
              <span className="text-[10px] text-slate-500 font-medium">Mbps</span>
            </div>
            <div className="flex items-baseline gap-1.5">
              <span className="text-[10px] text-slate-500 font-bold">↑</span>
              <span className="text-lg font-semibold text-rose-400 tabular-nums">{(data.uploadMbps || 0).toFixed(1)}</span>
              <span className="text-[10px] text-slate-500 font-medium">Mbps</span>
            </div>
          </div>
        </div>
      )}

      {/* Client Details */}
      {data.kind === 'client' && (
        <div className="rounded-lg border border-slate-700/50 bg-slate-800/30 p-3 space-y-3">
          <div className="flex items-center justify-between">
            <span className="font-mono text-lg font-semibold text-slate-100">{data.title}</span>
            <Badge variant="outline" className="border-slate-700 text-[10px] text-slate-400 bg-slate-800/50">
              {String(data.metadata?.type || 'TS')}
            </Badge>
          </div>
          
          <div className="flex items-center justify-between pt-2 border-t border-slate-700/50">
            <div className="flex items-baseline gap-1.5">
               <span className="text-sm font-semibold text-slate-100 tabular-nums">{data.bandwidthMbps.toFixed(2)}</span>
               <span className="text-[10px] text-slate-500 font-medium tracking-tight">Mbps</span>
            </div>
            <span className="text-[10px] font-medium text-slate-500">
              {String(data.metadata?.connectedAt || '')}
            </span>
          </div>
        </div>
      )}

      {/* Proxy & Engine Metrics */}
      {(data.kind === 'proxy' || data.kind === 'engine') && (
        <div className="rounded-lg border border-slate-700/50 bg-slate-800/30 p-2.5 flex items-center justify-between">
          <span className="text-[11px] font-medium text-slate-400">Active Streams</span>
          <span className="text-sm font-semibold text-slate-100">{data.streamCount}</span>
        </div>
      )}

      {data.failoverActive && (
        <div className="mt-3 flex items-center gap-2 rounded-lg border border-amber-500/20 bg-amber-500/5 px-2.5 py-1.5 text-[11px] font-medium text-amber-200">
          <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
          <span>Active Failover Route</span>
        </div>
      )}

      <Handle
        type="source"
        position={Position.Right}
        className="!h-2.5 !w-2.5 !border-2 !border-slate-800 !bg-slate-400"
      />
    </div>
  )
}
