import React from 'react'
import { useNavigate } from 'react-router-dom'

export function ReputationHeaderBar({ stats = {}, orchUrl, apiKey, onRefresh }) {
  const navigate = useNavigate()

  const total   = Object.values(stats.by_source || {}).reduce((a, b) => a + b, 0)
  const proton  = stats.by_source?.proton || 0
  const gluetun = stats.by_source?.gluetun || 0
  const up      = stats.by_status?.up || 0
  const elite   = stats.by_color?.green || 0
  const quar    = Object.values(stats.by_status || {}).filter((_, k) => k === 'quarantined').reduce((a, b) => a + b, 0)

  const handleRefreshProton = async () => {
    try {
      const headers = { 'Content-Type': 'application/json' }
      if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`
      await fetch(`${orchUrl}/api/v1/vpn/proton/refresh`, { method: 'POST', headers })
      onRefresh?.()
    } catch { /* ignore */ }
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 16,
      padding: '8px 16px',
      borderBottom: '1px solid var(--line-soft)',
      background: 'var(--bg-1)',
      flexShrink: 0,
      flexWrap: 'wrap',
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
        <span className="dot pulse" style={{ color: 'var(--acc-cyan, #22d3ee)', fontSize: 10 }}/>
        <span style={{
          fontFamily: 'var(--font-mono)',
          fontSize: 12, fontWeight: 600,
          letterSpacing: '0.06em',
          color: 'var(--fg-0)',
        }}>VPN.REPUTATION</span>
      </div>

      <span style={{
        fontSize: 11,
        fontFamily: 'var(--font-mono)',
        color: 'var(--fg-3)',
        letterSpacing: '0.02em',
      }}>
        {total} servers · {proton} proton · {gluetun} gluetun · {up} up · {elite} elite
        {quar > 0 && ` · ${quar} quarantined`}
      </span>

      <div style={{ flex: 1 }}/>

      <button
        onClick={handleRefreshProton}
        style={btnStyle}
        title="Refresh ProtonVPN server catalog"
      >
        ⟳ REFRESH PROTON
      </button>

      <button
        onClick={() => navigate('/vpn/probes')}
        style={{ ...btnStyle, color: 'var(--acc-green)', borderColor: 'var(--acc-green)' }}
      >
        ▶ PROBE
      </button>

      <button
        onClick={() => navigate('/vpn/leases')}
        style={btnStyle}
        title="VPN node management"
      >
        ⌬ NODES
      </button>
    </div>
  )
}

const btnStyle = {
  background: 'none',
  border: '1px solid var(--line)',
  color: 'var(--fg-2)',
  cursor: 'pointer',
  fontSize: 10,
  fontFamily: 'var(--font-mono)',
  letterSpacing: '0.04em',
  padding: '3px 8px',
  borderRadius: 2,
}
