import React from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import { RepGauge } from '../../components/reputation/RepGauge'
import { RepFlagBadges } from '../../components/reputation/RepFlagBadges'
import { LoadBar } from '../../components/reputation/LoadBar'
import { useServerDetail } from './hooks/useServerDetail'

export function ServerDetailPage({ orchUrl }) {
  const { id } = useParams()
  const navigate = useNavigate()
  const { detail, loading, error, refetch } = useServerDetail({ orchUrl, serverId: id })

  const doAction = async (path, body) => {
    try {
      await fetch(`${orchUrl}/api/v1/vpn/servers/${id}/${path}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      refetch()
    } catch { /* ignore */ }
  }

  if (loading) return <Loading/>
  if (error || !detail) return <Err msg={error || 'not found'} onBack={() => navigate('/vpn')}/>

  const { server, by_category = [], recent_probes = [] } = detail
  const isQuar = server.quarantined
  const color  = server.score_color || 'red'

  return (
    <div style={{ padding: 20, maxWidth: 900, fontFamily: 'var(--font-mono)' }}>
      <button onClick={() => navigate('/vpn')} style={backBtn}>← VPN.REPUTATION</button>

      {/* Identity card */}
      <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line)', borderRadius: 2, padding: 16, marginTop: 12 }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, flexWrap: 'wrap' }}>
          <div style={{ flex: 1, minWidth: 200 }}>
            <div style={{ fontSize: 16, fontWeight: 600, color: 'var(--fg-0)' }}>
              {server.pinned && <span style={{ color: 'var(--acc-cyan, #22d3ee)', marginRight: 6 }}>★</span>}
              {server.name || server.hostname}
            </div>
            <div style={{ fontSize: 11, color: 'var(--fg-3)', marginTop: 2 }}>{server.hostname}</div>
            <div style={{ fontSize: 11, color: 'var(--fg-2)', marginTop: 4 }}>
              {[server.country, server.city].filter(Boolean).join(' · ')}
              {server.cc && <span style={{ marginLeft: 8, color: 'var(--acc-cyan, #22d3ee)' }}>[{server.cc}]</span>}
            </div>
            <div style={{ marginTop: 8 }}>
              <RepFlagBadges flags={server.flags || {}} quarantined={isQuar}/>
            </div>
          </div>

          {/* KPI strip */}
          <div style={{ display: 'flex', gap: 24, flexWrap: 'wrap' }}>
            <KPI label="REPUTATION">
              <RepGauge score={server.score || 0} color={color} width={60}/>
            </KPI>
            <KPI label="TTFB">
              <span style={{ fontSize: 14, color: 'var(--fg-1)' }}>
                {server.ttfb_p50_ms != null ? `${server.ttfb_p50_ms}ms` : '—'}
              </span>
            </KPI>
            <KPI label="LOAD">
              <LoadBar pct={server.load_pct}/>
            </KPI>
            <KPI label="PROBES">
              <span style={{ fontSize: 14, color: 'var(--fg-1)' }}>
                {server.probes_n != null ? `${server.successes_n}/${server.probes_n}` : '—'}
              </span>
            </KPI>
          </div>
        </div>

        {/* Actions */}
        <div style={{ display: 'flex', gap: 8, marginTop: 12 }}>
          <ActionBtn
            onClick={() => doAction('pin', { pinned: !server.pinned })}
            active={server.pinned}
          >
            {server.pinned ? '★ Unpin' : '★ Pin'}
          </ActionBtn>
          <ActionBtn
            onClick={() => doAction('quarantine', isQuar
              ? { until: null }
              : { until: new Date(Date.now() + 3600000).toISOString(), reason: 'manual' }
            )}
            danger={!isQuar}
          >
            {isQuar ? '⊘ Lift quarantine' : '⊘ Quarantine 1h'}
          </ActionBtn>
        </div>
      </div>

      {/* Per-category */}
      {by_category.length > 0 && (
        <Section title="PER-CATEGORY REPUTATION">
          {by_category.map(c => (
            <div key={c.category} style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '6px 0', borderBottom: '1px solid var(--line-soft)' }}>
              <span style={{ width: 120, fontSize: 11, color: 'var(--fg-2)' }}>{c.category}</span>
              <RepGauge score={c.score} color={c.score_color || 'red'} width={80}/>
              <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>{c.probes_n} probes</span>
              <span style={{ fontSize: 11, color: 'var(--fg-3)' }}>{c.ttfb_p50_ms != null ? `${c.ttfb_p50_ms}ms` : ''}</span>
            </div>
          ))}
        </Section>
      )}

      {/* Recent probes */}
      {recent_probes.length > 0 && (
        <Section title="RECENT PROBES">
          <div style={{ display: 'grid', gridTemplateColumns: '1fr 120px 80px 80px 80px', gap: 0 }}>
            {['CONTENT', 'CATEGORY', 'OUTCOME', 'TTFB', 'STARTED'].map(h => (
              <span key={h} style={{ fontSize: 9, color: 'var(--fg-3)', letterSpacing: '0.06em', padding: '4px 0', fontWeight: 600 }}>{h}</span>
            ))}
            {recent_probes.map((p, i) => (
              <React.Fragment key={i}>
                <span style={cellStyle}>{p.content_id || '—'}</span>
                <span style={cellStyle}>{p.category}</span>
                <span style={{ ...cellStyle, color: p.outcome === 'success' ? 'var(--acc-green)' : 'var(--acc-red)' }}>{p.outcome}</span>
                <span style={cellStyle}>{p.ttfb_ms != null ? `${p.ttfb_ms}ms` : '—'}</span>
                <span style={{ ...cellStyle, color: 'var(--fg-3)' }}>{p.started_at ? new Date(p.started_at).toLocaleTimeString() : '—'}</span>
              </React.Fragment>
            ))}
          </div>
        </Section>
      )}
    </div>
  )
}

function KPI({ label, children }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <span style={{ fontSize: 9, letterSpacing: '0.06em', color: 'var(--fg-3)', fontWeight: 600 }}>{label}</span>
      {children}
    </div>
  )
}

function Section({ title, children }) {
  return (
    <div style={{ marginTop: 16, background: 'var(--bg-1)', border: '1px solid var(--line)', borderRadius: 2, padding: 16 }}>
      <div style={{ fontSize: 9, letterSpacing: '0.08em', color: 'var(--fg-3)', fontWeight: 600, marginBottom: 8 }}>{title}</div>
      {children}
    </div>
  )
}

function ActionBtn({ onClick, active, danger, children }) {
  return (
    <button onClick={onClick} style={{
      background: 'none',
      border: `1px solid ${active ? 'var(--acc-cyan, #22d3ee)' : danger ? 'var(--acc-amber)' : 'var(--line)'}`,
      color: active ? 'var(--acc-cyan, #22d3ee)' : danger ? 'var(--acc-amber)' : 'var(--fg-2)',
      cursor: 'pointer',
      fontSize: 11,
      fontFamily: 'var(--font-mono)',
      padding: '4px 12px',
      borderRadius: 2,
    }}>
      {children}
    </button>
  )
}

function Loading() {
  return <div style={{ padding: 32, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>Loading…</div>
}

function Err({ msg, onBack }) {
  return (
    <div style={{ padding: 32, color: 'var(--acc-red)', fontFamily: 'var(--font-mono)' }}>
      Error: {msg} <button onClick={onBack} style={{ marginLeft: 12, color: 'var(--fg-2)', background: 'none', border: 'none', cursor: 'pointer' }}>← back</button>
    </div>
  )
}

const backBtn = {
  background: 'none',
  border: 'none',
  color: 'var(--acc-cyan, #22d3ee)',
  cursor: 'pointer',
  fontSize: 11,
  fontFamily: 'var(--font-mono)',
  padding: 0,
}

const cellStyle = {
  fontSize: 11,
  color: 'var(--fg-2)',
  fontFamily: 'var(--font-mono)',
  padding: '4px 0',
  borderBottom: '1px solid var(--line-soft)',
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap',
}
