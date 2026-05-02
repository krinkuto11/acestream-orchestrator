import React from 'react'

function StatusTag({ status }) {
  const map = {
    started: 'green', active: 'green',
    pending_failover: 'magenta', migrating: 'magenta',
    ended: 'amber', stopping: 'amber',
    failed: 'red', error: 'red',
  }
  const normalized = String(status || '').toLowerCase().replace(/ /g, '_')
  const color = map[normalized] || 'amber'
  const label = normalized.toUpperCase().replace(/_/g, '.')
  return <span className={`tag tag-${color}`}><span className="dot pulse"/>{label}</span>
}

function BufferBar({ value }) {
  const pct = Math.max(0, Math.min(100, Number(value) || 0))
  const color = pct > 70 ? 'var(--acc-green)' : pct > 40 ? 'var(--acc-amber)' : 'var(--acc-red)'
  return (
    <span style={{ fontSize: 12.5, color: color, fontWeight: 600, fontFamily: 'var(--font-mono)' }}>{pct}%</span>
  )
}

function getStreamInfohash(s) {
  const fromLabel = String(s?.labels?.['stream.resolved_infohash'] || '').trim()
  if (fromLabel) return fromLabel.slice(0, 12)
  const fromKey = String(s?.key || '').trim()
  if (fromKey) return fromKey.slice(0, 12)
  const rawId = String(s?.id || '')
  return rawId.split('|')[0].slice(0, 12) || '—'
}

function getStreamEngine(s) {
  return s?.container_name || (s?.container_id ? s.container_id.slice(0, 8) : '—')
}

function getStreamMode(s) {
  const ctrl = (s?.control_mode || '').trim().toUpperCase()
  const mode = (s?.stream_mode || '').trim().toUpperCase()
  if (ctrl && mode) return `${ctrl}/${mode}`
  if (ctrl) return ctrl
  if (mode) return mode
  return String(s?.labels?.['stream_mode'] || s?.labels?.['stream.mode'] || '').trim().toUpperCase() || '—'
}

function getStreamBitrate(s) {
  // Use nominal bitrate from labels if available, fallback to measured bitrate
  const nominal = Number(s?.labels?.['stream.nominal_bitrate'] || 0)
  const b = nominal > 0 ? nominal / 1e6 : Number(s?.bitrate_mbps || (s?.bitrate ? (s.bitrate * 8) / 1e6 : 0))
  return Number.isFinite(b) && b > 0 ? b.toFixed(1) + ' Mbps' : '—'
}

function formatSpeed(kbps) {
  if (!kbps || kbps <= 0) return '0 KB/s'
  if (kbps >= 1024) return (kbps / 1024).toFixed(1) + ' MB/s'
  return Math.round(kbps) + ' KB/s'
}

function getStreamSpeedDown(s) {
  return formatSpeed(s?.speed_down)
}

function getStreamSpeedUp(s) {
  return formatSpeed(s?.speed_up)
}

function getStreamBuffer(s) {
  return Number(s?.buffer_fill_percent ?? s?.buffer_seconds ?? 0)
}

function getStreamStarted(s) {
  if (!s?.started_at) return '—'
  try { return new Date(s.started_at).toLocaleTimeString([], { hour12: false }) } catch { return '—' }
}

function getStreamClients(s) {
  if (typeof s?.client_count === 'number') return s.client_count
  if (typeof s?.active_clients === 'number') return s.active_clients
  if (Array.isArray(s?.clients)) return s.clients.length
  return 0
}

function getStreamPeers(s) {
  return typeof s?.peers === 'number' ? s.peers : '—'
}

