import React, { useEffect, useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import { AlertTriangle, GaugeCircle, KeyRound, Network, Server, ShieldAlert, Tv, Waves, Workflow } from 'lucide-react'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { HoverCard, HoverCardContent, HoverCardTrigger } from '@/components/ui/hover-card'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { ScrollArea } from '@/components/ui/scroll-area'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { RoutingTopologyPage } from '@/pages/RoutingTopologyPage'
import { cn } from '@/lib/utils'
import { useStreamingCentralStore } from '@/stores/streamingCentralStore'
import { useTheme } from '@/components/ThemeProvider'
import { CHART_SERIES, getChartTheme } from '@/lib/chartTheme'

const sparklineOption = (points, color) => ({
  animation: false,
  grid: { left: 2, right: 2, top: 4, bottom: 2 },
  xAxis: {
    type: 'category',
    data: points.map((_, idx) => idx),
    show: false,
  },
  yAxis: {
    type: 'value',
    show: false,
    scale: true,
  },
  tooltip: { show: false },
  series: [
    {
      type: 'line',
      data: points,
      smooth: 0.2,
      symbol: 'none',
      lineStyle: {
        width: 2,
        color,
      },
      areaStyle: {
        color,
        opacity: 0.18,
      },
    },
  ],
})

// Status-color palette for KPI tiles: subdued bg + vibrant border
const TONE_CARD = {
  default: 'bg-card border-border',
  sky: 'bg-sky-500/10 border-sky-500/20',
  emerald: 'bg-emerald-500/10 border-emerald-500/20',
  amber: 'bg-amber-500/10 border-amber-500/20',
  rose: 'bg-rose-500/10 border-rose-500/20',
}

const TONE_ICON = {
  default: 'text-muted-foreground',
  sky: 'text-sky-500',
  emerald: 'text-emerald-500',
  amber: 'text-amber-500',
  rose: 'text-rose-500',
}

const TONE_SPARKLINE = {
  default: CHART_SERIES.blue,
  sky: CHART_SERIES.sky,
  emerald: CHART_SERIES.emerald,
  amber: CHART_SERIES.amber,
  rose: CHART_SERIES.rose,
}

function KpiTile({ title, value, tone = 'default', points = [], suffix = '', icon: Icon }) {
  return (
    <Card className={cn('h-full shadow-sm border', TONE_CARD[tone])}>
      <CardHeader className="p-4 pb-2">
        <CardTitle className={cn('flex items-center justify-between text-xs font-semibold uppercase tracking-wider', TONE_ICON[tone])}>
          <span>{title}</span>
          <Icon className="h-4 w-4" />
        </CardTitle>
      </CardHeader>
      <CardContent className="p-4 pt-0 space-y-2">
        <p className="text-4xl font-black leading-none tracking-tight text-foreground">
          {value}
          {suffix ? <span className="ml-1 text-lg font-semibold text-muted-foreground">{suffix}</span> : null}
        </p>
        <div className="h-10 w-full rounded bg-muted/40 px-1 py-1">
          <ReactECharts option={sparklineOption(points, TONE_SPARKLINE[tone])} style={{ height: 28 }} />
        </div>
      </CardContent>
    </Card>
  )
}

const formatPercent = (value) => `${Number(value || 0).toFixed(2)}`
const formatTime = (iso) => {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleTimeString([], { hour12: false })
}

const formatEgress = (egressGbps) => {
  const normalized = Number(egressGbps || 0)
  if (!Number.isFinite(normalized) || normalized <= 0) {
    return { value: '0.0', suffix: 'Mbps' }
  }

  if (normalized >= 1) {
    return {
      value: normalized.toFixed(3),
      suffix: 'Gbps',
    }
  }

  return {
    value: (normalized * 1000).toFixed(1),
    suffix: 'Mbps',
  }
}

// Status-gradient palette for engine saturation tiles (fleet matrix).
// White text is intentional: these tiles have saturated colored backgrounds.
const engineToneClass = (score) => {
  if (score >= 88) return 'from-rose-600/70 to-rose-800/70 border-rose-500/70'
  if (score >= 70) return 'from-amber-500/60 to-amber-700/60 border-amber-400/70'
  if (score >= 45) return 'from-sky-500/60 to-sky-700/60 border-sky-400/70'
  return 'from-emerald-500/60 to-emerald-700/60 border-emerald-400/70'
}

export function StreamingCentralPage({
  engines,
  streams,
  vpnStatus,
  orchestratorStatus,
  orchUrl,
  apiKey,
}) {
  const { resolvedTheme } = useTheme()
  const {
    kpiHistory,
    vpnLeaseSummary,
    dashboardSnapshot,
    engineStatsById,
    engineInspectById,
    engineStartEvents,
    bufferBuckets,
    selectedEngineId,
    logsByContainerId,
    logsLoadingByContainerId,
    logsErrorByContainerId,
    ingestLiveSnapshot,
    setDashboardSnapshot,
    setEngineStartEvents,
    setVpnLeaseSummary,
    refreshBackendTelemetry,
    openEngineLogs,
    closeEngineLogs,
    setContainerLogsSnapshot,
    setContainerLogsLoading,
    setContainerLogsError,
    fetchContainerLogs,
  } = useStreamingCentralStore((state) => state)

  useEffect(() => {
    ingestLiveSnapshot({
      engines,
      streams,
      vpnStatus,
      orchestratorStatus,
    })
  }, [engines, streams, vpnStatus, orchestratorStatus, ingestLiveSnapshot])

  useEffect(() => {
    refreshBackendTelemetry({ orchUrl, apiKey })
    const interval = window.setInterval(() => {
      refreshBackendTelemetry({ orchUrl, apiKey })
    }, 30000)

    return () => {
      window.clearInterval(interval)
    }
  }, [orchUrl, apiKey, refreshBackendTelemetry])

  useEffect(() => {
    let eventSource = null
    let reconnectTimer = null
    let fallbackInterval = null
    let closed = false

    const fetchMetricsFallback = async () => {
      try {
        const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : {}
        const response = await fetch(`${orchUrl}/api/v1/metrics/dashboard?window_seconds=900&max_points=240`, {
          headers,
        })
        if (!response.ok) return
        const payload = await response.json()
        setDashboardSnapshot(payload)
      } catch {
        // Best-effort telemetry update.
      }
    }

    const connect = () => {
      if (closed) return

      if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
        fetchMetricsFallback()
        fallbackInterval = window.setInterval(fetchMetricsFallback, 10000)
        return
      }

      const streamUrl = new URL(`${orchUrl}/api/v1/metrics/stream`)
      streamUrl.searchParams.set('window_seconds', '900')
      streamUrl.searchParams.set('max_points', '240')
      if (apiKey) {
        streamUrl.searchParams.set('api_key', apiKey)
      }

      eventSource = new EventSource(streamUrl.toString())

      const handleMetricsSnapshot = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          setDashboardSnapshot(parsed?.payload || null)
        } catch {
          // Ignore malformed frames and keep existing snapshot.
        }
      }

      eventSource.addEventListener('metrics_snapshot', handleMetricsSnapshot)
      eventSource.onmessage = handleMetricsSnapshot

      eventSource.onerror = () => {
        if (eventSource) {
          eventSource.close()
          eventSource = null
        }
        if (!closed) {
          reconnectTimer = window.setTimeout(connect, 2000)
        }
      }
    }

    connect()

    return () => {
      closed = true
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer)
      }
      if (fallbackInterval) {
        window.clearInterval(fallbackInterval)
      }
      if (eventSource) {
        eventSource.close()
      }
    }
  }, [orchUrl, apiKey, setDashboardSnapshot])

  useEffect(() => {
    let eventSource = null
    let reconnectTimer = null
    let fallbackInterval = null
    let closed = false

    const applyEngineEvents = (events) => {
      const filtered = (Array.isArray(events) ? events : []).filter((event) => {
        const category = String(event?.category || '').toLowerCase()
        return category === 'created'
      })
      setEngineStartEvents(filtered)
    }

    const fetchEventsFallback = async () => {
      try {
        const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : {}
        const response = await fetch(`${orchUrl}/api/v1/events?event_type=engine&category=created&limit=100`, {
          headers,
        })
        if (!response.ok) return
        const payload = await response.json()
        applyEngineEvents(payload)
      } catch {
        // Best-effort telemetry update.
      }
    }

    const connect = () => {
      if (closed) return

      if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
        fetchEventsFallback()
        fallbackInterval = window.setInterval(fetchEventsFallback, 10000)
        return
      }

      const streamUrl = new URL(`${orchUrl}/api/v1/events/live`)
      streamUrl.searchParams.set('event_type', 'engine')
      streamUrl.searchParams.set('limit', '100')
      if (apiKey) {
        streamUrl.searchParams.set('api_key', apiKey)
      }

      eventSource = new EventSource(streamUrl.toString())

      const handleEventsSnapshot = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          const events = parsed?.payload?.events || []
          applyEngineEvents(events)
        } catch {
          // Ignore malformed frames and keep existing events.
        }
      }

      eventSource.addEventListener('events_snapshot', handleEventsSnapshot)
      eventSource.onmessage = handleEventsSnapshot

      eventSource.onerror = () => {
        if (eventSource) {
          eventSource.close()
          eventSource = null
        }
        if (!closed) {
          reconnectTimer = window.setTimeout(connect, 2000)
        }
      }
    }

    connect()

    return () => {
      closed = true
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer)
      }
      if (fallbackInterval) {
        window.clearInterval(fallbackInterval)
      }
      if (eventSource) {
        eventSource.close()
      }
    }
  }, [orchUrl, apiKey, setEngineStartEvents])

  useEffect(() => {
    let eventSource = null
    let reconnectTimer = null
    let fallbackInterval = null
    let closed = false

    const fetchVpnLeases = async () => {
      try {
        const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : {}
        const response = await fetch(`${orchUrl}/api/v1/vpn/leases`, { headers })
        if (!response.ok) return
        const payload = await response.json()
        setVpnLeaseSummary(payload)
      } catch {
        // Best-effort telemetry update.
      }
    }

    const connect = () => {
      if (closed) {
        return
      }

      if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
        fetchVpnLeases()
        fallbackInterval = window.setInterval(fetchVpnLeases, 10000)
        return
      }

      const streamUrl = new URL(`${orchUrl}/api/v1/vpn/leases/stream`)
      if (apiKey) {
        streamUrl.searchParams.set('api_key', apiKey)
      }

      eventSource = new EventSource(streamUrl.toString())

      const handleLeaseSnapshot = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          setVpnLeaseSummary(parsed?.payload || null)
        } catch {
          // Ignore malformed frame and keep latest summary.
        }
      }

      eventSource.addEventListener('vpn_leases_snapshot', handleLeaseSnapshot)
      eventSource.onmessage = handleLeaseSnapshot

      eventSource.onerror = () => {
        if (eventSource) {
          eventSource.close()
          eventSource = null
        }

        if (!closed) {
          reconnectTimer = window.setTimeout(connect, 2000)
        }
      }
    }

    connect()

    return () => {
      closed = true
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer)
      }
      if (fallbackInterval) {
        window.clearInterval(fallbackInterval)
      }
      if (eventSource) {
        eventSource.close()
      }
    }
  }, [orchUrl, apiKey])

  useEffect(() => {
    if (!selectedEngineId) return undefined

    let eventSource = null
    let reconnectTimer = null
    let fallbackInterval = null
    let closed = false

    const fetchLogsFallback = () => {
      fetchContainerLogs({ orchUrl, apiKey, containerId: selectedEngineId })
    }

    const connect = () => {
      if (closed) {
        return
      }

      setContainerLogsLoading({ containerId: selectedEngineId, loading: true })

      if (typeof window === 'undefined' || typeof window.EventSource === 'undefined') {
        fetchLogsFallback()
        fallbackInterval = window.setInterval(fetchLogsFallback, 2500)
        return
      }

      const streamUrl = new URL(`${orchUrl}/api/v1/containers/${encodeURIComponent(selectedEngineId)}/logs/stream`)
      streamUrl.searchParams.set('tail', '300')
      streamUrl.searchParams.set('since_seconds', '1200')
      streamUrl.searchParams.set('interval_seconds', '2.5')
      if (apiKey) {
        streamUrl.searchParams.set('api_key', apiKey)
      }

      eventSource = new EventSource(streamUrl.toString())

      const handleLogsSnapshot = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          setContainerLogsSnapshot({
            containerId: selectedEngineId,
            payload: parsed?.payload || {},
          })
        } catch {
          setContainerLogsError({
            containerId: selectedEngineId,
            error: 'Failed to parse logs stream payload',
          })
        }
      }

      const handleLogsErrorEvent = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          setContainerLogsError({
            containerId: selectedEngineId,
            error: parsed?.payload?.detail || 'logs_stream_error',
          })
        } catch {
          setContainerLogsError({
            containerId: selectedEngineId,
            error: 'logs_stream_error',
          })
        }
      }

      eventSource.addEventListener('container_logs_snapshot', handleLogsSnapshot)
      eventSource.addEventListener('container_logs_error', handleLogsErrorEvent)

      eventSource.onmessage = (event) => {
        try {
          const parsed = JSON.parse(event.data)
          const eventType = String(parsed?.type || '')
          if (eventType === 'container_logs_snapshot') {
            setContainerLogsSnapshot({
              containerId: selectedEngineId,
              payload: parsed?.payload || {},
            })
          } else if (eventType === 'container_logs_error') {
            setContainerLogsError({
              containerId: selectedEngineId,
              error: parsed?.payload?.detail || 'logs_stream_error',
            })
          }
        } catch {
          setContainerLogsError({
            containerId: selectedEngineId,
            error: 'Failed to parse logs stream payload',
          })
        }
      }

      eventSource.onerror = () => {
        setContainerLogsLoading({ containerId: selectedEngineId, loading: false })

        if (eventSource) {
          eventSource.close()
          eventSource = null
        }

        if (!closed) {
          reconnectTimer = window.setTimeout(connect, 2000)
        }
      }
    }

    connect()

    return () => {
      closed = true
      if (reconnectTimer) {
        window.clearTimeout(reconnectTimer)
      }
      if (fallbackInterval) {
        window.clearInterval(fallbackInterval)
      }
      if (eventSource) {
        eventSource.close()
      }
    }
  }, [
    selectedEngineId,
    orchUrl,
    apiKey,
    fetchContainerLogs,
    setContainerLogsSnapshot,
    setContainerLogsLoading,
    setContainerLogsError,
  ])

  const vpnIncident =
    (vpnStatus?.mode === 'redundant' && (!vpnStatus?.vpn1?.connected || !vpnStatus?.vpn2?.connected)) ||
    (vpnStatus?.mode === 'single' && !vpnStatus?.connected)

  const breakerIncident =
    orchestratorStatus?.provisioning?.circuit_breaker_state &&
    orchestratorStatus.provisioning.circuit_breaker_state !== 'closed'

  const activeEmergency = Boolean(vpnIncident || breakerIncident)

  const sortedEngineEvents = useMemo(() => {
    return [...(engineStartEvents || [])]
      .sort((a, b) => new Date(a.timestamp).getTime() - new Date(b.timestamp).getTime())
      .slice(-16)
  }, [engineStartEvents])

  const latencyOverlayOption = useMemo(() => {
    const ct = getChartTheme(resolvedTheme === 'dark')
    const timestamps = dashboardSnapshot?.history?.timestamps || []
    const labels = timestamps.map((ts) => formatTime(ts))
    const ttfb = dashboardSnapshot?.history?.ttfbP95Ms || []
    const activeStreamsSeries = dashboardSnapshot?.history?.activeStreams || []

    const markLineData = sortedEngineEvents
      .map((event) => ({
        xAxis: formatTime(event.timestamp),
        name: event.message?.slice(0, 18) || 'Engine start',
      }))
      .filter((entry) => labels.includes(entry.xAxis))

    return {
      animation: false,
      grid: { left: 42, right: 56, top: 25, bottom: 65 },
      tooltip: { trigger: 'axis', backgroundColor: ct.tooltipBg, borderColor: ct.tooltipBorder, textStyle: { color: ct.tooltipText } },
      legend: {
        bottom: 0,
        left: 'center',
        data: ['TTFB p95 (ms)', 'Active Streams'],
        textStyle: { color: ct.legendText },
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: labels,
        axisLabel: { color: ct.axisLabel },
      },
      yAxis: [
        {
          type: 'value',
          name: 'ms',
          axisLabel: { color: ct.axisLabel },
          splitLine: { lineStyle: { color: ct.splitLine } },
        },
        {
          type: 'value',
          name: 'streams',
          axisLabel: { color: ct.axisLabel },
          splitLine: { show: false },
        },
      ],
      series: [
        {
          name: 'TTFB p95 (ms)',
          type: 'line',
          smooth: 0.2,
          yAxisIndex: 0,
          symbol: 'none',
          data: ttfb,
          lineStyle: { color: CHART_SERIES.rose, width: 2 },
          areaStyle: { color: CHART_SERIES.rose, opacity: 0.12 },
          markLine: {
            symbol: ['none', 'none'],
            data: markLineData,
            lineStyle: { color: CHART_SERIES.amber, width: 1.3, type: 'dashed' },
            label: {
              color: CHART_SERIES.amber,
              formatter: 'Engine Start',
              position: 'end',
              verticalAlign: 'top',
              padding: [0, 0, 0, 0],
            },
          },
        },
        {
          name: 'Active Streams',
          type: 'line',
          smooth: 0.2,
          yAxisIndex: 1,
          symbol: 'none',
          data: activeStreamsSeries,
          lineStyle: { color: CHART_SERIES.blue, width: 2 },
          areaStyle: { color: CHART_SERIES.blue, opacity: 0.12 },
        },
      ],
    }
  }, [dashboardSnapshot, sortedEngineEvents, resolvedTheme])

  const heatmapOption = useMemo(() => {
    const ct = getChartTheme(resolvedTheme === 'dark')
    const xBuckets = bufferBuckets || []
    const xLabels = xBuckets.map((bucket) => formatTime(bucket.ts))

    const streamRows = (streams || []).slice(0, 24)
    const streamLabels = streamRows.map((stream) => stream.container_name || stream.id.slice(0, 10))

    const data = []
    xBuckets.forEach((bucket, xIndex) => {
      streamRows.forEach((stream, yIndex) => {
        const value = Number(bucket.values?.[stream.id] || 0)
        data.push([xIndex, yIndex, value])
      })
    })

    return {
      animation: false,
      grid: { left: 90, right: 20, top: 16, bottom: 65 },
      tooltip: { position: 'top', backgroundColor: ct.tooltipBg, borderColor: ct.tooltipBorder, textStyle: { color: ct.tooltipText } },
      xAxis: {
        type: 'category',
        data: xLabels,
        axisLabel: { color: ct.axisLabel, showMaxLabel: true, hideOverlap: true },
        splitArea: { show: false },
      },
      yAxis: {
        type: 'category',
        data: streamLabels,
        axisLabel: { color: ct.axisLabel },
        splitArea: { show: false },
      },
      visualMap: {
        min: 0,
        max: 40,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        text: ['Healthy buffer', 'Stutter risk'],
        textStyle: { color: ct.legendText },
        inRange: {
          color: [CHART_SERIES.rose, CHART_SERIES.amber, '#facc15', CHART_SERIES.emerald],
        },
      },
      series: [
        {
          name: 'Proxy Buffer Pieces',
          type: 'heatmap',
          data,
          emphasis: {
            itemStyle: {
              shadowBlur: 10,
              shadowColor: 'rgba(0, 0, 0, 0.6)',
            },
          },
        },
      ],
    }
  }, [bufferBuckets, streams, resolvedTheme])

  const activeStreamsValue = Number(orchestratorStatus?.streams?.active ?? streams?.length ?? 0)
  const healthyEnginesValue = Number(
    orchestratorStatus?.engines?.healthy ?? (engines || []).filter((engine) => engine.health_status === 'healthy').length,
  )
  const unhealthyEnginesValue = Number(
    orchestratorStatus?.engines?.unhealthy ?? (engines || []).filter((engine) => engine.health_status === 'unhealthy').length,
  )
  const runningEnginesValue = Number(orchestratorStatus?.engines?.running ?? (engines || []).length)
  const breakerState = orchestratorStatus?.provisioning?.circuit_breaker_state || 'unknown'
  const capacityUsed = Number(orchestratorStatus?.capacity?.used ?? 0)
  const capacityTotal = Number(orchestratorStatus?.capacity?.total ?? 0)
  const capacityMax = Number(orchestratorStatus?.capacity?.max_replicas ?? capacityTotal)

  const credentialSummary = useMemo(() => {
    const statusSummary = vpnStatus?.lease_summary || vpnStatus?.credentials_summary || null
    const merged = vpnLeaseSummary || statusSummary || {}
    const leased = Number(merged?.leased ?? merged?.active_leases ?? 0)
    const total = Number(
      merged?.max_vpn_capacity
      ?? merged?.total
      ?? merged?.pool_size
      ?? merged?.credentials_total
      ?? ((Number(merged?.available ?? 0) + leased) || 0),
    )
    return {
      leased,
      total: Math.max(total, leased),
    }
  }, [vpnLeaseSummary, vpnStatus])

  const provisioningBlocked = orchestratorStatus?.provisioning?.can_provision === false
  const provisioningBlockedReason = orchestratorStatus?.provisioning?.blocked_reason
  const provisioningRecoveryEta =
    orchestratorStatus?.provisioning?.blocked_reason_details?.recovery_eta_seconds ?? null

  const vpnCredentialPoints = useMemo(() => {
    const pointCount = Math.max(4, kpiHistory.timestamps?.length || 0)
    return Array.from({ length: pointCount }).map(() => credentialSummary.leased)
  }, [credentialSummary.leased, kpiHistory.timestamps])

  const vpnEssentials = useMemo(() => {
    if (!vpnStatus || vpnStatus.mode === 'disabled') {
      return []
    }

    if (vpnStatus.mode === 'single') {
      const tunnel = vpnStatus.vpn1 || vpnStatus
      return [{
        label: 'VPN',
        connected: Boolean(tunnel?.connected),
        container: tunnel?.container_name || tunnel?.container || 'N/A',
        publicIp: tunnel?.public_ip || 'N/A',
        provider: tunnel?.provider || null,
      }]
    }

    return [vpnStatus.vpn1, vpnStatus.vpn2]
      .filter(Boolean)
      .map((tunnel, index) => ({
        label: `VPN ${index + 1}`,
        connected: Boolean(tunnel.connected),
        container: tunnel.container_name || tunnel.container || 'N/A',
        publicIp: tunnel.public_ip || 'N/A',
        provider: tunnel.provider || null,
      }))
  }, [vpnStatus])

  const successRate = Number(
    dashboardSnapshot?.proxy?.request_window_1m?.success_rate_percent ??
    (orchestratorStatus?.status === 'healthy' ? 99.5 : 95),
  )

  const egressGbps = Number(dashboardSnapshot?.proxy?.throughput?.egress_mbps || 0) / 1000
  const egressDisplay = formatEgress(egressGbps)

  const selectedEngine = (engines || []).find((engine) => engine.container_id === selectedEngineId)
  const selectedEngineLogs = selectedEngineId ? logsByContainerId[selectedEngineId] : null
  const logsLoading = selectedEngineId ? Boolean(logsLoadingByContainerId[selectedEngineId]) : false
  const logsError = selectedEngineId ? logsErrorByContainerId[selectedEngineId] : null

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-bold tracking-tight text-foreground">Streaming Central</h1>
          <p className="text-sm text-muted-foreground">
            High-frequency NOC surface for routing stability, QoE telemetry, and container saturation.
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <Badge variant={activeEmergency ? 'destructive' : 'success'}>
            {activeEmergency ? 'Incident mode' : 'Nominal'}
          </Badge>
          <Badge variant="outline">1s cadence</Badge>
          <Badge variant="outline">15m trend window</Badge>
        </div>
      </div>

      {activeEmergency && (
        <Alert variant="destructive">
          <ShieldAlert className="h-4 w-4" />
          <AlertTitle>Active Emergencies</AlertTitle>
          <AlertDescription>
            {vpnIncident ? 'VPN tunnel degradation detected. ' : ''}
            {breakerIncident ? 'Provisioning circuit breaker is not closed. ' : ''}
            Operators should watch failover edges and TTFB spikes.
          </AlertDescription>
        </Alert>
      )}

      {provisioningBlocked && provisioningBlockedReason && (
        <Alert variant="warning" className="border-amber-500/60 bg-amber-500/10">
          <AlertTriangle className="h-4 w-4 text-amber-300" />
          <AlertTitle>Provisioning Blocked</AlertTitle>
          <AlertDescription>
            {provisioningBlockedReason}
            {typeof provisioningRecoveryEta === 'number' ? ` Recovery ETA: ${provisioningRecoveryEta}s.` : ''}
          </AlertDescription>
        </Alert>
      )}

      <Tabs defaultValue="pulse" className="space-y-3">
        <TabsList className="grid w-full grid-cols-4 border-slate-700 bg-slate-900/90 text-slate-300">
          <TabsTrigger value="pulse" className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50">Global Pulse</TabsTrigger>
          <TabsTrigger value="topology" className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50">Routing Topology</TabsTrigger>
          <TabsTrigger value="microscope" className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50">Stream Microscope</TabsTrigger>
          <TabsTrigger value="fleet" className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50">Fleet Matrix</TabsTrigger>
        </TabsList>

        <TabsContent value="pulse" className="space-y-3">
          <div className="grid grid-cols-12 gap-4">
            <div className="col-span-12 sm:col-span-6 xl:col-span-2">
              <KpiTile
                title="Active Streams"
                value={activeStreamsValue}
                points={kpiHistory.activeStreams}
                tone="sky"
                icon={Tv}
              />
            </div>
            <div className="col-span-12 sm:col-span-6 xl:col-span-2">
              <KpiTile
                title="Global Egress"
                value={egressDisplay.value}
                suffix={egressDisplay.suffix}
                points={kpiHistory.egressGbps}
                tone="emerald"
                icon={Network}
              />
            </div>
            <div className="col-span-12 sm:col-span-6 xl:col-span-3">
              <KpiTile
                title="Engine Capacity"
                value={`${runningEnginesValue} / ${capacityUsed}`}
                suffix={capacityMax > 0 ? `(max ${capacityMax})` : ''}
                points={kpiHistory.healthyEngines}
                tone={runningEnginesValue >= capacityMax && capacityMax > 0 ? 'rose' : 'amber'}
                icon={Server}
              />
            </div>
            <div className="col-span-12 sm:col-span-6 xl:col-span-2">
              <KpiTile
                title="VPN Credentials"
                value={`${credentialSummary.leased} / ${credentialSummary.total}`}
                points={vpnCredentialPoints}
                tone={credentialSummary.leased > 0 ? 'amber' : 'default'}
                icon={KeyRound}
              />
            </div>
            <div className="col-span-12 sm:col-span-6 xl:col-span-3">
              <KpiTile
                title="Success Rate"
                value={formatPercent(successRate)}
                suffix="%"
                points={kpiHistory.successRate}
                tone={successRate < 97 ? 'rose' : 'default'}
                icon={GaugeCircle}
              />
            </div>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <Card className="shadow-sm">
              <CardHeader className="p-4 pb-2">
                <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  Health Essentials
                </CardTitle>
              </CardHeader>
              <CardContent className="grid gap-2 p-4 pt-0 text-sm md:grid-cols-3">
                <div className="rounded-md border border-border bg-muted/40 p-3">
                  <p className="text-xs uppercase tracking-wider text-muted-foreground">Engine health</p>
                  <p className="mt-0.5 font-semibold text-foreground">
                    {healthyEnginesValue} healthy / {unhealthyEnginesValue} unhealthy
                  </p>
                </div>
                <div className="rounded-md border border-border bg-muted/40 p-3">
                  <p className="text-xs uppercase tracking-wider text-muted-foreground">Capacity</p>
                  <p className="mt-0.5 font-semibold text-foreground">{runningEnginesValue} / {capacityUsed} (max {capacityMax || capacityTotal})</p>
                </div>
                <div className="rounded-md border border-border bg-muted/40 p-3">
                  <p className="text-xs uppercase tracking-wider text-muted-foreground">Circuit breaker</p>
                  <div className="mt-0.5">
                    <Badge variant={breakerState === 'closed' ? 'success' : 'warning'}>{breakerState}</Badge>
                  </div>
                </div>
              </CardContent>
            </Card>

            <Card className="shadow-sm">
              <CardHeader className="p-4 pb-2">
                <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                  VPN Essentials
                </CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 p-4 pt-0 text-sm">
                <div className="rounded-md border border-border bg-muted/40 p-3">
                  <p className="text-xs uppercase tracking-wider text-muted-foreground">Mode</p>
                  <p className="mt-0.5 font-semibold text-foreground">{vpnStatus?.mode || 'disabled'}</p>
                </div>
                {vpnEssentials.length ? (
                  vpnEssentials.map((vpn) => (
                    <div key={vpn.label} className="rounded-md border border-border bg-muted/40 p-3">
                      <div className="flex items-center justify-between gap-2">
                        <p className="font-semibold text-foreground">{vpn.label}</p>
                        <Badge variant={vpn.connected ? 'success' : 'destructive'}>
                          {vpn.connected ? 'Connected' : 'Disconnected'}
                        </Badge>
                      </div>
                      <p className="mt-1 text-xs text-muted-foreground">{vpn.container}</p>
                      <p className="mt-0.5 font-mono text-xs text-foreground">{vpn.publicIp}</p>
                      {vpn.provider ? <p className="mt-0.5 text-xs text-muted-foreground">{vpn.provider}</p> : null}
                    </div>
                  ))
                ) : (
                  <div className="rounded-md border border-border bg-muted/40 p-3">
                    <p className="text-sm text-muted-foreground">VPN is disabled.</p>
                  </div>
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="topology" className="space-y-3">
          <RoutingTopologyPage
            engines={engines}
            streams={streams}
            vpnStatus={vpnStatus}
            orchestratorStatus={orchestratorStatus}
            embedded
          />
        </TabsContent>

        <TabsContent value="microscope" className="space-y-3">
          <Card className="shadow-sm">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Proxy Buffer Heatmap (rolling 5m)
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <ReactECharts option={heatmapOption} style={{ height: 430 }} />
            </CardContent>
          </Card>

          <Card className="shadow-sm">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Latency overlay + engine starts
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <ReactECharts option={latencyOverlayOption} style={{ height: 340 }} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="fleet" className="space-y-3">
          <Card className="shadow-sm">
            <CardHeader className="p-4 pb-2">
              <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Container saturation map
              </CardTitle>
            </CardHeader>
            <CardContent className="p-4 pt-0">
              <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
                {(engines || []).map((engine) => {
                  const stats = engineStatsById?.[engine.container_id] || {}
                  const inspect = engineInspectById?.[engine.container_id] || {}
                  const cpu = Number(stats.cpu_percent || 0)
                  const memoryPercent = Number(stats.memory_percent || 0)
                  const utilization = Math.max(cpu, memoryPercent)

                  return (
                    <HoverCard key={engine.container_id} openDelay={100} closeDelay={120}>
                      <HoverCardTrigger asChild>
                        <button
                          type="button"
                          onClick={() => openEngineLogs(engine.container_id)}
                          className={cn(
                            'group h-24 rounded-lg border bg-gradient-to-br p-2 text-left text-white shadow-sm transition hover:scale-[1.01] hover:shadow-md',
                            engineToneClass(utilization),
                          )}
                        >
                          <p className="truncate text-[11px] font-semibold uppercase tracking-wider">
                            {engine.container_name || engine.container_id.slice(0, 12)}
                          </p>
                          <div className="mt-2 flex items-end justify-between text-xs">
                            <span>CPU {cpu.toFixed(1)}%</span>
                            <span>RAM {memoryPercent.toFixed(1)}%</span>
                          </div>
                          <div className="mt-1 h-1.5 rounded bg-black/25">
                            <div
                              className="h-full rounded bg-white/85"
                              style={{ width: `${Math.min(100, utilization)}%` }}
                            />
                          </div>
                        </button>
                      </HoverCardTrigger>
                      <HoverCardContent className="w-80">
                        <div className="space-y-1 text-xs">
                          <p className="text-sm font-semibold text-foreground">{engine.container_name || engine.container_id}</p>
                          <p className="text-muted-foreground">Host Port: {engine.port}</p>
                          <p className="text-muted-foreground">VPN Tunnel: {engine.vpn_container || 'unassigned'}</p>
                          <p className="text-muted-foreground">Uptime anchor: {formatTime(engine.first_seen || inspect.created)}</p>
                          <p className="text-muted-foreground">Restart count: {inspect.restart_count ?? engine.restart_count ?? 0}</p>
                          <p className="text-muted-foreground">Streams: {engine.stream_count ?? 0}</p>
                        </div>
                      </HoverCardContent>
                    </HoverCard>
                  )
                })}
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Sheet open={Boolean(selectedEngineId)} onOpenChange={(open) => (!open ? closeEngineLogs() : undefined)}>
        <SheetContent side="right" className="w-[92vw] max-w-[720px]">
          <SheetHeader>
            <SheetTitle>{selectedEngine?.container_name || selectedEngineId || 'Engine logs'}</SheetTitle>
            <SheetDescription>
              Live trailing Docker logs. Refreshes every 2.5s while the panel is open.
            </SheetDescription>
          </SheetHeader>

          <div className="mt-4 flex items-center justify-between gap-2">
            <div className="text-xs text-muted-foreground">
              Last fetch: {selectedEngineLogs?.fetchedAt ? formatTime(selectedEngineLogs.fetchedAt) : '-'}
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                if (!selectedEngineId) return
                fetchContainerLogs({ orchUrl, apiKey, containerId: selectedEngineId })
              }}
            >
              Refresh
            </Button>
          </div>

          {logsError ? (
            <Alert variant="destructive" className="mt-3">
              <AlertTriangle className="h-4 w-4" />
              <AlertTitle>Log retrieval failed</AlertTitle>
              <AlertDescription>{logsError}</AlertDescription>
            </Alert>
          ) : null}

          <div className="mt-3 rounded-md border border-border bg-muted/20">
            <ScrollArea className="h-[70vh]">
              <pre className="px-4 py-3 font-mono text-[11px] leading-relaxed text-foreground">
                {logsLoading ? 'Loading logs...' : (selectedEngineLogs?.lines || []).join('\n') || 'No logs available.'}
              </pre>
            </ScrollArea>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  )
}
