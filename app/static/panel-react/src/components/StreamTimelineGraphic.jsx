import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from 'recharts'
import { Badge } from '@/components/ui/badge'
import { ChartContainer, ChartTooltip, ChartTooltipContent } from '@/components/ui/chart'
import { cn } from '@/lib/utils'

const MAX_HISTORY_POINTS = 120
const HISTORY_KEY_PREFIX = 'acestream_history_'
const CLIENT_SAMPLE_HOLD_MS = 30000
const CLIENT_COLOR_PALETTE = [
  'hsl(var(--chart-1, 221 83% 53%))',
  'hsl(var(--chart-2, 160 84% 39%))',
  'hsl(var(--chart-3, 32 95% 44%))',
  'hsl(var(--chart-4, 262 83% 58%))',
  'hsl(var(--chart-5, 348 83% 47%))',
]

function toNumber(value) {
  const parsed = Number.parseFloat(String(value ?? ''))
  return Number.isFinite(parsed) ? parsed : null
}

function toEpochMs(value) {
  const parsed = toNumber(value)
  if (!Number.isFinite(parsed)) return null
  if (parsed > 1e12) return parsed
  if (parsed > 1e9) return parsed * 1000
  return null
}

function formatClock(timestampMs) {
  if (!Number.isFinite(timestampMs)) return 'N/A'
  try {
    return new Date(timestampMs).toLocaleTimeString()
  } catch {
    return 'N/A'
  }
}

function sanitizeClientKey(input) {
  return String(input || 'unknown')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '_')
    .replace(/^_+|_+$/g, '')
    .slice(0, 40)
}

function getClientLabel(client, fallbackIndex = 0) {
  return String(client?.ip_address || client?.client_id || `client-${fallbackIndex + 1}`).trim()
}

function getClientSeriesKey(client, index) {
  const raw = client?.client_id || client?.ip_address || `client_${index + 1}`
  return `client_${sanitizeClientKey(raw) || `client_${index + 1}`}`
}

function buildStorageKey(streamId) {
  return `${HISTORY_KEY_PREFIX}${String(streamId || 'unknown')}`
}

function readHistoryFromStorage(storageKey) {
  if (typeof window === 'undefined' || !window.localStorage) {
    return { points: [], labels: {} }
  }

  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) return { points: [], labels: {} }

    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed)) {
      return { points: parsed.slice(-MAX_HISTORY_POINTS), labels: {} }
    }

    if (parsed && Array.isArray(parsed.points)) {
      return {
        points: parsed.points.slice(-MAX_HISTORY_POINTS),
        labels: parsed.labels && typeof parsed.labels === 'object' ? parsed.labels : {},
      }
    }
  } catch {
    // Ignore malformed persisted history.
  }

  return { points: [], labels: {} }
}

function persistHistory(storageKey, points, labels) {
  if (typeof window === 'undefined' || !window.localStorage) {
    return
  }

  try {
    window.localStorage.setItem(storageKey, JSON.stringify({ points, labels }))
  } catch {
    // Best-effort persistence only.
  }
}

