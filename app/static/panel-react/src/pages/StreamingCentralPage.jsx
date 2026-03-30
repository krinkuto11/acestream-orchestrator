import React, { useEffect, useMemo } from 'react'
import ReactECharts from 'echarts-for-react'
import { AlertTriangle, GaugeCircle, Network, Server, ShieldAlert, Tv, Waves, Workflow } from 'lucide-react'
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

function KpiTile({ title, value, tone = 'default', points = [], suffix = '', icon: Icon }) {
  const toneClass = {
    default: 'border-slate-700/70 bg-slate-950/80 text-slate-50',
    cyan: 'border-cyan-500/40 bg-cyan-950/30 text-cyan-100',
    emerald: 'border-emerald-500/40 bg-emerald-950/30 text-emerald-100',
    amber: 'border-amber-500/40 bg-amber-950/30 text-amber-100',
    rose: 'border-rose-500/40 bg-rose-950/30 text-rose-100',
  }

  const sparklineColor = {
    default: '#94a3b8',
    cyan: '#22d3ee',
    emerald: '#34d399',
    amber: '#f59e0b',
    rose: '#fb7185',
  }

  return (
    <Card className={cn('rounded-xl border shadow-sm', toneClass[tone])}>
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center justify-between text-[11px] font-semibold uppercase tracking-[0.14em]">
          <span>{title}</span>
          <Icon className="h-4 w-4 opacity-90" />
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-2 pt-0">
        <p className="text-4xl font-black leading-none tracking-tight">
          {value}
          {suffix ? <span className="ml-1 text-lg font-semibold opacity-75">{suffix}</span> : null}
        </p>
        <div className="h-11 w-full rounded bg-black/25 px-1 py-1">
          <ReactECharts option={sparklineOption(points, sparklineColor[tone])} style={{ height: 32 }} />
        </div>
      </CardContent>
    </Card>
  )
}

