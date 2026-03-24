import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { AlertCircle, Activity, Users, Gauge, Cpu, Server, ShieldCheck, Network } from 'lucide-react'
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

function Pane({ title, subtitle, icon: Icon, children, className = '' }) {
  return (
    <Card className={`border-slate-200 bg-white/80 shadow-sm backdrop-blur dark:border-slate-800 dark:bg-slate-950/70 ${className}`}>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-sm font-semibold text-slate-700 dark:text-slate-200">
          <span className="rounded-md bg-slate-100 p-1.5 dark:bg-slate-800">
            <Icon className="h-4 w-4" />
          </span>
          {title}
        </CardTitle>
        {subtitle && <p className="text-xs text-slate-500 dark:text-slate-400">{subtitle}</p>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  )
}

function StatTile({ label, value, hint }) {
  return (
    <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900">
      <p className="text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">{label}</p>
      <p className="mt-1 text-xl font-semibold text-slate-900 dark:text-slate-50">{value}</p>
      {hint && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{hint}</p>}
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

  const fetchSnapshot = useCallback(async () => {
    try {
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }

      const response = await fetch(`${orchUrl}/metrics/dashboard?window_seconds=${windowSeconds}&max_points=360`, { headers })
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`)
      }

      const data = await response.json()
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
      }
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [orchUrl, apiKey, windowSeconds])

  useEffect(() => {
    setLoading(true)
    fetchSnapshot()
    const interval = setInterval(fetchSnapshot, 1000)
    return () => clearInterval(interval)
  }, [fetchSnapshot])

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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-slate-900 dark:text-slate-100">Streaming Observability</h1>
          <p className="mt-1 text-slate-600 dark:text-slate-400">Pane-based dashboard for RED proxy telemetry, USE infrastructure metrics, and stream health.</p>
        </div>
        <div className="flex items-center gap-3">
          <label className="text-sm text-slate-600 dark:text-slate-300">Window</label>
          <select
            className="rounded-md border border-slate-300 bg-white px-2 py-1 text-sm text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-200"
            value={windowSeconds}
            onChange={(e) => setWindowSeconds(parseInt(e.target.value, 10))}
          >
            {WINDOW_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>{opt.label}</option>
            ))}
          </select>
          <Badge variant={loading ? 'secondary' : 'outline'}>
            {loading ? 'Refreshing...' : 'Live'}
          </Badge>
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-4">
        <StatTile label="Global Active Streams" value={snapshot?.north_star?.global_active_streams || 0} hint="Current viewers" />
        <StatTile
          label="Global Egress Total"
          value={formatBytes(snapshot?.proxy?.throughput?.window_egress_total_bytes || 0)}
          hint={`Total over selected window (${windowLabel})`}
        />
        <StatTile
          label="Global Ingress Total"
          value={formatBytes(snapshot?.proxy?.throughput?.window_ingress_total_bytes || 0)}
          hint={`Total over selected window (${windowLabel})`}
        />
        <StatTile label="Proxy Active Clients" value={snapshot?.north_star?.proxy_active_clients || 0} hint="TS + HLS clients" />
      </div>

      <div className="grid gap-5 xl:grid-cols-12">
        <Pane title="Proxy Throughput" subtitle="Ingress vs egress rate trend" icon={Network} className="xl:col-span-8">
          <div style={{ height: '260px' }}>
            <Line
              data={dualChartData(
                { label: 'Ingress Mbps', data: history.ingressMbps, border: 'rgb(14, 165, 233)', bg: 'rgba(14, 165, 233, 0.16)' },
                { label: 'Egress Mbps', data: history.egressMbps, border: 'rgb(16, 185, 129)', bg: 'rgba(16, 185, 129, 0.16)' }
              )}
              options={lineOptions('Proxy Throughput', 'Mbps')}
            />
          </div>
          <p className="mt-3 text-xs text-slate-500 dark:text-slate-400">
            Rates are shown in Mbps (megabits per second). For reference: 1 MB/s = 8 Mbps.
          </p>
        </Pane>

        <Pane title="Request Reliability" subtitle="RED summary" icon={ShieldCheck} className="xl:col-span-4">
          <div className="grid gap-3">
            <StatTile
              label="Success Rate"
              value={formatPercent(snapshot?.proxy?.request_window_1m?.success_rate_percent)}
              hint={`${snapshot?.proxy?.request_window_1m?.total_requests_1m || 0} requests in last minute`}
            />
            <div className="grid grid-cols-2 gap-3">
              <StatTile label="4xx/min" value={snapshot?.proxy?.request_window_1m?.error_4xx_rate_per_min || 0} />
              <StatTile label="5xx/min" value={snapshot?.proxy?.request_window_1m?.error_5xx_rate_per_min || 0} />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <StatTile label="TTFB avg" value={`${(snapshot?.proxy?.ttfb?.avg_ms || 0).toFixed(1)} ms`} />
              <StatTile label="TTFB p95" value={`${(snapshot?.proxy?.ttfb?.p95_ms || 0).toFixed(1)} ms`} />
            </div>
          </div>
        </Pane>

        <Pane title="Connections & Streams" subtitle="Current client pressure" icon={Users} className="xl:col-span-6">
          <div className="mb-3 grid grid-cols-2 gap-3">
            <StatTile label="Active Clients" value={snapshot?.north_star?.proxy_active_clients || 0} hint={`TS ${snapshot?.proxy?.active_clients?.ts || 0} | HLS ${snapshot?.proxy?.active_clients?.hls || 0}`} />
            <StatTile label="Active Streams" value={snapshot?.north_star?.global_active_streams || 0} hint={`${snapshot?.engines?.used || 0} engines used`} />
          </div>
          <div style={{ height: '220px' }}>
            <Line
              data={dualChartData(
                { label: 'Active Clients', data: history.activeClients, border: 'rgb(249, 115, 22)', bg: 'rgba(249, 115, 22, 0.18)' },
                { label: 'Active Streams', data: history.activeStreams, border: 'rgb(59, 130, 246)', bg: 'rgba(59, 130, 246, 0.14)' }
              )}
              options={lineOptions('Load Footprint', 'Count')}
            />
          </div>
        </Pane>

        <Pane title="Engine State" subtitle="Healthy routing capacity" icon={Server} className="xl:col-span-3">
          <div className="mb-3 grid grid-cols-2 gap-3">
            <StatTile label="Total" value={snapshot?.engines?.total || 0} />
            <StatTile label="Healthy" value={snapshot?.engines?.healthy || 0} />
            <StatTile label="Unhealthy" value={snapshot?.engines?.unhealthy || 0} />
            <StatTile label="Uptime avg" value={`${Math.round((snapshot?.engines?.uptime_avg_seconds || 0) / 60)} min`} />
          </div>
          <div style={{ height: '170px' }}>
            <Bar
              data={{
                labels: ['Playing', 'Idle', 'Unhealthy', 'Unknown'],
                datasets: [{
                  data: [engineState.playing || 0, engineState.idle || 0, engineState.unhealthy || 0, engineState.unknown || 0],
                  backgroundColor: ['rgba(16,185,129,0.7)', 'rgba(59,130,246,0.7)', 'rgba(239,68,68,0.7)', 'rgba(148,163,184,0.7)'],
                  borderColor: ['rgb(16,185,129)', 'rgb(59,130,246)', 'rgb(239,68,68)', 'rgb(148,163,184)'],
                  borderWidth: 1,
                }],
              }}
              options={barOptions}
            />
          </div>
        </Pane>

        <Pane title="Infrastructure USE" subtitle="Docker saturation and utilization" icon={Cpu} className="xl:col-span-3">
          <div className="grid gap-3">
            <StatTile label="CPU" value={formatPercent(snapshot?.docker?.cpu_percent)} />
            <StatTile label="Memory" value={formatBytes(snapshot?.docker?.memory_usage || 0)} />
            <StatTile label="Restart Total" value={snapshot?.docker?.restart_total || 0} />
            <StatTile label="OOM Killed" value={snapshot?.docker?.oom_killed_total || 0} />
          </div>
        </Pane>

        <Pane title="Latency & System Drift" subtitle="P95 TTFB and success trend" icon={Gauge} className="xl:col-span-6">
          <div style={{ height: '240px' }}>
            <Line
              data={{
                labels,
                datasets: [
                  {
                    label: 'TTFB p95 (ms)',
                    data: history.ttfbP95Ms,
                    borderColor: 'rgb(239,68,68)',
                    backgroundColor: 'rgba(239,68,68,0.14)',
                    tension: 0.28,
                    fill: true,
                    yAxisID: 'y',
                  },
                  {
                    label: 'Success Rate (%)',
                    data: history.successRate,
                    borderColor: 'rgb(22,163,74)',
                    backgroundColor: 'rgba(22,163,74,0.12)',
                    tension: 0.28,
                    fill: true,
                    yAxisID: 'y2',
                  },
                ],
              }}
              options={{
                responsive: true,
                maintainAspectRatio: false,
                interaction: { mode: 'index', intersect: false },
                scales: {
                  y: { beginAtZero: true, title: { display: true, text: 'ms' } },
                  y2: {
                    position: 'right',
                    min: 0,
                    max: 100,
                    grid: { drawOnChartArea: false },
                    title: { display: true, text: '%' },
                  },
                },
              }}
            />
          </div>
        </Pane>

        <Pane title="Stream Health" subtitle="Peers, buffer health, and active keys" icon={Activity} className="xl:col-span-6">
          <div className="mb-3 grid grid-cols-2 gap-3">
            <StatTile label="Total Peers" value={snapshot?.streams?.total_peers || 0} />
            <StatTile label="Buffer Avg Pieces" value={snapshot?.streams?.buffer?.avg_pieces || 0} />
            <StatTile label="Buffer Min Pieces" value={snapshot?.streams?.buffer?.min_pieces || 0} />
            <StatTile label="Download Speed" value={`${(snapshot?.streams?.download_speed_mbps || 0).toFixed(2)} MB/s`} />
          </div>
          <div className="rounded-xl border border-slate-200 bg-slate-50 p-3 dark:border-slate-800 dark:bg-slate-900">
            <p className="mb-2 text-xs uppercase tracking-wide text-slate-500 dark:text-slate-400">Active Infohash / Content Keys</p>
            <div className="max-h-28 space-y-1 overflow-auto pr-1">
              {activeKeys.length === 0 ? (
                <p className="text-xs text-slate-500 dark:text-slate-400">No active streams</p>
              ) : (
                activeKeys.map((key) => (
                  <div key={key} className="truncate rounded-md bg-white px-2 py-1 text-xs text-slate-700 dark:bg-slate-950 dark:text-slate-300">
                    {key}
                  </div>
                ))
              )}
            </div>
          </div>
        </Pane>
      </div>

      <div className="grid gap-5 md:grid-cols-2">
        <Pane title="CPU Trend" subtitle="Container utilization over time" icon={Cpu}>
          <div style={{ height: '180px' }}>
            <Line
              data={chartData('CPU %', history.cpuPercent, 'rgb(234, 88, 12)', 'rgba(234, 88, 12, 0.14)')}
              options={lineOptions('CPU', '%')}
            />
          </div>
        </Pane>
        <Pane title="Memory Trend" subtitle="Container memory footprint" icon={Server}>
          <div style={{ height: '180px' }}>
            <Line
              data={chartData('Memory Bytes', history.memoryBytes, 'rgb(59, 130, 246)', 'rgba(59, 130, 246, 0.16)')}
              options={lineOptions('Memory', 'bytes')}
            />
          </div>
        </Pane>
      </div>
    </div>
  )
}