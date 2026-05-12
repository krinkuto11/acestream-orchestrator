import React from 'react'

const PILL = {
  display: 'inline-block',
  fontSize: 8,
  fontFamily: 'var(--font-mono)',
  letterSpacing: '0.04em',
  padding: '1px 4px',
  borderRadius: 2,
  lineHeight: 1.5,
  fontWeight: 600,
}

export function RepFlagBadges({ flags = {}, quarantined = false }) {
  return (
    <div style={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
      {flags.port_forward && (
        <span style={{ ...PILL, color: 'var(--acc-green)', border: '1px solid var(--acc-green)' }}>FWD</span>
      )}
      {flags.stream && (
        <span style={{ ...PILL, color: 'var(--acc-magenta, #c026d3)', border: '1px solid var(--acc-magenta, #c026d3)' }}>STR</span>
      )}
      {flags.secure_core && (
        <span style={{ ...PILL, color: 'var(--acc-cyan, #22d3ee)', border: '1px solid var(--acc-cyan, #22d3ee)' }}>SC</span>
      )}
      {flags.free && (
        <span style={{ ...PILL, color: 'var(--fg-3)', border: '1px solid var(--line)' }}>FREE</span>
      )}
      {quarantined && (
        <span style={{ ...PILL, color: 'var(--acc-red)', border: '1px solid var(--acc-red)' }}>QUAR</span>
      )}
    </div>
  )
}
