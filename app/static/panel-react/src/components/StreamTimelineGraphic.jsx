import React from 'react'
import { Badge } from '@/components/ui/badge'

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
  return `${seconds.toFixed(2)}s`
}

function getClientInitial(client) {
  const source = String(client?.ip_address || client?.client_id || '?').trim()
  return source.charAt(0).toUpperCase() || '?'
}

export default function StreamTimelineGraphic({
  livepos,
  clients = [],
  isLive = false,
  compact = false,
}) {
  const liveFirst = toNumber(livepos?.live_first ?? livepos?.first_ts)
  const liveLast = toNumber(livepos?.live_last ?? livepos?.last_ts)
  const pos = toNumber(livepos?.pos)
  const lastTs = toNumber(livepos?.last_ts ?? liveLast)
  const hasWindow = Number.isFinite(liveFirst) && Number.isFinite(liveLast) && liveLast > liveFirst

  if (!hasWindow) {
    return (
      <div className="rounded-lg border bg-muted/20 px-3 py-2 text-xs text-muted-foreground">
        Timeline unavailable
      </div>
    )
  }

  const totalRange = liveLast - liveFirst
  const liveEdgePercent = 100
  const enginePercent = Number.isFinite(pos) ? clamp(((pos - liveFirst) / totalRange) * 100, 0, 100) : 0
  const bufferEndPercent = Number.isFinite(lastTs) ? clamp(((lastTs - liveFirst) / totalRange) * 100, 0, 100) : liveEdgePercent
  const bufferWidthPercent = Math.max(0, bufferEndPercent - enginePercent)

  const normalizedClients = clients
    .map((client, index) => {
      const lag = Number.parseFloat(String(client?.buffer_seconds_behind ?? ''))
      if (!Number.isFinite(lag)) return null
      const markerTs = liveLast - Math.max(0, lag)
      const percent = clamp(((markerTs - liveFirst) / totalRange) * 100, 0, 100)
      return {
        client,
        lag,
        percent,
        key: client?.client_id || client?.ip_address || `client-${index}`,
      }
    })
    .filter(Boolean)

  const clusterMap = new Map()
  normalizedClients.forEach((entry) => {
    const key = String(Math.round(entry.percent * 2) / 2)
    if (!clusterMap.has(key)) {
      clusterMap.set(key, {
        percent: entry.percent,
        clients: [],
      })
    }
    clusterMap.get(key).clients.push(entry)
  })

  const clusters = Array.from(clusterMap.values()).sort((a, b) => a.percent - b.percent)

  return (
    <div className="space-y-2">
      {!compact && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={isLive ? 'success' : 'secondary'}>{isLive ? 'LIVE' : 'Not live'}</Badge>
          <span className="text-xs text-muted-foreground">Engine at {enginePercent.toFixed(1)}% of live window</span>
        </div>
      )}

      <div className={`rounded-xl border bg-muted/20 px-3 ${compact ? 'py-2' : 'py-3'}`}>
        <div className={`relative ${compact ? 'h-8' : 'h-12'}`}>
          <div className="absolute left-0 right-0 top-1/2 h-2 -translate-y-1/2 rounded-full bg-muted" />

          <div
            className="absolute top-1/2 h-2 -translate-y-1/2 rounded-full bg-emerald-500/60"
            style={{
              left: `${enginePercent}%`,
              width: `${bufferWidthPercent}%`,
            }}
          />

          <div
            className="absolute top-1/2 z-10 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background bg-primary shadow"
            style={{ left: `${enginePercent}%` }}
            title="Engine buffer position"
          />

          <div
            className="absolute top-1/2 z-10 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rounded-full border-2 border-background bg-red-500 shadow"
            style={{ left: `${liveEdgePercent}%` }}
            title="Live edge"
          />

          {clusters.map((cluster, index) => {
            const count = cluster.clients.length
            const first = cluster.clients[0]
            const title = cluster.clients
              .map(({ client, lag }) => `${client.ip_address || client.client_id || 'client'} · ${formatLag(lag)} behind`)
              .join('\n')

            return (
              <div
                key={`cluster-${index}-${cluster.percent}`}
                className="absolute top-[78%] z-20 -translate-x-1/2"
                style={{ left: `${cluster.percent}%` }}
                title={title}
              >
                {count > 1 ? (
                  <div className="flex h-5 min-w-5 items-center justify-center rounded-full border border-border bg-amber-500 px-1 text-[10px] font-semibold text-white shadow">
                    +{count}
                  </div>
                ) : (
                  <div className="flex h-5 w-5 items-center justify-center rounded-full border border-border bg-sky-600 text-[10px] font-semibold text-white shadow">
                    {getClientInitial(first.client)}
                  </div>
                )}
              </div>
            )
          })}
        </div>

        {!compact && (
          <div className="mt-2 flex items-center justify-between text-[11px] text-muted-foreground">
            <span>Window start</span>
            <span>Live edge</span>
          </div>
        )}
      </div>
    </div>
  )
}
