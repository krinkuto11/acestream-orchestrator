import React from 'react'

const COLOR_MAP = {
  green:   'var(--acc-green)',
  amber:   'var(--acc-amber)',
  magenta: 'var(--acc-magenta, #c026d3)',
  red:     'var(--acc-red)',
}

export function RepSparkline({ data = [], color = 'red', width = 86, height = 20 }) {
  const fill = COLOR_MAP[color] || 'var(--acc-red)'

  // Pad to 30 values
  const vals = [...Array(30).fill(0), ...data].slice(-30)

  if (vals.every(v => v === 0)) {
    return (
      <svg width={width} height={height}>
        <line x1={0} y1={height / 2} x2={width} y2={height / 2}
          stroke="var(--line-soft, #333)" strokeWidth="1" strokeDasharray="2,3"/>
      </svg>
    )
  }

  const step = width / (vals.length - 1)
  const points = vals.map((v, i) => {
    const x = i * step
    const y = height - v * (height - 2) - 1
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')

  return (
    <svg width={width} height={height} style={{ overflow: 'visible' }}>
      <polyline
        points={points}
        fill="none"
        stroke={fill}
        strokeWidth="1.2"
        strokeLinejoin="round"
        strokeLinecap="round"
      />
    </svg>
  )
}
