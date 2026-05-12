import { useState, useEffect, useCallback } from 'react'

export function useRecentProbes({ orchUrl }) {
  const [probes, setProbes] = useState([])

  const fetch_ = useCallback(async () => {
    try {
      const res = await fetch(`${orchUrl}/api/v1/vpn/reputation/recent-probes`)
      if (!res.ok) return
      const data = await res.json()
      setProbes(data.items || [])
    } catch { /* ignore */ }
  }, [orchUrl])

  useEffect(() => {
    fetch_()
    const t = setInterval(fetch_, 30000)
    return () => clearInterval(t)
  }, [fetch_])

  const prependProbe = useCallback(probe => {
    setProbes(prev => [probe, ...prev].slice(0, 10))
  }, [])

  return { probes, prependProbe, refetch: fetch_ }
}
