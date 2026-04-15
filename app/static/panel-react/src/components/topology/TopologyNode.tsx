import { AlertTriangle, GitBranch, Server, ShieldCheck, Timer, Users, Zap } from 'lucide-react'
import { Handle, Position, type NodeProps } from 'reactflow'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import { formatThroughputDual, type TopologyNodeData } from '@/stores/topologyStore'

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

const themeByKind = {
  vpn: {
    wrapper: 'border-indigo-500 bg-[#1e1b4b]', // solid indigo-950
    title: 'text-indigo-50',
    subtitle: 'text-indigo-200',
    iconBg: 'bg-indigo-600 text-indigo-100',
    box: 'border-indigo-600 bg-[#312e81] text-indigo-100', // solid indigo-900
    label: 'text-indigo-300'
  },
  engine: {
    wrapper: 'border-blue-500 bg-[#172554]', // solid blue-950
    title: 'text-blue-50',
    subtitle: 'text-blue-200',
    iconBg: 'bg-blue-600 text-blue-100',
    box: 'border-blue-600 bg-[#1e3a8a] text-blue-100', // solid blue-900
    label: 'text-blue-300'
  },
  proxy: {
    wrapper: 'border-fuchsia-500 bg-[#4a044e]', // solid fuchsia-950
    title: 'text-fuchsia-50',
    subtitle: 'text-fuchsia-200',
    iconBg: 'bg-fuchsia-600 text-fuchsia-100',
    box: 'border-fuchsia-600 bg-[#701a75] text-fuchsia-100', // solid fuchsia-900
    label: 'text-fuchsia-300'
  },
  client: {
    wrapper: 'border-teal-500 bg-[#042f2e]', // solid teal-950
    title: 'text-teal-50',
    subtitle: 'text-teal-200',
    iconBg: 'bg-teal-600 text-teal-100',
    box: 'border-teal-600 bg-[#134e4a] text-teal-100', // solid teal-900
    label: 'text-teal-300'
  },
}

const healthClassByState = {
  healthy: '',
  degraded: 'ring-1 ring-amber-400',
  down: 'brightness-90 grayscale-[30%] ring-1 ring-rose-500',
}

const healthLabelByState = {
  healthy: 'Healthy',
  degraded: 'Degraded',
  down: 'Down',
}

export function TopologyNode({ data, selected }: NodeProps<TopologyNodeData>) {
  const Icon = iconByKind[data.kind] || Server
  const theme = themeByKind[data.kind] || themeByKind.engine
  const isDraining = data.lifecycle === 'draining'

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
        isDraining && 'border-amber-400 border-dashed opacity-85',
        selected && 'ring-2 ring-sky-400 shadow-sky-500/20',
      )}
    >
      <Handle
        type="target"
        position={Position.Left}
        className="!h-2.5 !w-2.5 !border-2 !border-slate-800 !bg-slate-300"
      />

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

        <div className="flex flex-col items-end gap-1">
          <Badge
            variant={data.health === 'down' ? 'destructive' : data.health === 'degraded' ? 'warning' : 'outline'}
            className={cn(
              "text-[10px] font-semibold uppercase",
              data.health === 'healthy' && `border-emerald-500/40 text-emerald-400 bg-emerald-500/10`
            )}
          >
            {healthLabelByState[data.health]}
          </Badge>
          {data.kind === 'engine' && data.forwarded && (
            <Badge variant="outline" className="h-5 gap-1 border-amber-400/60 bg-amber-500/10 px-1.5 text-[9px] font-semibold text-amber-200">
              <Zap className="h-2.5 w-2.5" />
              Forwarded
            </Badge>
          )}
          {isDraining && (
            <Badge variant="warning" className="gap-1 border-amber-400/60 bg-amber-500/15 text-[10px] font-semibold uppercase text-amber-200">
              <Timer className="h-3 w-3" />
              Draining
            </Badge>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        {/* VPN Specific Details Restored */}
        {data.kind === 'vpn' && (
          <div className="rounded-lg border border-indigo-600 bg-[#312e81] p-2 space-y-1.5 mb-1.5">
            {vpnIp && (
              <div className="flex items-center gap-2">
                <span className="font-mono text-[11px] font-semibold text-indigo-100">{vpnIp}</span>
                {flag && <span className="text-sm shadow-sm">{flag}</span>}
              </div>
            )}
            {vpnProvider && (
              <p className="text-[10px] font-medium text-indigo-300 leading-none">
                {vpnProvider}{vpnCountry ? ` · ${vpnCountry}` : ''}
              </p>
            )}
          </div>
        )}

        {data.kind === 'engine' && (
          <div className={cn("flex items-center justify-between rounded-md border p-1.5 px-2 shadow-sm", theme.box)}>
            <span className={cn("text-[10px] uppercase font-semibold", theme.label)}>Streams</span>
            <span className="text-xs font-semibold">{data.streamCount}</span>
          </div>
        )}

        {/* Standard Bandwidth Block - Restored Upload/Download for VPN */}
        {data.kind === 'vpn' ? (
          <div className={cn("grid grid-cols-2 gap-1.5", theme.box)}>
            <div className="flex flex-col p-1 px-1.5 rounded bg-[#1e1b4b] border border-indigo-800">
              <span className="text-[8px] text-emerald-400 font-bold uppercase leading-none mb-1">Down</span>
              <div className="flex items-baseline gap-1">
                <span className="text-[10px] font-bold leading-none tracking-tight">
                  {formatThroughputDual(data.bandwidthKbps)}
                </span>
              </div>
            </div>
            <div className="flex flex-col p-1 px-1.5 rounded bg-[#1e1b4b] border border-indigo-800">
              <span className="text-[8px] text-rose-400 font-bold uppercase leading-none mb-1">Up</span>
              <div className="flex items-baseline gap-1">
                <span className="text-[10px] font-bold leading-none tracking-tight">
                  {formatThroughputDual(data.uploadKbps)}
                </span>
              </div>
            </div>
          </div>
        ) : data.kind === 'proxy' || data.kind === 'engine' ? (
          <div className={cn("flex items-center justify-between rounded-md border p-1.5 px-2 shadow-sm", theme.box)}>
            <span className={cn("text-[10px] uppercase font-semibold", theme.label)}>
              {data.kind === 'proxy' ? 'Throughput' : 'Egress (to Proxy)'}
            </span>
            <span className="text-[10px] font-bold">
              {formatThroughputDual(data.kind === 'proxy' ? data.bandwidthKbps : data.proxyIngressKbps)}
            </span>
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
