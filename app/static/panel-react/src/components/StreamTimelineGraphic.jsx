import React from 'react'
import { Badge } from '@/components/ui/badge'
import { Avatar, AvatarFallback } from '@/components/ui/avatar'
import { cn } from '@/lib/utils'

const MIN_ZOOM_WINDOW_SECONDS = 60
const CLUSTER_BUCKET_PERCENT = 2

function toNumber(value) {
  const parsed = Number.parseFloat(String(value ?? ''))
  return Number.isFinite(parsed) ? parsed : null
}

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value))
}

function formatLag(seconds) {
  if (!Number.isFinite(seconds)) return 'N/A'
  if (seconds <= 0) return 'Live'
  if (seconds < 0.1) return `${Math.round(seconds * 1000)}ms`
  return `${seconds.toFixed(1)}s`
}

function getClientInitial(client) {
  const source = String(client?.ip_address || client?.client_id || '?').trim()
  return source.charAt(0).toUpperCase() || '?'
}

function getClusterTone(maxLagSeconds) {
  if (maxLagSeconds > 5) {
    return {
      marker: 'border-amber-500/60 bg-amber-500 text-white',
      line: 'bg-amber-500/70',
    }
  }

  return {
    marker: 'border-emerald-500/60 bg-emerald-500 text-white',
    line: 'bg-emerald-500/70',
  }
}

function buildTimelineModel(livepos, clients) {
  const liveEdgeTs = toNumber(livepos?.last_ts ?? livepos?.live_last)
  if (!Number.isFinite(liveEdgeTs)) return null

  const enginePosTs = toNumber(livepos?.pos)
  const bufferStartTs = toNumber(livepos?.first_ts ?? livepos?.live_first)
  const bufferEndTs = toNumber(livepos?.last_ts ?? livepos?.live_last)

  const normalizedClients = (Array.isArray(clients) ? clients : [])
    .map((client, index) => {
      const lagSeconds = Math.max(0, toNumber(client?.buffer_seconds_behind) ?? 0)
      const timestamp = liveEdgeTs - lagSeconds
      return {
        client,
        lagSeconds,
        timestamp,
        key: client?.client_id || client?.ip_address || `client-${index}`,
      }
    })

  const maxClientLag = normalizedClients.reduce((max, entry) => Math.max(max, entry.lagSeconds), 0)
  const engineLag = Number.isFinite(enginePosTs) ? Math.max(0, liveEdgeTs - enginePosTs) : 0
  const zoomWindowSeconds = Math.max(
    MIN_ZOOM_WINDOW_SECONDS,
    maxClientLag * 1.5,
    engineLag * 1.15,
  )

  const windowStartTs = liveEdgeTs - zoomWindowSeconds
  const toPercent = (timestamp) => clamp(((timestamp - windowStartTs) / zoomWindowSeconds) * 100, 0, 100)

  const clustersByBucket = new Map()
  normalizedClients.forEach((entry) => {
    const percent = toPercent(entry.timestamp)
    const bucket = Math.round(percent / CLUSTER_BUCKET_PERCENT) * CLUSTER_BUCKET_PERCENT
    const bucketKey = String(bucket)
    if (!clustersByBucket.has(bucketKey)) {
      clustersByBucket.set(bucketKey, {
        percent: bucket,
        clients: [],
        maxLagSeconds: 0,
      })
    }

    const bucketEntry = clustersByBucket.get(bucketKey)
    bucketEntry.clients.push({ ...entry, percent })
    bucketEntry.maxLagSeconds = Math.max(bucketEntry.maxLagSeconds, entry.lagSeconds)
  })

  const clusters = Array.from(clustersByBucket.values()).sort((a, b) => a.percent - b.percent)
  const enginePercent = Number.isFinite(enginePosTs) ? toPercent(enginePosTs) : 0
  const bufferedStartPercent = Number.isFinite(bufferStartTs) ? toPercent(bufferStartTs) : 0
  const bufferedEndPercent = Number.isFinite(bufferEndTs) ? toPercent(bufferEndTs) : 100

  return {
    clusters,
    enginePercent,
    bufferedStartPercent,
    bufferedEndPercent,
    zoomWindowSeconds,
    maxClientLag,
    windowStartTs,
    liveEdgeTs,
  }
}

