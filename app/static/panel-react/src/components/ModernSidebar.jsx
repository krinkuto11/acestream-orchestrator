import React, { useEffect, useState } from 'react'
import { Link, useLocation } from 'react-router-dom'
import { useLocalStorage } from '@/hooks/useLocalStorage'

const NAV_ITEMS = [
  { id: 'central',    label: 'Overview',   glyph: '◇', kbd: '1', href: '/' },
  { id: 'engines',    label: 'Engines',    glyph: '▤', kbd: '2', href: '/engines' },
  { id: 'streams',    label: 'Streams',    glyph: '↯', kbd: '3', href: '/streams' },
  { id: 'monitoring', label: 'Monitor',    glyph: '▧', kbd: '4', href: '/stream-monitoring' },
  { id: 'topology',   label: 'Topology',   glyph: '⌬', kbd: '5', href: '/routing-topology' },
  { id: 'metrics',    label: 'Dashboard',  glyph: '▲', kbd: '6', href: '/metrics' },
  { id: 'settings',   label: 'Settings',   glyph: '⌗', kbd: '7', href: '/settings' },
]

function BrandMark({ size = 18 }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <rect x="2" y="2" width="20" height="20" stroke="var(--acc-green)" strokeWidth="1"/>
      <rect x="6" y="6" width="12" height="12" stroke="var(--acc-green-dim)" strokeWidth="1"/>
      <rect x="10" y="10" width="4" height="4" fill="var(--acc-green)"/>
    </svg>
  )
}

function StatusRow({ label, value, ok }) {
  const color = ok ? 'var(--acc-green)' : 'var(--acc-amber)'
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
      <span className="dot pulse" style={{ color }}/>
      <span style={{ flex: 1 }}>{label}</span>
      <span style={{ color: ok ? 'var(--fg-2)' : 'var(--acc-amber)' }}>{value}</span>
    </div>
  )
}

export function ModernSidebar({ orchestratorStatus, isConnected }) {
  const location = useLocation()
  const [version, setVersion] = useState('–')

  useEffect(() => {
    fetch('/panel/version.json')
      .then(r => r.json())
      .then(d => setVersion(d.version))
      .catch(() => {})
  }, [])

  useEffect(() => {
    // CSS var for content offset
    document.documentElement.style.setProperty('--sidebar-width', '200px')
  }, [])

  const circuitBreaker = orchestratorStatus?.provisioning?.circuit_breaker_state || 'closed'
  const vpnOk = isConnected

  return (
    <aside style={{
      position: 'fixed', left: 0, top: 0,
      width: 200, height: '100vh',
      display: 'flex', flexDirection: 'column',
      background: 'var(--bg-1)',
      borderRight: '1px solid var(--line-soft)',
      flexShrink: 0,
      zIndex: 50,
    }}>
      {/* Brand */}
      <div style={{
        display: 'flex', alignItems: 'center', gap: 8,
        padding: '14px',
        borderBottom: '1px solid var(--line-soft)',
        flexShrink: 0,
      }}>
        <BrandMark size={18}/>
        <div style={{ lineHeight: 1.1, minWidth: 0 }}>
          <div style={{
            fontFamily: 'var(--font-display)',
            fontSize: 12, fontWeight: 600,
            letterSpacing: '0.02em',
            color: 'var(--fg-0)',
          }}>ACE//ORCH</div>
          <div style={{
            fontSize: 9, color: 'var(--fg-3)',
            letterSpacing: '0.1em',
          }}>v{version}</div>
        </div>
      </div>

      {/* Nav */}
      <div style={{ padding: '12px 8px 4px', flexShrink: 0 }}>
        <div className="label" style={{ padding: '0 6px 6px' }}>NAV</div>
        <nav style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {NAV_ITEMS.map(item => {
            const isActive = location.pathname === item.href
            return (
              <Link
                key={item.id}
                to={item.href}
                style={{
                  display: 'flex', alignItems: 'center', gap: 8,
                  padding: '6px 8px',
                  borderLeft: `2px solid ${isActive ? 'var(--acc-green)' : 'transparent'}`,
                  background: isActive ? 'var(--bg-2)' : 'transparent',
                  color: isActive ? 'var(--fg-0)' : 'var(--fg-1)',
                  textDecoration: 'none',
                  fontFamily: 'var(--font-mono)',
                  fontSize: 11,
                  transition: 'background 0.1s',
                }}
              >
                <span style={{ width: 14, flexShrink: 0, color: isActive ? 'var(--acc-green)' : 'var(--fg-3)' }}>
                  {item.glyph}
                </span>
                <span style={{ flex: 1 }}>{item.label}</span>
                <span className="kbd">{item.kbd}</span>
              </Link>
            )
          })}
        </nav>
      </div>

      <div style={{ flex: 1 }}/>

      {/* Ctrl plane status */}
      <div style={{
        display: 'flex', flexDirection: 'column', gap: 6,
        padding: '12px 14px',
        borderTop: '1px solid var(--line-soft)',
        flexShrink: 0,
      }}>
        <div className="label">CTRL PLANE</div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 10, color: 'var(--fg-1)' }}>
          <StatusRow label="api" value={isConnected ? 'online' : 'down'} ok={isConnected}/>
          <StatusRow label="scheduler" value="ok" ok={true}/>
          <StatusRow label="breaker" value={circuitBreaker} ok={circuitBreaker === 'closed'}/>
          <StatusRow label="sse" value="live" ok={isConnected}/>
        </div>
        <div className="hr" style={{ margin: '2px 0' }}/>
        <div style={{ fontSize: 10, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
          1s · eu-central
        </div>
      </div>
    </aside>
  )
}