export function StreamsPage({ streams, orchUrl, apiKey, onStopStream, onDeleteEngine, debugMode }) {
  const active = streams.filter(s => String(s.status || '').toLowerCase() === 'started').length
  const migrations = streams.filter(s => String(s.status || '').toLowerCase().includes('failover')).length
  const totalBitrate = streams.reduce((sum, s) => {
    const b = Number(s.bitrate_mbps || (s.bitrate ? s.bitrate / 1e6 : 0))
    return sum + (Number.isFinite(b) ? b : 0)
  }, 0)

  const sortedStreams = [...streams].sort((a, b) => {
    // migrations first
    const am = String(a.status || '').includes('failover') ? 0 : 1
    const bm = String(b.status || '').includes('failover') ? 0 : 1
    if (am !== bm) return am - bm
    return new Date(b.started_at || 0) - new Date(a.started_at || 0)
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div>
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 18, fontWeight: 600, color: 'var(--fg-0)', margin: 0 }}>Streams</h1>
          <div style={{ fontSize: 12.5, color: 'var(--fg-2)', marginTop: 2 }}>
            {streams.length} active
            {migrations > 0 && ` · ${migrations} migrating`}
            {totalBitrate > 0 && ` · ${totalBitrate.toFixed(1)} Mb/s aggregate`}
          </div>
        </div>
        <div style={{ flex: 1 }}/>
        <span className="tag tag-green"><span className="dot pulse"/> ACTIVE {active}</span>
        {migrations > 0 && <span className="tag tag-magenta"><span className="dot pulse"/> HOT-SWAP {migrations}</span>}
      </div>

      {/* Migration banner */}
      {migrations > 0 && (
        <div style={{ background: 'var(--acc-magenta-bg)', border: '1px solid var(--acc-magenta-dim)', padding: '10px 14px' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
            <span className="label" style={{ color: 'var(--acc-magenta)' }}>STATEFUL MIGRATION · {migrations} stream{migrations > 1 ? 's' : ''}</span>
            <div style={{ flex: 1 }}/>
            <span style={{ fontSize: 11, color: 'var(--fg-2)' }}>HLS · session continuity preserved</span>
          </div>
          <MigrationViz streams={streams}/>
        </div>
      )}

      {/* Streams table */}
      <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-soft)' }}>
        <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)' }}>
          <span className="label">ACTIVE SESSIONS</span>
        </div>
        {streams.length === 0 ? (
          <div style={{ padding: '32px 0', textAlign: 'center', fontSize: 12.5, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
            — no active streams —
          </div>
        ) : (
          <table className="data" style={{ width: '100%' }}>
            <thead>
              <tr>
                <th>ID</th>
                <th>INFOHASH</th>
                <th>ENGINE</th>
                <th>MODE</th>
                <th>CLIENTS</th>
                <th>PEERS</th>
                <th>BITRATE</th>
                <th>DOWN</th>
                <th>UP</th>
                <th style={{ minWidth: 60 }}>BUFFER</th>
                <th>STARTED</th>
                <th>STATUS</th>
                <th/>
              </tr>
            </thead>
            <tbody>
              {sortedStreams.map(s => {
                const isMigrating = String(s.status || '').toLowerCase().includes('failover')
                return (
                  <tr key={s.id} style={{ background: isMigrating ? 'var(--acc-magenta-bg)' : undefined }}>
                    <td style={{ fontWeight: 600, color: 'var(--fg-0)' }}>{String(s.id || '').slice(0, 8) || '—'}</td>
                    <td style={{ color: 'var(--acc-cyan)', fontFamily: 'var(--font-mono)', fontSize: 11 }}>{getStreamInfohash(s)}</td>
                    <td style={{ color: isMigrating ? 'var(--acc-magenta)' : 'var(--fg-1)' }}>{getStreamEngine(s)}</td>
                    <td style={{ color: 'var(--fg-2)' }}>{getStreamMode(s)}</td>
                    <td>{getStreamClients(s)}</td>
                    <td style={{ color: 'var(--fg-1)', fontWeight: 600 }}>{getStreamPeers(s)}</td>
                    <td style={{ color: 'var(--fg-1)', whiteSpace: 'nowrap' }}>{getStreamBitrate(s)}</td>
                    <td style={{ color: 'var(--acc-green)', fontFamily: 'var(--font-mono)', fontSize: 11, whiteSpace: 'nowrap' }}>{getStreamSpeedDown(s)}</td>
                    <td style={{ color: 'var(--acc-amber)', fontFamily: 'var(--font-mono)', fontSize: 11, whiteSpace: 'nowrap' }}>{getStreamSpeedUp(s)}</td>
                    <td style={{ textAlign: 'center' }}><BufferBar value={getStreamBuffer(s)}/></td>
                    <td style={{ color: 'var(--fg-2)' }}>{getStreamStarted(s)}</td>
                    <td><StatusTag status={s.status}/></td>
                    <td style={{ textAlign: 'right' }}>
                      {onStopStream && (
                        <button
                          onClick={() => onStopStream(s.id)}
                          className="tag tag-red"
                          style={{ cursor: 'pointer', padding: '2px 8px', fontSize: 9 }}
                        >✕ STOP</button>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  )
}

function MigrationViz({ streams }) {
  const migrating = streams.filter(s => String(s.status || '').toLowerCase().includes('failover'))
  if (migrating.length === 0) return null

  const W = 700, H = 80
  const stream = migrating[0]

  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" style={{ display: 'block' }}>
      <text x="60" y="14" fontSize="9" fill="var(--fg-3)" fontFamily="var(--font-mono)" letterSpacing="1">SOURCE</text>
      <text x="640" y="14" textAnchor="end" fontSize="9" fill="var(--fg-3)" fontFamily="var(--font-mono)" letterSpacing="1">TARGET</text>

      <rect x="20" y="22" width="120" height="40" fill="var(--bg-0)" stroke="var(--acc-amber)"/>
      <text x="80" y="40" textAnchor="middle" fontSize="11" fill="var(--fg-0)" fontFamily="var(--font-mono)" fontWeight="600">
        {(stream.container_name || stream.container_id || 'source').slice(0, 10)}
      </text>
      <text x="80" y="54" textAnchor="middle" fontSize="9" fill="var(--acc-amber)" fontFamily="var(--font-mono)" letterSpacing="1">DRAINING</text>

      <rect x="560" y="22" width="120" height="40" fill="var(--bg-0)" stroke="var(--acc-green)"/>
      <text x="620" y="40" textAnchor="middle" fontSize="11" fill="var(--fg-0)" fontFamily="var(--font-mono)" fontWeight="600">target</text>
      <text x="620" y="54" textAnchor="middle" fontSize="9" fill="var(--acc-green)" fontFamily="var(--font-mono)" letterSpacing="1">RECEIVING</text>

      <line x1="140" y1="42" x2="560" y2="42" stroke="var(--acc-magenta)" strokeWidth="1" strokeDasharray="3 3"/>
      {[0, 1, 2, 3, 4].map(i => (
        <circle key={i} r="4" fill="var(--acc-magenta)">
          <animateMotion dur="2.4s" begin={`${i * 0.3}s`} repeatCount="indefinite" path="M 140 42 L 560 42"/>
        </circle>
      ))}

      <text x="350" y="32" textAnchor="middle" fontSize="9" fill="var(--acc-magenta)" fontFamily="var(--font-mono)" letterSpacing="1">
        PROXY HOT-SWAP · CLIENT HTTP SOCKET PRESERVED
      </text>
      <text x="350" y="62" textAnchor="middle" fontSize="9" fill="var(--fg-2)" fontFamily="var(--font-mono)">
        stream {stream.id?.slice(0, 8) || '–'} · {String(stream.status || '').toUpperCase().replace(/_/g, '·')}
      </text>
    </svg>
  )
}
