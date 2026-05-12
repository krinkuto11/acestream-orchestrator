import React from 'react'

const COLOR_MAP = {
  green:   'var(--acc-green)',
  amber:   'var(--acc-amber)',
  magenta: 'var(--acc-magenta, #c026d3)',
  red:     'var(--acc-red)',
}

export function RepGauge({ score = 0, color = 'red', width = 48, height = 6 }) {
  const fill = COLOR_MAP[color] || 'var(--acc-red)'
  const pct = Math.round((score || 0) * 100)

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{
        width, height,
        background: 'var(--bg-2, #1e1e1e)',
        borderRadius: 2,
        overflow: 'hidden',
        flexShrink: 0,
      }}>
        <div style={{
          width: `${pct}%`,
          height: '100%',
          background: fill,
          transition: 'width 0.3s ease',
        }}/>
      </div>
      <span style={{
        fontSize: 11,
        fontFamily: 'var(--font-mono)',
        color: fill,
        fontVariantNumeric: 'tabular-nums',
        minWidth: 26,
        textAlign: 'right',
      }}>
        {pct.toString().padStart(2, '0')}
      </span>
    </div>
  )
}
