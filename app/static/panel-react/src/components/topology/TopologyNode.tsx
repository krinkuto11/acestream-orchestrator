import React from 'react'
import { Handle, Position, type NodeProps } from 'reactflow'
import { cn } from '@/lib/utils'
import { formatThroughputDual, type TopologyNodeData } from '@/stores/topologyStore'

const formatBytes = (bytes: number, decimals = 2) => {
  if (bytes === 0) return '0 B'
  const k = 1024
  const dm = decimals < 0 ? 0 : decimals
  const sizes = ['B', 'KB', 'MB', 'GB', 'TB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i]
}

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
    accent: 'var(--acc-cyan)',
    bg: 'var(--acc-cyan-bg)',
    dim: 'var(--acc-cyan-dim)',
  },
  engine: {
    accent: 'var(--acc-green)',
    bg: 'var(--acc-green-bg)',
    dim: 'var(--acc-green-dim)',
  },
  proxy: {
    accent: 'var(--acc-magenta)',
    bg: 'var(--acc-magenta-bg)',
    dim: 'var(--acc-magenta-dim)',
  },
  client: {
    accent: 'var(--fg-1)',
    bg: 'var(--bg-2)',
    dim: 'var(--line)',
  },
}

export function TopologyNode({ data, selected }: NodeProps<TopologyNodeData>) {
  const theme = themeByKind[data.kind] || themeByKind.engine
  const isDraining = data.lifecycle === 'draining'
  const flag = countryToFlag(data.metadata?.country as string)

  return (
    <div
      style={{
        minWidth: 220,
        background: 'var(--bg-1)',
        border: `1px solid ${selected ? theme.accent : 'var(--line)'}`,
        padding: '12px',
        boxShadow: selected ? `0 0 20px ${theme.bg}` : '0 4px 12px rgba(0,0,0,0.3)',
        position: 'relative',
        opacity: isDraining ? 0.7 : 1,
        transition: 'all 0.2s ease',
      }}
      className="bracketed"
    >
      <Handle
        type="target"
        position={Position.Left}
        style={{ background: theme.accent, border: '2px solid var(--bg-1)', width: 8, height: 8 }}
      />

      {/* Node Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 10 }}>
        <div>
          <div style={{ fontSize: 9, color: 'var(--fg-3)', letterSpacing: 1, marginBottom: 2 }}>{data.kind.toUpperCase()}</div>
          <div style={{ fontSize: 13, fontWeight: 700, color: 'var(--fg-0)', fontFamily: 'var(--font-display)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', maxWidth: 140 }}>
            {data.title}
          </div>
          <div style={{ fontSize: 10, color: theme.accent, fontFamily: 'var(--font-mono)', opacity: 0.8 }}>
            {data.subtitle}
          </div>
        </div>
        
        <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
          <span className={`tag tag-${data.health === 'healthy' ? 'green' : data.health === 'down' ? 'red' : 'amber'}`} style={{ fontSize: 9 }}>
            {data.health.toUpperCase()}
          </span>
          {data.kind === 'engine' && data.forwarded && (
            <span className="tag tag-cyan" style={{ fontSize: 8 }}>FWD</span>
          )}
        </div>
      </div>

      {/* Content Area */}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {data.kind === 'vpn' && (
          <div style={{ background: 'var(--bg-0)', border: '1px solid var(--line-soft)', padding: '6px 8px' }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <span style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--fg-1)' }}>
                {data.metadata?.publicIp as string || '—'} {flag}
              </span>
              {typeof data.load === 'number' && (
                <span style={{ fontSize: 10, fontWeight: 700, color: data.load > 80 ? 'var(--acc-red)' : data.load > 50 ? 'var(--acc-amber)' : 'var(--acc-green)' }}>
                  {Math.round(data.load)}%
                </span>
              )}
            </div>
            {data.metadata?.provider && (
              <div style={{ fontSize: 9, color: 'var(--fg-3)', marginTop: 2 }}>
                {data.metadata.provider as string}
              </div>
            )}
          </div>
        )}

        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--bg-0)', padding: '6px 8px', border: '1px solid var(--line-soft)' }}>
          <span style={{ fontSize: 9, color: 'var(--fg-3)' }}>
            {data.kind === 'engine' ? 'STREAMS' : data.kind === 'client' ? 'SENT' : 'THROUGHPUT'}
          </span>
          <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--fg-1)', fontVariantNumeric: 'tabular-nums' }}>
            {data.kind === 'engine' 
              ? data.streamCount 
              : data.kind === 'client' 
                ? formatBytes(Number(data.metadata?.totalBytes || 0))
                : formatThroughputDual(data.bandwidthKbps)
            }
          </span>
        </div>

        {data.kind === 'vpn' && (
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: 'var(--bg-0)', padding: '6px 8px', border: '1px solid var(--line-soft)' }}>
            <span style={{ fontSize: 9, color: 'var(--fg-3)' }}>UPLOAD</span>
            <span style={{ fontSize: 11, fontWeight: 700, color: 'var(--acc-amber)', fontVariantNumeric: 'tabular-nums' }}>
              {formatThroughputDual(data.uploadKbps)}
            </span>
          </div>
        )}
      </div>

      <Handle
        type="source"
        position={Position.Right}
        style={{ background: theme.accent, border: '2px solid var(--bg-1)', width: 8, height: 8 }}
      />
    </div>
  )
}
