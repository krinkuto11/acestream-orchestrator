import React from 'react'

export function LoadBar({ pct }) {
  if (pct == null) return <span style={{ color: 'var(--fg-3)', fontSize: 11 }}>—</span>

  const color = pct >= 80
    ? 'var(--acc-red)'
    : pct >= 50
    ? 'var(--acc-amber)'
    : 'var(--acc-green)'

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
      <div style={{ width: 24, height: 4, background: 'var(--bg-2)', borderRadius: 1, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: color }}/>
      </div>
      <span style={{ fontSize: 11, fontVariantNumeric: 'tabular-nums', color: 'var(--fg-2)' }}>
        {pct}
      </span>
    </div>
  )
}
