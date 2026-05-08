import React, { useState, useEffect, useRef } from 'react'
import { useNavigate } from 'react-router-dom'

export function ProbesPage({ orchUrl }) {
  const navigate = useNavigate()
  const [contentId, setContentId]  = useState('')
  const [n, setN]                  = useState(5)
  const [selection, setSelection]  = useState('top_score')
  const [running, setRunning]      = useState(false)
  const [jobId, setJobId]          = useState(null)
  const [results, setResults]      = useState([])
  const esRef = useRef(null)

  const run = async () => {
    if (!contentId.trim()) return
    setRunning(true)
    setResults([])
    setJobId(null)

    try {
      const res = await fetch(`${orchUrl}/api/v1/vpn/reputation/probe`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content_id: contentId.trim(), n, selection }),
      })
      const data = await res.json()
      setJobId(data.job_id)

      // Subscribe to SSE for live results.
      const url = new URL(`${orchUrl}/api/v1/vpn/reputation/stream`)
      const es = new EventSource(url.toString())
      esRef.current = es

      es.addEventListener('vpn.probe.completed', e => {
        try {
          const p = JSON.parse(e.data)
          if (p.job_id === data.job_id || !p.job_id) {
            setResults(prev => [p, ...prev])
          }
        } catch { /* ignore */ }
      })
    } catch (err) {
      console.error(err)
    } finally {
      setRunning(false)
    }
  }

  useEffect(() => () => esRef.current?.close(), [])

  return (
    <div style={{ padding: 20, maxWidth: 800, fontFamily: 'var(--font-mono)' }}>
      <button onClick={() => navigate('/vpn')} style={backBtn}>← VPN.REPUTATION</button>

      <div style={{ fontSize: 13, fontWeight: 600, color: 'var(--fg-0)', letterSpacing: '0.04em', marginTop: 12, marginBottom: 16 }}>
        MANUAL PROBE RUNNER
      </div>

      {/* Form */}
      <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line)', padding: 16, borderRadius: 2 }}>
        <Row label="content_id">
          <input
            value={contentId}
            onChange={e => setContentId(e.target.value)}
            placeholder="acid:7c3a…a8f9"
            style={inputStyle}
          />
        </Row>

        <Row label="N nodes">
          <input
            type="number"
            min={1} max={50}
            value={n}
            onChange={e => setN(Number(e.target.value))}
            style={{ ...inputStyle, width: 80 }}
          />
        </Row>

        <Row label="selection">
          <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
            {[
              ['top_score',          'Top score'],
              ['stratified_country', 'Stratified by country'],
              ['random',             'Random'],
            ].map(([val, label]) => (
              <label key={val} style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer', fontSize: 11, color: 'var(--fg-2)' }}>
                <input
                  type="radio"
                  name="selection"
                  value={val}
                  checked={selection === val}
                  onChange={() => setSelection(val)}
                  style={{ accentColor: 'var(--acc-green)' }}
                />
                {label}
              </label>
            ))}
          </div>
        </Row>

        <button
          onClick={run}
          disabled={running || !contentId.trim()}
          style={{
            marginTop: 12,
            background: running ? 'none' : 'var(--acc-green)',
            color: running ? 'var(--fg-3)' : 'var(--bg-0)',
            border: `1px solid ${running ? 'var(--line)' : 'var(--acc-green)'}`,
            cursor: running ? 'not-allowed' : 'pointer',
            fontSize: 11,
            fontFamily: 'var(--font-mono)',
            padding: '6px 16px',
            borderRadius: 2,
            fontWeight: 600,
            letterSpacing: '0.04em',
          }}
        >
          {running ? '⌛ RUNNING…' : '▶ RUN PROBE'}
        </button>

        {jobId && (
          <div style={{ marginTop: 8, fontSize: 10, color: 'var(--fg-3)' }}>
            job_id: {jobId}
          </div>
        )}
      </div>

      {/* Results */}
      {results.length > 0 && (
        <div style={{ marginTop: 16, background: 'var(--bg-1)', border: '1px solid var(--line)', padding: 16, borderRadius: 2 }}>
          <div style={{ fontSize: 9, letterSpacing: '0.08em', color: 'var(--fg-3)', fontWeight: 600, marginBottom: 8 }}>RESULTS ({results.length})</div>
          {results.map((r, i) => (
            <div key={i} style={{ display: 'flex', gap: 12, padding: '4px 0', borderBottom: '1px solid var(--line-soft)', fontSize: 11 }}>
              <span style={{ color: r.outcome === 'success' ? 'var(--acc-green)' : 'var(--acc-red)', minWidth: 80 }}>{r.outcome}</span>
              <span style={{ color: 'var(--fg-3)', minWidth: 80 }}>{r.server_id}</span>
              <span style={{ color: 'var(--fg-2)' }}>{r.ttfb_ms != null ? `${r.ttfb_ms}ms` : ''}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function Row({ label, children }) {
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 16, marginBottom: 12 }}>
      <span style={{ fontSize: 11, color: 'var(--fg-3)', minWidth: 100, paddingTop: 3 }}>{label}:</span>
      {children}
    </div>
  )
}

const inputStyle = {
  background: 'var(--bg-2)',
  border: '1px solid var(--line)',
  color: 'var(--fg-1)',
  fontFamily: 'var(--font-mono)',
  fontSize: 11,
  padding: '4px 8px',
  borderRadius: 2,
  width: 280,
  outline: 'none',
}

const backBtn = {
  background: 'none',
  border: 'none',
  color: 'var(--acc-cyan, #22d3ee)',
  cursor: 'pointer',
  fontSize: 11,
  fontFamily: 'var(--font-mono)',
  padding: 0,
}
