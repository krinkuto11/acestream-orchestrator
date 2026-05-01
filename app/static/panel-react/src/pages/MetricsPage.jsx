import React, { useState, useEffect, useCallback } from 'react'
import { formatBytes } from '../utils/formatters'
import { Line, Bar } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend,
} from 'chart.js'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  BarElement,
  Title,
  Tooltip,
  Legend
)

function Pane({ title, subtitle, children, span }) {
  return (
    <div style={{
      background: 'var(--bg-1)',
      border: '1px solid var(--line-soft)',
      display: 'flex', flexDirection: 'column',
      gridColumn: span ? `span ${span}` : undefined,
    }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)' }}>
        <span className="label">{title}</span>
        {subtitle && <span style={{ fontSize: 10, color: 'var(--fg-3)', marginLeft: 8 }}>{subtitle}</span>}
      </div>
      <div style={{ padding: 14, flex: 1 }}>{children}</div>
    </div>
  )
}

function StatTile({ label, value, hint, accent = 'fg-0' }) {
  return (
    <div style={{
      padding: '10px 12px',
      background: 'var(--bg-0)',
      border: '1px solid var(--line-soft)',
    }}>
      <div className="label" style={{ marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 20, fontWeight: 600, color: `var(--${accent})`, fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.02em' }}>{value}</div>
      {hint && <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 2 }}>{hint}</div>}
    </div>
  )
}

function formatMbps(value) {
  return `${(Number(value) || 0).toFixed(2)} Mbps`
}

function formatPercent(value) {
  return `${(Number(value) || 0).toFixed(2)}%`
}

function formatWindowLabel(seconds) {
  const value = Number(seconds) || 0
  if (value >= 86400) return `${Math.round(value / 86400)}d`
  if (value >= 3600) return `${Math.round(value / 3600)}h`
  return `${Math.round(value / 60)}m`
}

const MAX_HISTORY_POINTS = 360

const resolveTimestamp = (value) => {
  if (!value) return new Date()
  const numeric = Number(value)
  if (Number.isFinite(numeric)) {
    return new Date(numeric < 1_000_000_000_000 ? numeric * 1000 : numeric)
  }
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? new Date() : parsed
}

