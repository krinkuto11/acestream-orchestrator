import React, { useState, useEffect, useCallback } from 'react'

export function ReputationFilters({ filter, onChange, totalMatched, totalAll }) {
  const [q, setQ] = useState(filter.q || '')

  // Debounce q.
  useEffect(() => {
    const t = setTimeout(() => {
      if (q !== filter.q) onChange({ ...filter, q })
    }, 250)
    return () => clearTimeout(t)
  }, [q]) // eslint-disable-line

  const setSource = s => onChange({ ...filter, source: s })
  const toggleQuar = () => onChange({
    ...filter,
    quarantined: filter.quarantined === 'include' ? 'only' : 'include',
  })

  const srcOptions = ['all', 'proton', 'gluetun']

  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 12,
      padding: '6px 16px',
      borderBottom: '1px solid var(--line-soft)',
      background: 'var(--bg-0)',
      flexShrink: 0,
      flexWrap: 'wrap',
    }}>
      <input
        value={q}
        onChange={e => setQ(e.target.value)}
        placeholder="search…"
        style={{
          background: 'var(--bg-2)',
          border: '1px solid var(--line)',
          color: 'var(--fg-1)',
          fontFamily: 'var(--font-mono)',
          fontSize: 11,
          padding: '3px 8px',
          borderRadius: 2,
          width: 160,
          outline: 'none',
        }}
      />

      <div style={{ display: 'flex', gap: 0 }}>
        {srcOptions.map(s => (
          <button
            key={s}
            onClick={() => setSource(s)}
            style={{
              background: filter.source === s || (!filter.source && s === 'all')
                ? 'var(--acc-cyan, #22d3ee)' : 'var(--bg-2)',
              color: filter.source === s || (!filter.source && s === 'all')
                ? 'var(--bg-0)' : 'var(--fg-2)',
              border: '1px solid var(--line)',
              marginLeft: -1,
              cursor: 'pointer',
              fontSize: 9,
              fontFamily: 'var(--font-mono)',
              letterSpacing: '0.04em',
              padding: '3px 8px',
              fontWeight: 600,
            }}
          >
            {s.toUpperCase()}
          </button>
        ))}
      </div>

      <label style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'pointer', fontSize: 11, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)' }}>
        <input
          type="checkbox"
          checked={filter.quarantined === 'only'}
          onChange={toggleQuar}
          style={{ accentColor: 'var(--acc-amber)' }}
        />
        quarantined only
      </label>

      <div style={{ flex: 1 }}/>

      <span style={{ fontSize: 11, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
        {totalMatched} of {totalAll}
      </span>
    </div>
  )
}