function StreamTimelineGraphic({
  streamId,
  livepos,
  clients = [],
  showStreamWindow = true,
  eventMarkers = [],
  isLive = false,
  compact = false,
  className,
}) {
  const storageKey = useMemo(() => buildStorageKey(streamId), [streamId])
  const [history, setHistory] = useState([])
  const [clientLabels, setClientLabels] = useState({})
  const [hydrated, setHydrated] = useState(false)
  const lastClientSamplesRef = useRef({})

  useEffect(() => {
    const loaded = readHistoryFromStorage(storageKey)
    const loadedPoints = Array.isArray(loaded.points) ? loaded.points : []
    const loadedLabels = loaded.labels || {}

    // Rehydrate recent client samples from persisted history so a quick page
    // reload does not produce a transient null-gap before live snapshots resume.
    const seededSamples = {}
    const seedTimestamp = Date.now()
    for (let i = loadedPoints.length - 1; i >= 0; i -= 1) {
      const point = loadedPoints[i] || {}
      Object.keys(point).forEach((key) => {
        if (!key.startsWith('client_') || seededSamples[key]) return
        const value = toNumber(point[key])
        if (!Number.isFinite(value)) return
        seededSamples[key] = {
          value: Math.max(0, value),
          label: String(loadedLabels[key] || key),
          updatedAt: seedTimestamp,
        }
      })
    }

    lastClientSamplesRef.current = seededSamples
    setHistory(loadedPoints)
    setClientLabels(loadedLabels)
    setHydrated(true)
  }, [storageKey])

  useEffect(() => {
    const liveEdgeTs = toNumber(livepos?.last_ts ?? livepos?.live_last)
    const posTs = toNumber(livepos?.pos)
    if (!Number.isFinite(liveEdgeTs) || !Number.isFinite(posTs)) {
      return
    }

    const engineLag = Math.max(0, liveEdgeTs - posTs)
    const timestamp = Date.now()
    const nextLabels = {}
    const clientValues = {}
    const seenClientKeys = new Set()
    let streamWindowMax = 0
    let streamWindowFallbackFromRunway = 0
    let hasExplicitStreamWindow = false

    ;(Array.isArray(clients) ? clients : []).forEach((client, index) => {
      const runway = toNumber(client?.buffer_seconds_behind)
      const streamWindow = toNumber(client?.stream_buffer_window_seconds)

      if (Number.isFinite(streamWindow)) {
        hasExplicitStreamWindow = true
        streamWindowMax = Math.max(streamWindowMax, Math.max(0, streamWindow))
      }

      if (!Number.isFinite(runway)) return
      const safeRunway = Math.max(0, runway)
      streamWindowFallbackFromRunway = Math.max(streamWindowFallbackFromRunway, safeRunway)
      const key = getClientSeriesKey(client, index)
      seenClientKeys.add(key)
      clientValues[key] = safeRunway
      const label = getClientLabel(client, index)
      nextLabels[key] = label
      lastClientSamplesRef.current[key] = {
        value: safeRunway,
        label,
        updatedAt: timestamp,
      }
    })

    // During short reconnect windows, tracker snapshots can momentarily miss
    // clients. Keep recent samples alive with gentle decay to avoid chart wipe.
    Object.entries(lastClientSamplesRef.current || {}).forEach(([key, sample]) => {
      if (seenClientKeys.has(key)) return

      const ageMs = Math.max(0, timestamp - toNumber(sample?.updatedAt) || 0)
      if (ageMs > CLIENT_SAMPLE_HOLD_MS) {
        delete lastClientSamplesRef.current[key]
        return
      }

      const lastValue = Math.max(0, toNumber(sample?.value) || 0)
      const ageSeconds = ageMs / 1000
      const decayedValue = Math.max(0, lastValue - ageSeconds)

      clientValues[key] = decayedValue
      nextLabels[key] = String(sample?.label || nextLabels[key] || key)
      streamWindowFallbackFromRunway = Math.max(streamWindowFallbackFromRunway, decayedValue)
    })

    const effectiveStreamWindow = showStreamWindow
      ? (
          hasExplicitStreamWindow
            ? streamWindowMax
            : streamWindowFallbackFromRunway
        )
      : null

    const thresholdObservedAt = timestamp

    const tick = {
      time: timestamp,
      liveEdge: 0,
      engineLag,
      streamWindow: Number.isFinite(toNumber(effectiveStreamWindow))
        ? Math.max(0, toNumber(effectiveStreamWindow))
        : null,
      dynamicThreshold: null,
      ...clientValues,
    }

    setHistory((prev) => {
      const next = [...prev, tick].slice(-MAX_HISTORY_POINTS)
      setClientLabels((prevLabels) => {
        const mergedLabels = { ...prevLabels, ...nextLabels }
        persistHistory(storageKey, next, mergedLabels)
        return mergedLabels
      })
      return next
    })
  }, [clients, livepos?.last_ts, livepos?.live_last, livepos?.pos, showStreamWindow, storageKey])

  const model = useMemo(() => {
    if (!history.length) return null

    const clientKeysSet = new Set()
    history.forEach((point) => {
      Object.keys(point).forEach((key) => {
        if (key.startsWith('client_')) {
          clientKeysSet.add(key)
        }
      })
    })

    Object.keys(clientLabels || {}).forEach((key) => {
      if (key.startsWith('client_')) {
        clientKeysSet.add(key)
      }
    })

    const clientKeys = Array.from(clientKeysSet)

    const normalizedHistory = history.map((point) => {
      const engineLag = toNumber(point.engineLag)
      const streamWindow = toNumber(point.streamWindow)
      const swarmLag = Number.isFinite(engineLag) ? Math.max(0, engineLag) : null

      const normalized = {
        time: point.time,
        liveEdge: 0,
        engineLag: swarmLag,
        engineLag__band: Number.isFinite(swarmLag) ? [0, swarmLag] : null,
        streamWindow: Number.isFinite(streamWindow) ? Math.max(0, streamWindow) : null,
      }

      clientKeys.forEach((key) => {
        const runway = toNumber(point[key])
        if (Number.isFinite(runway)) {
          const safeRunway = Math.max(0, runway)
          const viewerLag = swarmLag + safeRunway
          normalized[key] = viewerLag
          normalized[`${key}__runway`] = safeRunway
          normalized[`${key}__band`] = [swarmLag, viewerLag]
        } else {
          normalized[key] = null
          normalized[`${key}__runway`] = null
          normalized[`${key}__band`] = null
        }
      })
      return normalized
    })

    const rawMaxLag = normalizedHistory.reduce((max, point) => {
      let nextMax = max
      const engineLag = toNumber(point.engineLag)
      if (Number.isFinite(engineLag)) {
        nextMax = Math.max(nextMax, engineLag)
      }
      const streamWindow = toNumber(point.streamWindow)
      if (showStreamWindow && Number.isFinite(streamWindow)) {
        nextMax = Math.max(nextMax, streamWindow)
      }
      clientKeys.forEach((key) => {
        const lag = toNumber(point[key])
        if (Number.isFinite(lag)) {
          nextMax = Math.max(nextMax, lag)
        }
      })
      return nextMax
    }, 0)

    const yMax = Math.max(2, Math.ceil(rawMaxLag + 2))

    return {
      chartData: normalizedHistory,
      clientKeys,
      yMax,
    }
  }, [history, clientLabels, showStreamWindow])

  const chartConfig = useMemo(() => {
    const config = {
      engineLag: {
        label: 'Swarm Lead',
        color: 'var(--warning)',
      },
      liveEdge: {
        label: 'Live edge',
        color: 'var(--foreground)',
      },
    }

    if (showStreamWindow) {
      config.streamWindow = {
        label: 'HLS Proxy Window',
        color: 'var(--info)',
      }
    }

    if (model?.clientKeys?.length) {
      model.clientKeys.forEach((key, index) => {
        config[key] = {
          label: clientLabels[key] || key,
          color: CLIENT_COLOR_PALETTE[index % CLIENT_COLOR_PALETTE.length],
        }
      })
    }

    return config
  }, [model?.clientKeys, clientLabels, showStreamWindow])

  const normalizedEventMarkers = useMemo(() => {
    if (!Array.isArray(eventMarkers) || eventMarkers.length === 0) {
      return []
    }

    return eventMarkers
      .map((marker, index) => {
        const parsedTime = Number.parseInt(String(marker?.time ?? ''), 10)
        if (!Number.isFinite(parsedTime) || parsedTime <= 0) return null
        return {
          id: String(marker?.id || `event-${index}-${parsedTime}`),
          time: parsedTime,
          label: String(marker?.label || 'Recovery').trim() || 'Recovery',
          type: String(marker?.type || 'recovery').trim().toLowerCase() || 'recovery',
        }
      })
      .filter(Boolean)
      .slice(-24)
  }, [eventMarkers])

  if (!hydrated) {
    return (
      <div className={cn('rounded-lg border bg-muted/20 px-3 py-2 text-xs text-muted-foreground animate-pulse', className)}>
        Loading historical timeline...
      </div>
    )
  }

  if (!model || !model.chartData.length) {
    return (
      <div className={cn('rounded-lg border bg-muted/20 px-3 py-2 text-xs text-muted-foreground', className)}>
        Waiting for stream history...
      </div>
    )
  }

  const { chartData, clientKeys, yMax } = model
  const yMin = -Math.max(0.3, yMax * 0.04)
  const yTicks = Array.from(new Set([
    0,
    Math.round(yMax * 0.25),
    Math.round(yMax * 0.5),
    Math.round(yMax * 0.75),
    Math.round(yMax),
  ].filter((value) => Number.isFinite(value) && value >= 0))).sort((a, b) => a - b)

  return (
    <div className={cn('space-y-2', className)}>
      {!compact && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={isLive ? 'success' : 'secondary'}>{isLive ? 'LIVE' : 'Not live'}</Badge>
          <Badge variant="outline">History {chartData.length} ticks</Badge>
          <span className="text-xs text-muted-foreground">Y: Seconds (Viewer Runway / Swarm Lead / Threshold)</span>
        </div>
      )}

      <ChartContainer
        config={chartConfig}
        className={cn('w-full rounded-xl border border-border bg-muted/30 px-2', compact ? 'h-32 py-1' : 'h-44 py-2')}
      >
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart data={chartData} margin={{ top: 8, right: 10, bottom: 6, left: 10 }}>
            <CartesianGrid vertical={false} strokeDasharray="3 3" className="opacity-20" />

            <XAxis
              type="number"
              dataKey="time"
              domain={['dataMin', 'dataMax']}
              tickLine={false}
              axisLine={false}
              tickMargin={6}
              minTickGap={16}
              tickFormatter={(value) => formatClock(value)}
            />

            <YAxis
              type="number"
              domain={[yMin, yMax]}
              reversed
              ticks={yTicks}
              tickLine={false}
              axisLine={false}
              tickMargin={6}
              width={36}
              tickFormatter={(value) => {
                const normalized = Math.round(Math.max(0, Number(value) || 0))
                return normalized === 0 ? 'Live' : `${normalized}s`
              }}
            />

            <ChartTooltip
              cursor={false}
              content={(props) => (
                <ChartTooltipContent
                  {...props}
                  payload={(props?.payload || []).filter((entry) => {
                    const key = String(entry?.dataKey || '')
                    return !key.endsWith('__band') && !key.endsWith('__overlay')
                  })}
                  labelFormatter={(label) => formatClock(label)}
                  formatter={(value, name, entry) => {
                    const typedValue = Number(value)
                    if (!Number.isFinite(typedValue)) return 'N/A'
                    
                    const runway = entry?.payload?.[`${name}__runway`]
                    if (name.startsWith('client_') && Number.isFinite(runway)) {
                      return `${typedValue.toFixed(1)}s (${runway.toFixed(1)}s buffer)`
                    }
                    
                    return `${typedValue.toFixed(1)}s`
                  }}
                />
              )}
            />

            <Area
              type="linear"
              dataKey="engineLag__band"
              name="engineLag"
              stroke="none"
              fill="var(--color-engineLag)"
              fillOpacity={0.08}
              connectNulls={false}
              isAnimationActive={false}
              dot={false}
              activeDot={false}
            />


            {showStreamWindow && (
              <Line
                type="monotone"
                dataKey="streamWindow"
                name="streamWindow"
                stroke="var(--color-streamWindow)"
                strokeWidth={1.5}
                strokeDasharray="4 3"
                connectNulls={false}
                isAnimationActive={false}
                dot={false}
                activeDot={false}
              />
            )}

            {clientKeys.map((key, index) => (
              <React.Fragment key={key}>
                <Area
                  type="linear"
                  dataKey={`${key}__band`}
                  stroke="none"
                  fill={`var(--color-${key})`}
                  fillOpacity={0.06}
                  connectNulls={false}
                  isAnimationActive={false}
                  dot={false}
                  activeDot={false}
                />

                <Line
                  type="monotone"
                  dataKey={key}
                  name={key}
                  stroke={`var(--color-${key})`}
                  strokeWidth={1.5}
                  connectNulls={false}
                  isAnimationActive={false}
                  dot={false}
                  activeDot={false}
                />
              </React.Fragment>
            ))}

            {normalizedEventMarkers.map((marker) => (
              <ReferenceLine
                key={marker.id}
                x={marker.time}
                stroke={
                  marker.type === 'engine_switch'
                    ? 'hsl(var(--chart-4, 262 83% 58%))'
                    : marker.type === 'failover'
                      ? 'hsl(var(--destructive, 0 84% 60%))'
                      : marker.type === 'recovery'
                        ? 'hsl(var(--warning, 38 92% 50%))'
                        : 'hsl(var(--muted-foreground))'
                }
                strokeWidth={1.4}
                strokeOpacity={compact ? 0.6 : 0.8}
                ifOverflow="extendDomain"
                strokeDasharray={marker.type === 'engine_switch' || marker.type === 'failover' ? '4 2' : undefined}
              />
            ))}

            <ReferenceLine
              y={0}
              stroke="var(--color-liveEdge)"
              strokeWidth={1.2}
              strokeOpacity={0.95}
              ifOverflow="extendDomain"
            />


            <Line
              type="monotone"
              dataKey="engineLag"
              name="engineLag"
              stroke="var(--color-engineLag)"
              strokeWidth={1}
              strokeOpacity={0.4}
              strokeDasharray="4 4"
              connectNulls={false}
              isAnimationActive={false}
              dot={false}
              activeDot={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </ChartContainer>
    </div>
  )
}

StreamTimelineGraphic.displayName = 'StreamTimelineGraphic'

export default StreamTimelineGraphic
