import { useState, useEffect, useCallback } from 'react'

export function useServerDetail({ orchUrl, serverId }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetch_ = useCallback(async () => {
    if (!serverId) return
    setLoading(true)
    setError(null)
    try {
      const res = await fetch(`${orchUrl}/api/v1/vpn/servers/${encodeURIComponent(serverId)}/detail`)
      if (!res.ok) throw new Error(`${res.status}`)
      setDetail(await res.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [orchUrl, serverId])

  useEffect(() => { fetch_() }, [fetch_])

  return { detail, loading, error, refetch: fetch_ }
}