const formatGbps = (value) => `${Number(value || 0).toFixed(3)}`
const formatPercent = (value) => `${Number(value || 0).toFixed(2)}`
const formatTime = (iso) => {
  if (!iso) return '-'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '-'
  return date.toLocaleTimeString([], { hour12: false })
}

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
  const {
    kpiHistory,
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
    refreshBackendTelemetry,
    openEngineLogs,
    closeEngineLogs,
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
    }, 4000)

    return () => {
      window.clearInterval(interval)
    }
  }, [orchUrl, apiKey, refreshBackendTelemetry])

  useEffect(() => {
    if (!selectedEngineId) return undefined

    fetchContainerLogs({ orchUrl, apiKey, containerId: selectedEngineId })
    const interval = window.setInterval(() => {
      fetchContainerLogs({ orchUrl, apiKey, containerId: selectedEngineId })
    }, 2500)

    return () => {
      window.clearInterval(interval)
    }
  }, [selectedEngineId, orchUrl, apiKey, fetchContainerLogs])

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
      grid: { left: 42, right: 56, top: 30, bottom: 30 },
      tooltip: { trigger: 'axis' },
      legend: {
        data: ['TTFB p95 (ms)', 'Active Streams'],
        textStyle: { color: '#cbd5e1' },
      },
      xAxis: {
        type: 'category',
        boundaryGap: false,
        data: labels,
        axisLabel: { color: '#94a3b8' },
      },
      yAxis: [
        {
          type: 'value',
          name: 'ms',
          axisLabel: { color: '#fca5a5' },
          splitLine: { lineStyle: { color: 'rgba(148,163,184,0.15)' } },
        },
        {
          type: 'value',
          name: 'streams',
          axisLabel: { color: '#93c5fd' },
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
          lineStyle: { color: '#f87171', width: 2 },
          areaStyle: { color: 'rgba(248,113,113,0.12)' },
          markLine: {
            symbol: ['none', 'none'],
            data: markLineData,
            lineStyle: { color: '#f59e0b', width: 1.3, type: 'dashed' },
            label: {
              color: '#fcd34d',
              formatter: 'Engine Start',
              position: 'insideEndTop',
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
          lineStyle: { color: '#60a5fa', width: 2 },
          areaStyle: { color: 'rgba(96,165,250,0.12)' },
        },
      ],
    }
  }, [dashboardSnapshot, sortedEngineEvents])

  const heatmapOption = useMemo(() => {
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
      grid: { left: 90, right: 20, top: 16, bottom: 46 },
      tooltip: {
        position: 'top',
      },
      xAxis: {
        type: 'category',
        data: xLabels,
        axisLabel: { color: '#94a3b8', showMaxLabel: true, hideOverlap: true },
        splitArea: { show: false },
      },
      yAxis: {
        type: 'category',
        data: streamLabels,
        axisLabel: { color: '#cbd5e1' },
        splitArea: { show: false },
      },
      visualMap: {
        min: 0,
        max: 40,
        orient: 'horizontal',
        left: 'center',
        bottom: 0,
        text: ['Healthy buffer', 'Stutter risk'],
        inRange: {
          color: ['#7f1d1d', '#ea580c', '#facc15', '#22c55e'],
        },
      },
      series: [
        {
          name: 'Buffer Avg Pieces',
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
  }, [bufferBuckets, streams])

  const activeStreamsValue = Number(orchestratorStatus?.streams?.active ?? streams?.length ?? 0)
  const healthyEnginesValue = Number(
    orchestratorStatus?.engines?.healthy ?? (engines || []).filter((engine) => engine.health_status === 'healthy').length,
  )

  const successRate = Number(
    dashboardSnapshot?.proxy?.request_window_1m?.success_rate_percent ??
      (orchestratorStatus?.status === 'healthy' ? 99.5 : 95),
  )

  const egressGbps = Number(dashboardSnapshot?.proxy?.throughput?.egress_mbps || 0) / 1000

  const selectedEngine = (engines || []).find((engine) => engine.container_id === selectedEngineId)
  const selectedEngineLogs = selectedEngineId ? logsByContainerId[selectedEngineId] : null
  const logsLoading = selectedEngineId ? Boolean(logsLoadingByContainerId[selectedEngineId]) : false
  const logsError = selectedEngineId ? logsErrorByContainerId[selectedEngineId] : null

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-3xl font-black tracking-tight">Streaming Central</h1>
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

      <Tabs defaultValue="pulse" className="space-y-3">
        <TabsList>
          <TabsTrigger value="pulse">Global Pulse</TabsTrigger>
          <TabsTrigger value="topology">Routing Topology</TabsTrigger>
          <TabsTrigger value="microscope">Stream Microscope</TabsTrigger>
          <TabsTrigger value="fleet">Fleet Matrix</TabsTrigger>
        </TabsList>

        <TabsContent value="pulse" className="space-y-3">
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <KpiTile
              title="Active Streams"
              value={activeStreamsValue}
              points={kpiHistory.activeStreams}
              tone="cyan"
              icon={Tv}
            />
            <KpiTile
              title="Global Egress"
              value={formatGbps(egressGbps)}
              suffix="Gbps"
              points={kpiHistory.egressGbps}
              tone="emerald"
              icon={Network}
            />
            <KpiTile
              title="Healthy Engines"
              value={healthyEnginesValue}
              points={kpiHistory.healthyEngines}
              tone="amber"
              icon={Server}
            />
            <KpiTile
              title="Success Rate"
              value={formatPercent(successRate)}
              suffix="%"
              points={kpiHistory.successRate}
              tone={successRate < 97 ? 'rose' : 'default'}
              icon={GaugeCircle}
            />
          </div>

          <Card className="border-slate-700/70 bg-slate-950/78">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-slate-100">Quick risk scan</CardTitle>
            </CardHeader>
            <CardContent className="grid gap-2 text-sm md:grid-cols-3">
              <div className="rounded-md border border-slate-700 bg-slate-900/60 p-3">
                <p className="text-[11px] uppercase tracking-[0.14em] text-slate-400">VPN mode</p>
                <p className="font-semibold text-slate-100">{vpnStatus?.mode || 'unknown'}</p>
              </div>
              <div className="rounded-md border border-slate-700 bg-slate-900/60 p-3">
                <p className="text-[11px] uppercase tracking-[0.14em] text-slate-400">Capacity used</p>
                <p className="font-semibold text-slate-100">
                  {orchestratorStatus?.capacity?.used ?? 0} / {orchestratorStatus?.capacity?.total ?? 0}
                </p>
              </div>
              <div className="rounded-md border border-slate-700 bg-slate-900/60 p-3">
                <p className="text-[11px] uppercase tracking-[0.14em] text-slate-400">Circuit breaker</p>
                <p className="font-semibold text-slate-100">
                  {orchestratorStatus?.provisioning?.circuit_breaker_state || 'unknown'}
                </p>
              </div>
            </CardContent>
          </Card>
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
          <Card className="border-slate-700/70 bg-slate-950/80">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-slate-100">Buffer Heatmap (rolling 5m)</CardTitle>
            </CardHeader>
            <CardContent>
              <ReactECharts option={heatmapOption} style={{ height: 430 }} />
            </CardContent>
          </Card>

          <Card className="border-slate-700/70 bg-slate-950/80">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-slate-100">Latency overlay + engine starts</CardTitle>
            </CardHeader>
            <CardContent>
              <ReactECharts option={latencyOverlayOption} style={{ height: 340 }} />
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="fleet" className="space-y-3">
          <Card className="border-slate-700/70 bg-slate-950/80">
            <CardHeader className="pb-2">
              <CardTitle className="text-sm text-slate-100">Container saturation map</CardTitle>
            </CardHeader>
            <CardContent>
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
                            'group h-24 rounded-lg border bg-gradient-to-br p-2 text-left text-white shadow transition hover:scale-[1.01] hover:shadow-lg',
                            engineToneClass(utilization),
                          )}
                        >
                          <p className="truncate text-[11px] font-semibold uppercase tracking-[0.1em]">
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
                      <HoverCardContent className="w-80 border-slate-700 bg-slate-950 text-slate-100">
                        <div className="space-y-1 text-xs">
                          <p className="text-sm font-semibold">{engine.container_name || engine.container_id}</p>
                          <p className="text-slate-400">Host Port: {engine.port}</p>
                          <p className="text-slate-400">VPN Tunnel: {engine.vpn_container || 'unassigned'}</p>
                          <p className="text-slate-400">Uptime anchor: {formatTime(engine.first_seen || inspect.created)}</p>
                          <p className="text-slate-400">Restart count: {inspect.restart_count ?? engine.restart_count ?? 0}</p>
                          <p className="text-slate-400">Streams: {engine.stream_count ?? 0}</p>
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
        <SheetContent side="right" className="w-[92vw] max-w-[720px] border-slate-700 bg-slate-950 text-slate-100">
          <SheetHeader>
            <SheetTitle>{selectedEngine?.container_name || selectedEngineId || 'Engine logs'}</SheetTitle>
            <SheetDescription>
              Live trailing Docker logs. Refreshes every 2.5s while the panel is open.
            </SheetDescription>
          </SheetHeader>

          <div className="mt-4 flex items-center justify-between gap-2">
            <div className="text-xs text-slate-400">
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

          <div className="mt-3 rounded-md border border-slate-700 bg-black/30">
            <ScrollArea className="h-[70vh]">
              <pre className="px-4 py-3 font-mono text-[11px] leading-relaxed text-slate-200">
                {logsLoading ? 'Loading logs...' : (selectedEngineLogs?.lines || []).join('\n') || 'No logs available.'}
              </pre>
            </ScrollArea>
          </div>
        </SheetContent>
      </Sheet>
    </div>
  )
}
