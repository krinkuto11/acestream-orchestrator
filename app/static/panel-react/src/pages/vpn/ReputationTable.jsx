import React, { useRef, useCallback } from 'react'
import { ReputationRow } from './ReputationRow'

const COLS = [
  { key: '#',          label: '#',        width: 32,  sortKey: null },
  { key: 'accent',     label: '',         width: 4,   sortKey: null },
  { key: 'server',     label: 'SERVER',   width: 140, sortKey: 'name' },
  { key: 'location',   label: 'LOCATION', width: 154, sortKey: 'country' },
  { key: 'source',     label: 'SOURCE',   width: 70,  sortKey: null },
  { key: 'flags',      label: 'FLAGS',    width: 80,  sortKey: null },
  { key: 'reputation', label: 'REPUTATION', width: 140, sortKey: 'score' },
  { key: 'trend',      label: '30D TREND', width: 94, sortKey: null },
  { key: 'ttfb',       label: 'TTFB',    width: 62,  sortKey: 'ttfb' },
  { key: 'duration',   label: 'DURATION', width: 64,  sortKey: null },
  { key: 'probes',     label: 'PROBES',  width: 58,  sortKey: null },
  { key: 'load',       label: 'LOAD',    width: 56,  sortKey: 'load' },
  { key: 'menu',       label: '·',       width: 36,  sortKey: null },
]

export function ReputationTable({ items, loading, sort, dir, onSort, onLoadMore, hasMore, orchUrl, onAction }) {
  const sentinel = useRef(null)

  // Infinite scroll via IntersectionObserver.
  const observerRef = useCallback(node => {
    if (!node) return
    const obs = new IntersectionObserver(entries => {
      if (entries[0].isIntersecting && hasMore && !loading) onLoadMore()
    }, { threshold: 0.1 })
    obs.observe(node)
    return () => obs.disconnect()
  }, [hasMore, loading, onLoadMore])

  return (
    <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
      {/* Sticky header */}
      <div style={{
        display: 'grid',
        gridTemplateColumns: COLS.map(c => `${c.width}px`).join(' '),
        position: 'sticky', top: 0, zIndex: 10,
        background: 'var(--bg-1)',
        borderBottom: '1px solid var(--line)',
      }}>
        {COLS.map(col => (
          <div
            key={col.key}
            onClick={col.sortKey ? () => onSort(col.sortKey) : undefined}
            style={{
              padding: '4px 4px',
              fontSize: 9,
              fontFamily: 'var(--font-mono)',
              letterSpacing: '0.06em',
              color: sort === col.sortKey ? 'var(--acc-cyan, #22d3ee)' : 'var(--fg-3)',
              cursor: col.sortKey ? 'pointer' : 'default',
              userSelect: 'none',
              fontWeight: 600,
            }}
          >
            {col.label}
            {sort === col.sortKey && (
              <span style={{ marginLeft: 4, fontSize: 8 }}>{dir === 'asc' ? '▲' : '▼'}</span>
            )}
          </div>
        ))}
      </div>

      {/* Rows */}
      {items.length === 0 && !loading && (
        <div style={{ padding: 32, textAlign: 'center', color: 'var(--fg-3)', fontFamily: 'var(--font-mono)', fontSize: 12 }}>
          No servers found
        </div>
      )}
      {items.map((server, i) => (
        <ReputationRow
          key={server.id}
          server={server}
          index={i}
          orchUrl={orchUrl}
          onAction={onAction}
        />
      ))}

      {/* Sentinel for infinite scroll */}
      {hasMore && (
        <div ref={observerRef} style={{ padding: 12, textAlign: 'center', color: 'var(--fg-3)', fontSize: 11, fontFamily: 'var(--font-mono)' }}>
          {loading ? 'loading…' : '+ more'}
        </div>
      )}
    </div>
  )
}
