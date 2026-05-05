import { useState, useEffect, useCallback } from 'react'

export function useRecentProbes({ orchUrl, apiKey }) {
  const [probes, setProbes] = useState([])

  const fetch_ = useCallback(async () => {
    try {
      const headers = {}
      if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`
      const res = await fetch(`${orchUrl}/api/v1/vpn/reputation/recent-probes`, { headers })
      if (!res.ok) return
      const data = await res.json()
      setProbes(data.items || [])
    } catch { /* ignore */ }
  }, [orchUrl, apiKey])

  useEffect(() => {
    fetch_()
    const t = setInterval(fetch_, 30000)
    return () => clearInterval(t)
  }, [fetch_])

  // Prepend on SSE event.
  const prependProbe = useCallback(probe => {
    setProbes(prev => [probe, ...prev].slice(0, 10))
  }, [])

  return { probes, prependProbe, refetch: fetch_ }
}
