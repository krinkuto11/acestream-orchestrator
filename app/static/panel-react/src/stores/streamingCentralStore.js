import { create } from 'zustand'

const MAX_KPI_POINTS = 900
const MAX_BUFFER_BUCKETS = 60

const trim = (arr, max) => (arr.length <= max ? arr : arr.slice(arr.length - max))

const parseBufferPieces = (stream) => {
  const proxyPieces = stream?.proxy_buffer_pieces
  if (proxyPieces != null) return Number(proxyPieces)

  const raw = stream?.livepos?.buffer_pieces
  if (raw == null || raw === '') return 0
  const value = Number(raw)
  return Number.isFinite(value) ? value : 0
}

const buildHeaders = (apiKey) => {
  if (!apiKey) return {}
  return {
    Authorization: `Bearer ${apiKey}`,
  }
}

const toFallbackEgressGbps = (streams) => {
  const egressMbps = (streams || []).reduce((sum, stream) => {
    const speedUp = Number(stream?.speed_up || 0)
    if (!Number.isFinite(speedUp) || speedUp <= 0) return sum
    return sum + (speedUp / 1024) * 8
  }, 0)
  return egressMbps / 1000
}

export const useStreamingCentralStore = create((set, get) => ({
  engines: [],
  streams: [],
  vpnStatus: null,
  orchestratorStatus: null,
  dashboardSnapshot: null,
  engineStatsById: {},
  engineInspectById: {},
  lastInspectRefreshMs: 0,
  engineStartEvents: [],
  isBackendRefreshing: false,

  kpiHistory: {
    timestamps: [],
    activeStreams: [],
    egressGbps: [],
    healthyEngines: [],
    successRate: [],
  },

  bufferBuckets: [],
  lastBufferBucketMs: 0,

  selectedEngineId: null,
  logsByContainerId: {},
  logsLoadingByContainerId: {},
  logsErrorByContainerId: {},

  ingestLiveSnapshot: ({ engines, streams, vpnStatus, orchestratorStatus }) => {
    const now = new Date()
    const nowMs = now.getTime()

    const snapshotEngines = Array.isArray(engines) ? engines : []
    const snapshotStreams = Array.isArray(streams) ? streams : []
    const dashboardSnapshot = get().dashboardSnapshot

    const activeStreams = Number(orchestratorStatus?.streams?.active ?? snapshotStreams.length)
    const healthyEngines = Number(
      orchestratorStatus?.engines?.healthy ?? snapshotEngines.filter((engine) => engine.health_status === 'healthy').length,
    )

    const egressGbpsFromMetrics = Number(dashboardSnapshot?.proxy?.throughput?.egress_mbps || 0) / 1000
    const egressGbps =
      Number.isFinite(egressGbpsFromMetrics) && egressGbpsFromMetrics > 0
        ? egressGbpsFromMetrics
        : toFallbackEgressGbps(snapshotStreams)

    const successRateRaw = Number(dashboardSnapshot?.proxy?.request_window_1m?.success_rate_percent)
    const successRate = Number.isFinite(successRateRaw)
      ? successRateRaw
      : orchestratorStatus?.status === 'healthy'
        ? 99.5
        : 95

    set((state) => {
      const timestamps = trim([...state.kpiHistory.timestamps, now.toISOString()], MAX_KPI_POINTS)
      const activeStreamsHistory = trim([...state.kpiHistory.activeStreams, activeStreams], MAX_KPI_POINTS)
      const egressHistory = trim([...state.kpiHistory.egressGbps, Number(egressGbps.toFixed(3))], MAX_KPI_POINTS)
      const healthyHistory = trim([...state.kpiHistory.healthyEngines, healthyEngines], MAX_KPI_POINTS)
      const successHistory = trim([...state.kpiHistory.successRate, Number(successRate.toFixed(2))], MAX_KPI_POINTS)

      let nextBufferBuckets = state.bufferBuckets
      let nextLastBufferBucketMs = state.lastBufferBucketMs

      if (nowMs - state.lastBufferBucketMs >= 5000) {
        const values = {}
        snapshotStreams.slice(0, 60).forEach((stream) => {
          values[stream.id] = parseBufferPieces(stream)
        })

        nextBufferBuckets = trim(
          [
            ...state.bufferBuckets,
            {
              ts: now.toISOString(),
              values,
            },
          ],
          MAX_BUFFER_BUCKETS,
        )

        nextLastBufferBucketMs = nowMs
      }

      return {
        engines: snapshotEngines,
        streams: snapshotStreams,
        vpnStatus: vpnStatus || null,
        orchestratorStatus: orchestratorStatus || null,
        kpiHistory: {
          timestamps,
          activeStreams: activeStreamsHistory,
          egressGbps: egressHistory,
          healthyEngines: healthyHistory,
          successRate: successHistory,
        },
        bufferBuckets: nextBufferBuckets,
        lastBufferBucketMs: nextLastBufferBucketMs,
      }
    })
  },

  refreshBackendTelemetry: async ({ orchUrl, apiKey }) => {
    if (!orchUrl) return

    set({ isBackendRefreshing: true })

    const headers = buildHeaders(apiKey)

    const dashboardPromise = fetch(`${orchUrl}/api/v1/metrics/dashboard?window_seconds=900&max_points=240`, {
      headers,
    })
      .then((res) => {
        if (!res.ok) throw new Error(`metrics_dashboard_${res.status}`)
        return res.json()
      })
      .catch(() => null)

    const statsPromise = fetch(`${orchUrl}/api/v1/engines/stats/all`, { headers })
      .then((res) => {
        if (!res.ok) throw new Error(`engine_stats_${res.status}`)
        return res.json()
      })
      .catch(() => ({}))

    const eventsPromise = fetch(
      `${orchUrl}/api/v1/events?event_type=engine&category=created&limit=100`,
      { headers },
    )
      .then((res) => {
        if (!res.ok) throw new Error(`events_${res.status}`)
        return res.json()
      })
      .catch(() => [])

    const [dashboardSnapshot, engineStatsById, engineStartEvents] = await Promise.all([
      dashboardPromise,
      statsPromise,
      eventsPromise,
    ])

    const nowMs = Date.now()
    const shouldRefreshInspect =
      nowMs - Number(get().lastInspectRefreshMs || 0) >= 30000 &&
      Array.isArray(get().engines) &&
      get().engines.length > 0

    let engineInspectById = get().engineInspectById || {}

    if (shouldRefreshInspect) {
      const inspectEntries = await Promise.all(
        get().engines.map(async (engine) => {
          try {
            const response = await fetch(
              `${orchUrl}/api/v1/containers/${encodeURIComponent(engine.container_id)}`,
              { headers },
            )
            if (!response.ok) return [engine.container_id, null]
            const payload = await response.json()
            return [engine.container_id, payload]
          } catch {
            return [engine.container_id, null]
          }
        }),
      )

      engineInspectById = inspectEntries.reduce((acc, [containerId, payload]) => {
        if (payload) {
          acc[containerId] = payload
        }
        return acc
      }, { ...engineInspectById })
    }

    set({
      dashboardSnapshot,
      engineStatsById: engineStatsById || {},
      engineInspectById,
      lastInspectRefreshMs: shouldRefreshInspect ? nowMs : get().lastInspectRefreshMs,
      engineStartEvents: Array.isArray(engineStartEvents) ? engineStartEvents : [],
      isBackendRefreshing: false,
    })
  },

  openEngineLogs: (containerId) => {
    set({ selectedEngineId: containerId })
  },

  closeEngineLogs: () => {
    set({ selectedEngineId: null })
  },

  fetchContainerLogs: async ({ orchUrl, apiKey, containerId }) => {
    if (!orchUrl || !containerId) return

    set((state) => ({
      logsLoadingByContainerId: {
        ...state.logsLoadingByContainerId,
        [containerId]: true,
      },
      logsErrorByContainerId: {
        ...state.logsErrorByContainerId,
        [containerId]: null,
      },
    }))

    try {
      const response = await fetch(
        `${orchUrl}/api/v1/containers/${encodeURIComponent(containerId)}/logs?tail=300&since_seconds=1200`,
        {
          headers: buildHeaders(apiKey),
        },
      )

      if (!response.ok) {
        throw new Error(`logs_${response.status}`)
      }

      const data = await response.json()
      const lines = String(data.logs || '')
        .split('\n')
        .filter(Boolean)

      set((state) => ({
        logsByContainerId: {
          ...state.logsByContainerId,
          [containerId]: {
            lines,
            fetchedAt: data.fetched_at || new Date().toISOString(),
          },
        },
      }))
    } catch (error) {
      set((state) => ({
        logsErrorByContainerId: {
          ...state.logsErrorByContainerId,
          [containerId]: String(error?.message || error),
        },
      }))
    } finally {
      set((state) => ({
        logsLoadingByContainerId: {
          ...state.logsLoadingByContainerId,
          [containerId]: false,
        },
      }))
    }
  },
}))