export function MetricsPage({ apiKey, orchUrl }) {
  const WINDOW_OPTIONS = [
    { label: '5m', value: 300 },
    { label: '15m', value: 900 },
    { label: '30m', value: 1800 },
    { label: '1h', value: 3600 },
    { label: '6h', value: 21600 },
    { label: '24h', value: 86400 },
  ]

  const [windowSeconds, setWindowSeconds] = useState(900)
  const [snapshot, setSnapshot] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [history, setHistory] = useState({
    timestamps: [],
    egressMbps: [],
    ingressMbps: [],
    activeStreams: [],
    activeClients: [],
    successRate: [],
    ttfbP95Ms: [],
    cpuPercent: [],
    memoryBytes: [],
  })

  const applySnapshot = useCallback((data) => {
    if (!data) return
    setSnapshot(data)
    setError(null)

    const persistedHistory = data?.history
    if (persistedHistory && Array.isArray(persistedHistory.timestamps)) {
      setHistory({
        timestamps: persistedHistory.timestamps.map(ts => new Date(ts)),
        egressMbps: persistedHistory.egressMbps || [],
        ingressMbps: persistedHistory.ingressMbps || [],
        activeStreams: persistedHistory.activeStreams || [],
        activeClients: persistedHistory.activeClients || [],
        successRate: persistedHistory.successRate || [],
        ttfbP95Ms: persistedHistory.ttfbP95Ms || [],
        cpuPercent: persistedHistory.cpuPercent || [],
        memoryBytes: persistedHistory.memoryBytes || [],
      })
      return
    }

    const safeNumber = (value, fallback) => {
      const num = Number(value)
      return Number.isFinite(num) ? num : fallback
    }

    setHistory((prev) => {
      const timestamps = [...prev.timestamps, resolveTimestamp(data?.timestamp)]
      const activeStreams = [...prev.activeStreams, safeNumber(
        data?.streams_active ?? data?.orchestrator_status?.streams?.active ?? data?.streams?.active,
        prev.activeStreams[prev.activeStreams.length - 1] ?? 0,
      )]
      const activeClients = [...prev.activeClients, safeNumber(
        data?.proxy?.active_clients?.total ?? data?.north_star?.proxy_active_clients,
        prev.activeClients[prev.activeClients.length - 1] ?? 0,
      )]
      const successRate = [...prev.successRate, safeNumber(
        data?.proxy?.request_window_1m?.success_rate_percent ?? data?.north_star?.system_success_rate_percent,
        prev.successRate[prev.successRate.length - 1] ?? 0,
      )]
      const egressMbps = [...prev.egressMbps, safeNumber(
        data?.proxy?.throughput?.egress_mbps ?? data?.north_star?.global_egress_bandwidth_mbps,
        prev.egressMbps[prev.egressMbps.length - 1] ?? 0,
      )]
      const ingressMbps = [...prev.ingressMbps, safeNumber(
        data?.proxy?.throughput?.ingress_mbps,
        prev.ingressMbps[prev.ingressMbps.length - 1] ?? 0,
      )]
      const ttfbP95Ms = [...prev.ttfbP95Ms, safeNumber(
        data?.proxy?.request_window_1m?.ttfb_p95_ms ?? data?.proxy?.ttfb?.p95_ms,
        prev.ttfbP95Ms[prev.ttfbP95Ms.length - 1] ?? 0,
      )]
      const cpuPercent = [...prev.cpuPercent, safeNumber(
        data?.docker?.cpu_percent ?? data?.docker?.cpu,
        prev.cpuPercent[prev.cpuPercent.length - 1] ?? 0,
      )]
      const memoryBytes = [...prev.memoryBytes, safeNumber(
        data?.docker?.memory_usage,
        prev.memoryBytes[prev.memoryBytes.length - 1] ?? 0,
      )]

      const trim = (arr) => arr.length <= MAX_HISTORY_POINTS ? arr : arr.slice(arr.length - MAX_HISTORY_POINTS)

      return {
        timestamps: trim(timestamps),
        egressMbps: trim(egressMbps),
        ingressMbps: trim(ingressMbps),
        activeStreams: trim(activeStreams),
        activeClients: trim(activeClients),
        successRate: trim(successRate),
        ttfbP95Ms: trim(ttfbP95Ms),
        cpuPercent: trim(cpuPercent),
        memoryBytes: trim(memoryBytes),
      }
    })
  }, [])

  const fetchSnapshot = useCallback(async () => {
    try {
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }

      const response = await fetch(`${orchUrl}/api/v1/metrics/dashboard?window_seconds=${windowSeconds}&max_points=360`, { headers })
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`)
      }

      const data = await response.json()
      applySnapshot(data)
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [orchUrl, apiKey, windowSeconds, applySnapshot])

  useEffect(() => {
    let eventSource = null
    let reconnectTimer = null
    let closed = false

    const connect = () => {
      if (closed) {
        return
      }

      if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
        fetchSnapshot()
        return
      }

      const streamUrl = new URL(`${orchUrl}/api/v1/metrics/stream`)
      streamUrl.searchParams.set('window_seconds', String(windowSeconds))
      streamUrl.searchParams.set('max_points', '360')
      if (apiKey) {
        streamUrl.searchParams.set('api_key', apiKey)
      }

      eventSource = new EventSource(streamUrl.toString())

      const handleMetricsSnapshot = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          const payload = parsed?.payload ?? parsed
          applySnapshot(payload)
          setLoading(false)
        } catch (err) {
          console.error('Failed to parse metrics SSE payload:', err)
        }
      }

      eventSource.addEventListener('metrics_snapshot', handleMetricsSnapshot)
      eventSource.onmessage = handleMetricsSnapshot

      eventSource.onerror = () => {
        setLoading(false)
        if (eventSource) {
          eventSource.close()
          eventSource = null
        }

        if (!closed) {
          reconnectTimer = window.setTimeout(connect, 2000)
        }
      }
    }

    setLoading(true)
    connect()

    return () => {
      closed = true
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer)
      }
      if (eventSource) {
        eventSource.close()
      }
    }
  }, [orchUrl, apiKey, windowSeconds, fetchSnapshot, applySnapshot])

  const labels = history.timestamps.map(ts => ts.toLocaleTimeString())
  const chartData = (label, data, borderColor, backgroundColor) => ({
    labels,
    datasets: [{
      label,
      data,
      borderColor,
      backgroundColor,
      tension: 0.3,
      fill: true,
    }],
  })

  const dualChartData = (a, b) => ({
    labels,
    datasets: [
      {
        label: a.label,
        data: a.data,
        borderColor: a.border,
        backgroundColor: a.bg,
        tension: 0.3,
        fill: true,
      },
      {
        label: b.label,
        data: b.data,
        borderColor: b.border,
        backgroundColor: b.bg,
        tension: 0.3,
        fill: true,
      }
    ],
  })

  const lineOptions = (title, yAxisLabel) => ({
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index',
      intersect: false,
    },
    plugins: {
      legend: {
        position: 'top',
      },
      title: {
        display: true,
        text: title,
      },
    },
    scales: {
      y: {
        type: 'linear',
        display: true,
        beginAtZero: true,
        title: {
          display: true,
          text: yAxisLabel,
        },
      },
    },
  })

  const barOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        display: false,
      },
    },
    scales: {
      y: {
        beginAtZero: true,
      },
    },
  }

  const engineState = snapshot?.engines?.state_counts || {}
  const activeKeys = snapshot?.streams?.active_keys || []
  const effectiveWindowSeconds = snapshot?.observation_window_seconds || windowSeconds
  const windowLabel = formatWindowLabel(effectiveWindowSeconds)

  const grid4 = { display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }
  const grid2 = { display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 8 }
  const grid12 = { display: 'grid', gridTemplateColumns: 'repeat(12, 1fr)', gap: 12 }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div>
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 18, fontWeight: 600, color: 'var(--fg-0)', margin: 0 }}>Dashboard</h1>
          <div style={{ fontSize: 11, color: 'var(--fg-2)', marginTop: 2 }}>
            RED proxy telemetry · USE infrastructure · stream health
          </div>
        </div>
        <div style={{ flex: 1 }}/>
        <span style={{ fontSize: 10, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)' }}>window</span>
        <select
          value={windowSeconds}
          onChange={(e) => setWindowSeconds(parseInt(e.target.value, 10))}
          style={{
            background: 'var(--bg-0)', border: '1px solid var(--line)',
            color: 'var(--fg-0)', padding: '3px 8px',
            fontSize: 11, fontFamily: 'var(--font-mono)', cursor: 'pointer',
          }}
        >
          {WINDOW_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
        <span className={`tag ${loading ? '' : 'tag-green'}`}>
          <span className="dot pulse" style={{ color: loading ? 'var(--acc-amber)' : 'var(--acc-green)' }}/>
          {loading ? 'SYNCING' : 'LIVE'}
        </span>
      </div>

      {error && (
        <div style={{
          background: 'var(--acc-red-bg)', border: '1px solid var(--acc-red-dim)',
          padding: '8px 12px', fontSize: 11, color: 'var(--acc-red)',
          display: 'flex', gap: 8, alignItems: 'center',
        }}>
          <span>✕</span><span>{error}</span>
        </div>
      )}

      {/* Top KPI tiles */}
      <div style={grid4}>
        <StatTile label="ACTIVE STREAMS" value={snapshot?.north_star?.global_active_streams || 0} hint="current viewers" accent="acc-green"/>
        <StatTile label="EGRESS TOTAL" value={formatBytes(snapshot?.proxy?.throughput?.window_egress_total_bytes || 0)} hint={`over ${windowLabel}`}/>
        <StatTile label="INGRESS TOTAL" value={formatBytes(snapshot?.proxy?.throughput?.window_ingress_total_bytes || 0)} hint={`over ${windowLabel}`}/>
        <StatTile label="ACTIVE CLIENTS" value={snapshot?.north_star?.proxy_active_clients || 0} hint="TS + HLS"/>
      </div>

      {/* Chart grid */}
      <div style={grid12}>
        {/* Throughput 8/12 */}
        <div style={{ gridColumn: 'span 8' }}>
          <Pane title="PROXY.THROUGHPUT" subtitle="ingress vs egress rate">
            <div style={{ height: 240 }}>
              <Line
                data={dualChartData(
                  { label: 'Ingress Mbps', data: history.ingressMbps, border: 'rgb(14, 165, 233)', bg: 'rgba(14, 165, 233, 0.16)' },
                  { label: 'Egress Mbps', data: history.egressMbps, border: 'rgb(16, 185, 129)', bg: 'rgba(16, 185, 129, 0.16)' }
                )}
                options={lineOptions('Proxy Throughput', 'Mbps')}
              />
            </div>
            <div style={{ fontSize: 9, color: 'var(--fg-3)', marginTop: 8 }}>
              Rates in Mbps · 1 MB/s = 8 Mbps
            </div>
          </Pane>
        </div>

        {/* Reliability 4/12 */}
        <div style={{ gridColumn: 'span 4' }}>
          <Pane title="REQUEST.RELIABILITY" subtitle="RED summary">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <StatTile label="SUCCESS RATE" value={formatPercent(snapshot?.proxy?.request_window_1m?.success_rate_percent)} hint={`${snapshot?.proxy?.request_window_1m?.total_requests_1m || 0} req/min`} accent="acc-green"/>
              <div style={grid2}>
                <StatTile label="4XX/MIN" value={snapshot?.proxy?.request_window_1m?.error_4xx_rate_per_min || 0}/>
                <StatTile label="5XX/MIN" value={snapshot?.proxy?.request_window_1m?.error_5xx_rate_per_min || 0}/>
                <StatTile label="TTFB AVG" value={`${(snapshot?.proxy?.ttfb?.avg_ms || 0).toFixed(1)} ms`}/>
                <StatTile label="TTFB P95" value={`${(snapshot?.proxy?.ttfb?.p95_ms || 0).toFixed(1)} ms`}/>
              </div>
            </div>
          </Pane>
        </div>

        {/* Connections 6/12 */}
        <div style={{ gridColumn: 'span 6' }}>
          <Pane title="CONNECTIONS.STREAMS" subtitle="client pressure">
            <div style={{ ...grid2, marginBottom: 10 }}>
              <StatTile label="ACTIVE CLIENTS" value={snapshot?.north_star?.proxy_active_clients || 0} hint={`TS ${snapshot?.proxy?.active_clients?.ts || 0} | HLS ${snapshot?.proxy?.active_clients?.hls || 0}`}/>
              <StatTile label="ACTIVE STREAMS" value={snapshot?.north_star?.global_active_streams || 0} hint={`${snapshot?.engines?.used || 0} engines used`}/>
            </div>
            <div style={{ height: 200 }}>
              <Line
                data={dualChartData(
                  { label: 'Active Clients', data: history.activeClients, border: 'rgb(249, 115, 22)', bg: 'rgba(249, 115, 22, 0.18)' },
                  { label: 'Active Streams', data: history.activeStreams, border: 'rgb(59, 130, 246)', bg: 'rgba(59, 130, 246, 0.14)' }
                )}
                options={lineOptions('Load Footprint', 'Count')}
              />
            </div>
          </Pane>
        </div>

        {/* Engine state 3/12 */}
        <div style={{ gridColumn: 'span 3' }}>
          <Pane title="ENGINE.STATE" subtitle="routing capacity">
            <div style={{ ...grid2, marginBottom: 10 }}>
              <StatTile label="TOTAL" value={snapshot?.engines?.total || 0}/>
              <StatTile label="HEALTHY" value={snapshot?.engines?.healthy || 0} accent="acc-green"/>
              <StatTile label="UNHEALTHY" value={snapshot?.engines?.unhealthy || 0}/>
              <StatTile label="UPTIME AVG" value={`${Math.round((snapshot?.engines?.uptime_avg_seconds || 0) / 60)} min`}/>
            </div>
            <div style={{ height: 150 }}>
              <Bar
                data={{
                  labels: ['Playing', 'Idle', 'Unhealthy', 'Unknown'],
                  datasets: [{
                    data: [engineState.playing || 0, engineState.idle || 0, engineState.unhealthy || 0, engineState.unknown || 0],
                    backgroundColor: ['rgba(16,185,129,0.7)', 'rgba(59,130,246,0.7)', 'rgba(239,68,68,0.7)', 'rgba(100,116,139,0.7)'],
                    borderColor: ['rgb(16,185,129)', 'rgb(59,130,246)', 'rgb(239,68,68)', 'rgb(100,116,139)'],
                    borderWidth: 1,
                  }],
                }}
                options={barOptions}
              />
            </div>
          </Pane>
        </div>

        {/* Infrastructure USE 3/12 */}
        <div style={{ gridColumn: 'span 3' }}>
          <Pane title="INFRASTRUCTURE.USE" subtitle="Docker saturation">
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              <StatTile label="CPU" value={formatPercent(snapshot?.docker?.cpu_percent)}/>
              <StatTile label="MEMORY" value={formatBytes(snapshot?.docker?.memory_usage || 0)}/>
              <StatTile label="RESTARTS" value={snapshot?.docker?.restart_total || 0}/>
              <StatTile label="OOM KILLED" value={snapshot?.docker?.oom_killed_total || 0}/>
            </div>
          </Pane>
        </div>

        {/* Latency 6/12 */}
        <div style={{ gridColumn: 'span 6' }}>
          <Pane title="LATENCY.DRIFT" subtitle="P95 TTFB + success rate trend">
            <div style={{ height: 220 }}>
              <Line
                data={{
                  labels,
                  datasets: [
                    { label: 'TTFB p95 (ms)', data: history.ttfbP95Ms, borderColor: 'rgb(239,68,68)', backgroundColor: 'rgba(239,68,68,0.14)', tension: 0.28, fill: true, yAxisID: 'y' },
                    { label: 'Success Rate (%)', data: history.successRate, borderColor: 'rgb(22,163,74)', backgroundColor: 'rgba(22,163,74,0.12)', tension: 0.28, fill: true, yAxisID: 'y2' },
                  ],
                }}
                options={{
                  responsive: true, maintainAspectRatio: false,
                  interaction: { mode: 'index', intersect: false },
                  scales: {
                    y: { beginAtZero: true, title: { display: true, text: 'ms' } },
                    y2: { position: 'right', min: 0, max: 100, grid: { drawOnChartArea: false }, title: { display: true, text: '%' } },
                  },
                }}
              />
            </div>
          </Pane>
        </div>

        {/* Stream health 6/12 */}
        <div style={{ gridColumn: 'span 6' }}>
          <Pane title="STREAM.HEALTH" subtitle="peers, buffer, active keys">
            <div style={{ ...grid2, marginBottom: 10 }}>
              <StatTile label="TOTAL PEERS" value={snapshot?.streams?.total_peers || 0}/>
              <StatTile label="BUFFER AVG" value={snapshot?.streams?.buffer?.avg_pieces || 0}/>
              <StatTile label="BUFFER MIN" value={snapshot?.streams?.buffer?.min_pieces || 0}/>
              <StatTile label="DL SPEED" value={`${(snapshot?.streams?.download_speed_mbps || 0).toFixed(2)} Mb/s`}/>
            </div>
            <div className="label" style={{ marginBottom: 6 }}>ACTIVE INFOHASH KEYS</div>
            <div style={{ maxHeight: 100, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: 2 }}>
              {activeKeys.length === 0 ? (
                <span style={{ fontSize: 10, color: 'var(--fg-3)', fontStyle: 'italic' }}>— no active streams —</span>
              ) : (
                activeKeys.map(key => (
                  <div key={key} style={{
                    padding: '2px 8px', background: 'var(--bg-0)',
                    fontSize: 10, color: 'var(--fg-1)', fontFamily: 'var(--font-mono)',
                    overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  }}>{key}</div>
                ))
              )}
            </div>
          </Pane>
        </div>

        {/* CPU trend 6/12 */}
        <div style={{ gridColumn: 'span 6' }}>
          <Pane title="CPU.TREND" subtitle="container utilization">
            <div style={{ height: 180 }}>
              <Line data={chartData('CPU %', history.cpuPercent, 'rgb(234, 88, 12)', 'rgba(234, 88, 12, 0.14)')} options={lineOptions('CPU', '%')}/>
            </div>
          </Pane>
        </div>

        {/* Memory trend 6/12 */}
        <div style={{ gridColumn: 'span 6' }}>
          <Pane title="MEMORY.TREND" subtitle="container footprint">
            <div style={{ height: 180 }}>
              <Line data={chartData('Memory Bytes', history.memoryBytes, 'rgb(59, 130, 246)', 'rgba(59, 130, 246, 0.16)')} options={lineOptions('Memory', 'bytes')}/>
            </div>
          </Pane>
        </div>
      </div>
    </div>
  )
}