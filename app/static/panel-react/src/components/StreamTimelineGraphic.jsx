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
  isLive = false,
  compact = false,
  className,
}) {
  const storageKey = useMemo(() => buildStorageKey(streamId), [streamId])
  const [history, setHistory] = useState([])
  const [clientLabels, setClientLabels] = useState({})
  const [hydrated, setHydrated] = useState(false)
  const lastSignatureRef = useRef('')

  useEffect(() => {
    const loaded = readHistoryFromStorage(storageKey)
    setHistory(Array.isArray(loaded.points) ? loaded.points : [])
    setClientLabels(loaded.labels || {})
    lastSignatureRef.current = ''
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

    ;(Array.isArray(clients) ? clients : []).forEach((client, index) => {
      const lag = toNumber(client?.buffer_seconds_behind)
      if (!Number.isFinite(lag)) return
      const key = getClientSeriesKey(client, index)
      clientValues[key] = Math.max(0, lag)
      nextLabels[key] = getClientLabel(client, index)
    })

    const signature = JSON.stringify({
      engineLag: Number(engineLag.toFixed(3)),
      clients: Object.keys(clientValues)
        .sort()
        .map((key) => [key, Number(clientValues[key].toFixed(3))]),
    })

    if (signature === lastSignatureRef.current) {
      return
    }
    lastSignatureRef.current = signature

    const tick = {
      time: timestamp,
      engineLag,
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
  }, [clients, livepos?.last_ts, livepos?.live_last, livepos?.pos, storageKey])

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
      const normalized = {
        time: point.time,
        engineLag: toNumber(point.engineLag) ?? null,
      }
      clientKeys.forEach((key) => {
        const value = toNumber(point[key])
        normalized[key] = Number.isFinite(value) ? Math.max(0, value) : null
      })
      return normalized
    })

    const rawMaxLag = normalizedHistory.reduce((max, point) => {
      let nextMax = max
      const engineLag = toNumber(point.engineLag)
      if (Number.isFinite(engineLag)) {
        nextMax = Math.max(nextMax, engineLag)
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
  }, [history, clientLabels])

  const chartConfig = useMemo(() => {
    const config = {
      engineLag: {
        label: 'Engine lag',
        color: 'hsl(var(--primary))',
      },
      liveEdge: {
        label: 'Live edge',
        color: 'hsl(var(--ring))',
      },
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
  }, [model?.clientKeys, clientLabels])

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

  return (
    <div className={cn('space-y-2', className)}>
      {!compact && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={isLive ? 'success' : 'secondary'}>{isLive ? 'LIVE' : 'Not live'}</Badge>
          <Badge variant="outline">History {chartData.length} ticks</Badge>
          <span className="text-xs text-muted-foreground">Y: Seconds behind live edge</span>
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
              domain={[0, yMax]}
              reversed
              tickLine={false}
              axisLine={false}
              tickMargin={6}
              width={36}
              tickFormatter={(value) => `${Math.round(value)}s`}
            />

            <ChartTooltip
              cursor={false}
              content={(
                <ChartTooltipContent
                  labelFormatter={(label) => formatClock(label)}
                  formatter={(value, name) => {
                    if (!Number.isFinite(Number(value))) return 'N/A'
                    const prettyName = chartConfig[name]?.label || name
                    return `${prettyName}: ${Number(value).toFixed(2)}s`
                  }}
                />
              )}
            />

            <ReferenceLine y={0} stroke="var(--color-liveEdge)" strokeDasharray="4 4" strokeWidth={1.5} />

            <Area
              type="monotone"
              dataKey="engineLag"
              name="engineLag"
              stroke="var(--color-engineLag)"
              fill="var(--color-engineLag)"
              fillOpacity={0.22}
              strokeWidth={2}
              connectNulls={false}
              isAnimationActive={false}
              dot={compact ? false : { r: 1.5 }}
            />

            {clientKeys.map((key, index) => (
              <Line
                key={key}
                type="monotone"
                dataKey={key}
                name={key}
                stroke={chartConfig[key]?.color || CLIENT_COLOR_PALETTE[index % CLIENT_COLOR_PALETTE.length]}
                strokeWidth={1.5}
                connectNulls={false}
                isAnimationActive={false}
                dot={{ r: 2 }}
                activeDot={{ r: 3.5 }}
              />
            ))}
          </ComposedChart>
        </ResponsiveContainer>
      </ChartContainer>
    </div>
  )
}

StreamTimelineGraphic.displayName = 'StreamTimelineGraphic'

export default StreamTimelineGraphic
