import { useState, useEffect, useCallback, useRef } from 'react'

export function useVpnServers({ orchUrl, apiKey, filter = {} }) {
  const [items, setItems] = useState([])
  const [nextCursor, setNextCursor] = useState(null)
  const [totalMatched, setTotalMatched] = useState(0)
  const [stats, setStats] = useState({ by_source: {}, by_status: {}, by_color: {} })
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetchRef = useRef(null)

  const buildUrl = useCallback((cursor = null) => {
    const url = new URL(`${orchUrl}/api/v1/vpn/servers`)
    if (filter.source && filter.source !== 'all') url.searchParams.set('source', filter.source)
    if (filter.q) url.searchParams.set('q', filter.q)
    if (filter.quarantined) url.searchParams.set('quarantined', filter.quarantined)
    if (filter.sort) url.searchParams.set('sort', filter.sort)
    if (filter.dir) url.searchParams.set('dir', filter.dir)
    if (filter.category) url.searchParams.set('category', filter.category)
    if (cursor) url.searchParams.set('cursor', cursor)
    url.searchParams.set('limit', '100')
    return url.toString()
  }, [orchUrl, filter.source, filter.q, filter.quarantined, filter.sort, filter.dir, filter.category])

  const fetchServers = useCallback(async (cursor = null) => {
    setLoading(true)
    setError(null)
    try {
      const headers = {}
      if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`
      const res = await fetch(buildUrl(cursor), { headers })
      if (!res.ok) throw new Error(`${res.status}`)
      const data = await res.json()
      if (cursor) {
        setItems(prev => [...prev, ...(data.items || [])])
      } else {
        setItems(data.items || [])
      }
      setNextCursor(data.next_cursor || null)
      setTotalMatched(data.total_matched || 0)
      setStats(data.stats || { by_source: {}, by_status: {}, by_color: {} })
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [buildUrl, apiKey])

  // Refetch on filter change.
  useEffect(() => {
    fetchRef.current = fetchServers
    fetchServers()
  }, [fetchServers])

  const loadMore = useCallback(() => {
    if (nextCursor && !loading) fetchServers(nextCursor)
  }, [nextCursor, loading, fetchServers])

  // Patch a single item in-place (for SSE updates).
  const patchItem = useCallback((id, patch) => {
    setItems(prev => prev.map(item => item.id === id ? { ...item, ...patch } : item))
  }, [])

  // Subscribe to SSE reputation stream.
  useEffect(() => {
    if (!orchUrl) return
    const url = new URL(`${orchUrl}/api/v1/vpn/reputation/stream`)
    if (apiKey) url.searchParams.set('api_key', apiKey)

    const es = new EventSource(url.toString())

    const handleEvent = (type, handler) => {
      es.addEventListener(type, e => {
        try { handler(JSON.parse(e.data)) } catch { /* ignore */ }
      })
    }

    handleEvent('vpn.server.upserted', d => {
      patchItem(d.id, { status: d.status, hostname: d.hostname })
    })
    handleEvent('vpn.server.quarantined', d => {
      patchItem(d.id, { quarantined: !!d.until, quarantine_until: d.until })
    })
    handleEvent('vpn.server.pinned', d => {
      patchItem(d.id, { pinned: d.pinned })
    })
    handleEvent('vpn.reputation.refreshed', d => {
      patchItem(d.server_id, {
        score: d.score,
        score_color: d.score_color,
      })
    })
    handleEvent('vpn.probe.completed', () => {
      // Bump recent-probes section via rerender; actual data fetched by useRecentProbes.
    })

    return () => es.close()
  }, [orchUrl, apiKey, patchItem])

  return { items, nextCursor, totalMatched, stats, loading, error, loadMore, patchItem, refetch: () => fetchServers() }
}
