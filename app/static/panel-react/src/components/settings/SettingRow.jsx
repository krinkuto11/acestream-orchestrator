import React from 'react'

export function SettingRow({ label, description, children, warning, htmlFor, className = '' }) {
  return (
    <div style={{
      display: 'grid',
      gridTemplateColumns: 'minmax(180px, 240px) 1fr',
      gap: 12,
      padding: '10px 0',
      borderBottom: '1px dashed var(--line-soft)',
      alignItems: 'flex-start',
    }}>
      <div>
        <label
          htmlFor={htmlFor}
          style={{ fontSize: 11, color: 'var(--fg-0)', fontFamily: 'var(--font-mono)', fontWeight: 600, cursor: htmlFor ? 'pointer' : 'default' }}
        >
          {label}
        </label>
        {description && (
          <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 3, lineHeight: 1.5 }}>{description}</div>
        )}
        {warning && (
          <div style={{ fontSize: 10, color: 'var(--acc-amber)', marginTop: 4, display: 'flex', alignItems: 'center', gap: 4 }}>
            <span>⚠</span><span>{warning}</span>
          </div>
        )}
      </div>
      <div style={{ display: 'flex', alignItems: 'center', paddingTop: 2 }}>{children}</div>
    </div>
  )
}
