import { useState, useEffect, useCallback } from 'react'

export function useServerDetail({ orchUrl, apiKey, serverId }) {
  const [detail, setDetail] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)

  const fetch_ = useCallback(async () => {
    if (!serverId) return
    setLoading(true)
    setError(null)
    try {
      const headers = {}
      if (apiKey) headers['Authorization'] = `Bearer ${apiKey}`
      const res = await fetch(`${orchUrl}/api/v1/vpn/servers/${encodeURIComponent(serverId)}/detail`, { headers })
      if (!res.ok) throw new Error(`${res.status}`)
      setDetail(await res.json())
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [orchUrl, apiKey, serverId])

  useEffect(() => { fetch_() }, [fetch_])

  return { detail, loading, error, refetch: fetch_ }
}
