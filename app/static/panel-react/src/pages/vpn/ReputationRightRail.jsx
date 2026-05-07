import React from 'react'

function relTime(ts) {
  if (!ts) return '—'
  const d = (Date.now() - new Date(ts).getTime()) / 1000
  if (d < 60)  return `${Math.round(d)}s`
  if (d < 3600) return `${Math.round(d / 60)}m`
  return `${Math.round(d / 3600)}h`
}

export function ReputationRightRail({ vpnNodes = [], recentProbes = [] }) {
  return (
    <div style={{
      width: 280,
      flexShrink: 0,
      borderLeft: '1px solid var(--line-soft)',
      background: 'var(--bg-1)',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
    }}>
      {/* Active bindings */}
      <SectionHeader>ACTIVE BINDINGS</SectionHeader>
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0, maxHeight: 280 }}>
        {vpnNodes.length === 0 ? (
          <Empty>no active nodes</Empty>
        ) : vpnNodes.map(node => (
          <NodeRow key={node.container_name || node.id} node={node}/>
        ))}
      </div>

      <div style={{ height: 1, background: 'var(--line-soft)', margin: '0 12px' }}/>

      {/* Recent probes */}
      <SectionHeader>RECENT PROBES</SectionHeader>
      <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
        {recentProbes.length === 0 ? (
          <Empty>no recent probes</Empty>
        ) : recentProbes.map((p, i) => (
          <ProbeRow key={i} probe={p}/>
        ))}
      </div>
    </div>
  )
}

function SectionHeader({ children }) {
  return (
    <div style={{
      padding: '8px 12px 4px',
      fontSize: 9,
      fontFamily: 'var(--font-mono)',
      letterSpacing: '0.08em',
      color: 'var(--fg-3)',
      fontWeight: 600,
    }}>
      {children}
    </div>
  )
}

function Empty({ children }) {
  return (
    <div style={{ padding: '8px 12px', fontSize: 11, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
      {children}
    </div>
  )
}

function NodeRow({ node }) {
  const healthy = node.healthy || node.lifecycle === 'running'
  const color = healthy ? 'var(--acc-green)' : 'var(--acc-amber)'

  return (
    <div style={{ padding: '4px 12px', borderBottom: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 8 }}>
      <span className="dot" style={{ color, fontSize: 8 }}/>
      <div style={{ minWidth: 0, flex: 1 }}>
        <span style={{ fontSize: 11, color: 'var(--fg-1)', fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', display: 'block' }}>
          {node.container_name || node.id}
        </span>
        {node.assigned_hostname && (
          <div style={{ fontSize: 9, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {node.assigned_hostname}
          </div>
        )}
      </div>
      <span style={{ fontSize: 9, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', flexShrink: 0 }}>
        {relTime(node.first_seen)}
      </span>
    </div>
  )
}

function ProbeRow({ probe }) {
  const ok = probe.successes_n > 0
  const ratio = `${probe.successes_n}/${probe.sample_n}`

  return (
    <div style={{ padding: '4px 12px', borderBottom: '1px solid var(--line-soft)' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <span style={{ fontSize: 9, fontFamily: 'var(--font-mono)', color: 'var(--acc-cyan, #22d3ee)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: 140 }}>
          {probe.content_id || '—'}
        </span>
        <span style={{ fontSize: 10, color: ok ? 'var(--acc-green)' : 'var(--acc-amber)', fontFamily: 'var(--font-mono)' }}>
          {ratio}
        </span>
      </div>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginTop: 1 }}>
        <span style={{ fontSize: 9, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
          {probe.ttfb_avg_ms != null ? `${probe.ttfb_avg_ms}ms` : ''}
        </span>
        <span style={{ fontSize: 9, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
          {relTime(probe.started_at)}
        </span>
      </div>
    </div>
  )
}
