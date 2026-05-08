import React, { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { RepGauge } from '../../components/reputation/RepGauge'
import { RepSparkline } from '../../components/reputation/RepSparkline'
import { RepFlagBadges } from '../../components/reputation/RepFlagBadges'
import { SourceBadge } from '../../components/reputation/SourceBadge'
import { LoadBar } from '../../components/reputation/LoadBar'

const COLOR_MAP = {
  green:   'var(--acc-green)',
  amber:   'var(--acc-amber)',
  magenta: 'var(--acc-magenta, #c026d3)',
  red:     'var(--acc-red)',
}

export function ReputationRow({ server, index, orchUrl, onAction }) {
  const navigate = useNavigate()
  const [menuOpen, setMenuOpen] = useState(false)

  const color  = server.score_color || 'red'
  const accent = COLOR_MAP[color] || 'var(--acc-red)'
  const isQuar = server.quarantined

  const doAction = async (path, body) => {
    setMenuOpen(false)
    try {
      await fetch(`${orchUrl}/api/v1/vpn/servers/${server.id}/${path}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
      })
      onAction?.()
    } catch { /* ignore */ }
  }

  const copyID = () => {
    navigator.clipboard.writeText(server.id).catch(() => {})
    setMenuOpen(false)
  }

  const ttfb = server.ttfb_p50_ms != null ? `${server.ttfb_p50_ms}ms` : '—'
  const dur  = server.duration_avg_min != null ? `${server.duration_avg_min}m` : '—'

  return (
    <div
      role="row"
      tabIndex={0}
      onClick={() => navigate(`/vpn/servers/${server.id}`)}
      onKeyDown={e => e.key === 'Enter' && navigate(`/vpn/servers/${server.id}`)}
      style={{
        display: 'grid',
        gridTemplateColumns: '32px 4px 140px 154px 70px 80px 140px 94px 62px 64px 58px 56px 36px',
        alignItems: 'center',
        height: 36,
        borderBottom: '1px solid var(--line-soft)',
        background: index % 2 === 0 ? 'var(--bg-0)' : 'var(--bg-1)',
        opacity: isQuar ? 0.55 : 1,
        cursor: 'pointer',
        outline: 'none',
        transition: 'background 0.1s',
      }}
      onMouseEnter={e => e.currentTarget.style.background = 'var(--bg-2)'}
      onMouseLeave={e => e.currentTarget.style.background = index % 2 === 0 ? 'var(--bg-0)' : 'var(--bg-1)'}
    >
      {/* # */}
      <span style={{ fontSize: 10, color: 'var(--fg-3)', fontVariantNumeric: 'tabular-nums', textAlign: 'center', fontFamily: 'var(--font-mono)' }}>
        {String(index + 1).padStart(3, '0')}
      </span>

      {/* Accent stripe */}
      <div style={{ width: 4, height: '100%', background: accent, alignSelf: 'stretch' }}/>

      {/* SERVER */}
      <div style={{ paddingLeft: 8, overflow: 'hidden' }}>
        <div style={{ fontSize: 12, fontWeight: 600, color: 'var(--fg-0)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {server.pinned && <span style={{ color: 'var(--acc-cyan, #22d3ee)', marginRight: 4 }}>★</span>}
          {server.name || server.hostname}
        </div>
        <div style={{ fontSize: 9, color: 'var(--fg-3)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {server.hostname}
        </div>
      </div>

      {/* LOCATION */}
      <div style={{ overflow: 'hidden', paddingLeft: 4 }}>
        <span style={{ fontSize: 9, fontFamily: 'var(--font-mono)', color: 'var(--acc-cyan, #22d3ee)', marginRight: 4 }}>
          [{server.cc || '??'}]
        </span>
        <span style={{ fontSize: 11, color: 'var(--fg-2)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {[server.country, server.city].filter(Boolean).join(' · ')}
        </span>
      </div>

      {/* SOURCE */}
      <div style={{ paddingLeft: 4 }}>
        <SourceBadge source={server.source}/>
      </div>

      {/* FLAGS */}
      <div style={{ paddingLeft: 4 }}>
        <RepFlagBadges flags={server.flags || {}} quarantined={isQuar}/>
      </div>

      {/* REPUTATION */}
      <div style={{ paddingLeft: 4 }}>
        <RepGauge score={server.score || 0} color={color}/>
      </div>

      {/* 30D TREND */}
      <div style={{ paddingLeft: 4 }}>
        <RepSparkline data={server.history_30 || []} color={color}/>
      </div>

      {/* TTFB */}
      <span style={{ fontSize: 11, color: 'var(--fg-2)', fontVariantNumeric: 'tabular-nums', fontFamily: 'var(--font-mono)', paddingLeft: 4 }}>
        {ttfb}
      </span>

      {/* DURATION */}
      <span style={{ fontSize: 11, color: 'var(--fg-2)', fontVariantNumeric: 'tabular-nums', fontFamily: 'var(--font-mono)', paddingLeft: 4 }}>
        {dur}
      </span>

      {/* PROBES */}
      <span style={{ fontSize: 11, color: 'var(--fg-2)', fontVariantNumeric: 'tabular-nums', fontFamily: 'var(--font-mono)', paddingLeft: 4 }}>
        {server.probes_n != null ? `${server.successes_n}/${server.probes_n}` : '—'}
      </span>

      {/* LOAD */}
      <div style={{ paddingLeft: 4 }}>
        <LoadBar pct={server.load_pct}/>
      </div>

      {/* Menu */}
      <div
        style={{ position: 'relative', textAlign: 'center' }}
        onClick={e => { e.stopPropagation(); setMenuOpen(m => !m) }}
      >
        <button style={{ background: 'none', border: 'none', color: 'var(--fg-3)', cursor: 'pointer', fontSize: 14, padding: '0 8px' }}>
          ⋯
        </button>
        {menuOpen && (
          <div style={{
            position: 'absolute', right: 0, top: '100%', zIndex: 100,
            background: 'var(--bg-2)', border: '1px solid var(--line)',
            minWidth: 160, borderRadius: 2,
          }}>
            {[
              { label: server.pinned ? 'Unpin' : 'Pin — prefer', action: () => doAction('pin', { pinned: !server.pinned }) },
              { label: isQuar ? 'Lift quarantine' : 'Quarantine 1h', action: () => doAction('quarantine', isQuar ? { until: null } : { until: new Date(Date.now() + 3600000).toISOString(), reason: 'manual' }) },
              { label: 'Copy server id', action: copyID },
              { label: 'Open detail', action: () => { setMenuOpen(false); navigate(`/vpn/servers/${server.id}`) } },
            ].map(item => (
              <button key={item.label} onClick={e => { e.stopPropagation(); item.action() }} style={menuItemStyle}>
                {item.label}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

const menuItemStyle = {
  display: 'block',
  width: '100%',
  background: 'none',
  border: 'none',
  color: 'var(--fg-1)',
  fontFamily: 'var(--font-mono)',
  fontSize: 11,
  padding: '6px 12px',
  textAlign: 'left',
  cursor: 'pointer',
  borderBottom: '1px solid var(--line-soft)',
}
