import { Activity, AlertTriangle, GitBranch, Globe, Server, ShieldCheck, Users } from 'lucide-react'
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

// Map country name/code to emoji flag
const countryToFlag = (country: string | null | undefined): string | null => {
  if (!country) return null
  const c = country.trim().toLowerCase()
  // Common country name -> ISO 3166-1 alpha-2, then to flag
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
  // Try direct match first
  let code = nameToCode[c]
  if (!code && c.length === 2) {
    code = c.toUpperCase()
  }
  if (!code) return null
  // Convert ISO to flag emoji
  const codePoints = [...code.toUpperCase()].map(
    (char) => 0x1f1e6 + char.charCodeAt(0) - 65
  )
  return String.fromCodePoint(...codePoints)
}

export function TopologyNode({ data, selected }: NodeProps<TopologyNodeData>) {
  const Icon = iconByKind[data.kind]
  const theme = kindTheme[data.kind]
  const showStreams = data.kind !== 'vpn'

  const vpnIp = data.kind === 'vpn' ? String(data.metadata?.publicIp || '') : null
  const vpnCountry = data.kind === 'vpn' ? String(data.metadata?.country || '') : null
  const vpnProvider = data.kind === 'vpn' ? String(data.metadata?.provider || '') : null
  const flag = countryToFlag(vpnCountry)

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
          )}>
            <Icon className="h-4 w-4 text-white" />
          </div>
          <div>
            <p className="text-sm font-black leading-tight text-white drop-shadow-sm">{data.title}</p>
            <p className="text-[10px] font-bold text-slate-300 mt-0.5 tracking-tight">{data.subtitle}</p>
          </div>
        </div>

        <Badge
          variant={data.health === 'down' ? 'destructive' : data.health === 'degraded' ? 'warning' : 'success'}
          className="text-[10px] uppercase tracking-wide"
        >
          {healthLabelByState[data.health]}
        </Badge>
      </div>

      {/* VPN: Show IP + Country flag + Provider instead of Streams */}
      {data.kind === 'vpn' && (
        <div className="rounded-md border border-white/10 bg-white/5 p-2 space-y-1.5">
          {vpnIp && (
            <div className="flex items-center gap-1.5">
              <span className="font-mono text-xs font-bold text-slate-100">{vpnIp}</span>
              {flag && <span className="text-sm ml-0.5">{flag}</span>}
            </div>
          )}
          {vpnProvider && (
            <p className="text-[10px] font-bold text-slate-200 uppercase tracking-widest">{vpnProvider}{vpnCountry ? ` · ${vpnCountry}` : ''}</p>
          )}

          <div className="mt-1 space-y-1.5 pt-1.5 border-t border-white/5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-1 text-[10px] uppercase text-slate-400">
                <Activity className="h-3 w-3 text-white" />
                <span>Bandwidth</span>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <div>
                <span className="text-[9px] uppercase text-slate-400 font-black block leading-none mb-0.5">Down</span>
                <span className="text-xs font-black text-emerald-400">{data.bandwidthMbps.toFixed(1)} <span className="text-[9px] font-normal text-emerald-500/70">Mbps</span></span>
              </div>
              <div className="w-px h-6 bg-white/10" />
              <div>
                <span className="text-[9px] uppercase text-slate-400 font-black block leading-none mb-0.5">Up</span>
                <span className="text-xs font-black text-sky-400">{(data.uploadMbps || 0).toFixed(1)} <span className="text-[9px] font-normal text-sky-500/70">Mbps</span></span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Proxy & Client: Show stream/client count */}
      {showStreams && (
        <div className="rounded-md border border-white/10 bg-white/5 p-2">
          <p className="mb-0.5 text-[10px] uppercase text-slate-300 font-semibold tracking-tight">
            {data.kind === 'client' ? 'Clients' : 'Streams'}
          </p>
          <p className="font-bold text-slate-100">{data.streamCount}</p>
        </div>
      )}

      {/* Proxy & Client: Bandwidth */}
      {data.kind !== 'engine' && data.kind !== 'vpn' && (
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
