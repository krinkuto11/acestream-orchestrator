import React from 'react'

export function SourceBadge({ source }) {
  const isProton = source === 'proton'
  return (
    <span style={{
      fontSize: 9,
      fontFamily: 'var(--font-mono)',
      letterSpacing: '0.06em',
      color: isProton ? 'var(--acc-magenta, #c026d3)' : 'var(--fg-3)',
      fontWeight: 600,
    }}>
      ◇{source?.toUpperCase()}
    </span>
  )
}