function StreamTimelineGraphic({
  livepos,
  clients = [],
  isLive = false,
  compact = false,
  className,
}) {
  const model = buildTimelineModel(livepos, clients)

  if (!model) {
    return (
      <div className="rounded-lg border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        Timeline unavailable
      </div>
    )
  }

  const {
    clusters,
    enginePercent,
    bufferedStartPercent,
    bufferedEndPercent,
    zoomWindowSeconds,
    maxClientLag,
    windowStartTs,
    liveEdgeTs,
  } = model

  const bufferedLeft = Math.min(bufferedStartPercent, bufferedEndPercent)
  const bufferedWidth = Math.max(0, Math.abs(bufferedEndPercent - bufferedStartPercent))

  return (
    <div className={cn('space-y-2', className)}>
      {!compact && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={isLive ? 'success' : 'secondary'}>{isLive ? 'LIVE' : 'Not live'}</Badge>
          <Badge variant="outline">Zoom {Math.round(zoomWindowSeconds)}s</Badge>
          <span className="text-xs text-muted-foreground">
            Max client lag {formatLag(maxClientLag)}
          </span>
        </div>
      )}

      <div className={cn('rounded-xl border border-border bg-muted/30 px-3', compact ? 'py-2' : 'py-3')}>
        <div className={cn('relative overflow-hidden', compact ? 'h-14' : 'h-20')}>
          <div className={cn('absolute left-0 right-0 rounded-full bg-muted', compact ? 'bottom-1 h-2' : 'bottom-2 h-2.5')} />

          <div
            className={cn('absolute rounded-full bg-emerald-500/70 transition-all duration-300', compact ? 'bottom-1 h-2' : 'bottom-2 h-2.5')}
            style={{
              left: `${bufferedLeft}%`,
              width: `${bufferedWidth}%`,
            }}
          />

          <div
            className={cn('absolute z-20 -translate-x-1/2 rounded-full border-2 border-background bg-primary shadow transition-all duration-300', compact ? 'bottom-0.5 h-4 w-4' : 'bottom-1 h-4 w-4')}
            style={{ left: `${enginePercent}%` }}
            title="Engine playhead"
          />

          <div
            className={cn('absolute z-20 -translate-x-1/2 rounded-full border-2 border-background bg-red-500 shadow transition-all duration-300', compact ? 'bottom-0.5 h-4 w-4' : 'bottom-1 h-4 w-4')}
            style={{ left: '100%' }}
            title="Live edge"
          />

          {clusters.map((cluster, index) => {
            const first = cluster.clients[0]
            const tone = getClusterTone(cluster.maxLagSeconds)
            const title = cluster.clients
              .map(({ client, lagSeconds }) => {
                const label = client?.ip_address || client?.client_id || 'client'
                return `${label} - ${formatLag(lagSeconds)} behind`
              })
              .join('\n')

            return (
              <div
                key={`${index}-${cluster.percent}`}
                className="absolute z-30 -translate-x-1/2 transition-all duration-300"
                style={{ left: `${cluster.percent}%`, top: compact ? 2 : 4 }}
                title={title}
              >
                {cluster.clients.length > 1 ? (
                  <div className={cn('flex h-5 min-w-5 items-center justify-center rounded-full border px-1 text-[10px] font-semibold shadow', tone.marker)}>
                    +{cluster.clients.length}
                  </div>
                ) : (
                  <Avatar className="h-5 w-5 border border-border shadow">
                    <AvatarFallback className={cn('text-[10px] font-semibold', tone.marker)}>
                      {getClientInitial(first.client)}
                    </AvatarFallback>
                  </Avatar>
                )}
                <div className={cn('mx-auto mt-1 w-px rounded-full transition-all duration-300', tone.line, compact ? 'h-4' : 'h-6')} />
              </div>
            )
          })}
        </div>

        {!compact && (
          <div className="mt-1 flex items-center justify-between text-[11px] text-muted-foreground">
            <span>{new Date(windowStartTs * 1000).toLocaleTimeString()}</span>
            <span>{new Date(liveEdgeTs * 1000).toLocaleTimeString()} live edge</span>
          </div>
        )}
      </div>
    </div>
  )
}

StreamTimelineGraphic.displayName = 'StreamTimelineGraphic'

export default StreamTimelineGraphic
