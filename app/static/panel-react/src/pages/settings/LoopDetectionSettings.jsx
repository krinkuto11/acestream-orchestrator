import React, { useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { SettingRow } from '@/components/settings/SettingRow'
import { useSettingsForm } from '@/context/SettingsFormContext'

const DEFAULTS = {
  enabled: false,
  threshold_minutes: 60,
  check_interval_seconds: 10,
  retention_minutes: 0,
}

const toNumber = (value, fallback = 0) => {
  const next = Number(value)
  return Number.isFinite(next) ? next : fallback
}

export function LoopDetectionSettings({ apiKey, orchUrl, authRequired }) {
  const sectionId = 'loop-detection'
  const { registerSection, unregisterSection, setSectionDirty, setSectionSaving } = useSettingsForm()

  const [loading, setLoading] = useState(true)
  const [initialState, setInitialState] = useState(DEFAULTS)
  const [draft, setDraft] = useState(DEFAULTS)
  const [streams, setStreams] = useState([])
  const [streamsLoading, setStreamsLoading] = useState(false)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(initialState), [draft, initialState])

  const fetchConfig = async () => {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(`${orchUrl}/api/v1/stream-loop-detection/config`)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const payload = await response.json()
      const normalized = {
        enabled: Boolean(payload?.enabled),
        threshold_minutes: Math.round(toNumber(payload?.threshold_minutes, DEFAULTS.threshold_minutes)),
        check_interval_seconds: Math.round(toNumber(payload?.check_interval_seconds, DEFAULTS.check_interval_seconds)),
        retention_minutes: Math.round(toNumber(payload?.retention_minutes, DEFAULTS.retention_minutes)),
      }
      setInitialState(normalized)
      setDraft(normalized)
      setSectionDirty(sectionId, false)
    } catch (fetchError) {
      setError(`Failed to load loop detection settings: ${fetchError.message || String(fetchError)}`)
    } finally {
      setLoading(false)
    }
  }

  const fetchStreams = async () => {
    setStreamsLoading(true)
    try {
      const response = await fetch(`${orchUrl}/api/v1/looping-streams`)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const payload = await response.json()
      const mapped = Object.entries(payload?.streams || {}).map(([id, time]) => ({ id, time }))
      setStreams(mapped)
    } catch {
      // non-blocking list
    } finally {
      setStreamsLoading(false)
    }
  }

  useEffect(() => {
    fetchConfig()
    fetchStreams()
  }, [orchUrl])

  useEffect(() => {
    const save = async () => {
      if (authRequired && !String(apiKey || '').trim()) {
        throw new Error('API key required by server for loop detection settings updates')
      }

      setSectionSaving(sectionId, true)
      setError('')
      setMessage('')
      try {
        const headers = {}
        if (String(apiKey || '').trim()) {
          headers.Authorization = `Bearer ${String(apiKey).trim()}`
        }

        const params = new URLSearchParams()
        params.set('enabled', String(Boolean(draft.enabled)))
        params.set('threshold_seconds', String(Math.max(60, Math.round(toNumber(draft.threshold_minutes, 60) * 60))))
        params.set('check_interval_seconds', String(Math.max(5, Math.round(toNumber(draft.check_interval_seconds, 10)))))
        params.set('retention_minutes', String(Math.max(0, Math.round(toNumber(draft.retention_minutes, 0)))))

        const response = await fetch(`${orchUrl}/api/v1/stream-loop-detection/config?${params.toString()}`, {
          method: 'POST',
          headers,
        })

        if (!response.ok) {
          const failure = await response.json().catch(() => ({}))
          throw new Error(failure?.detail || `HTTP ${response.status}`)
        }

        setInitialState({ ...draft })
        setSectionDirty(sectionId, false)
        setMessage('Loop detection settings saved')
      } finally {
        setSectionSaving(sectionId, false)
      }
    }

    const discard = () => {
      setDraft(initialState)
      setSectionDirty(sectionId, false)
      setMessage('')
      setError('')
    }

    registerSection(sectionId, {
      title: 'Loop Detection',
      requiresAuth: true,
      save,
      discard,
    })

    return () => unregisterSection(sectionId)
  }, [
    apiKey,
    authRequired,
    draft,
    initialState,
    orchUrl,
    registerSection,
    setSectionDirty,
    setSectionSaving,
    unregisterSection,
  ])

  useEffect(() => {
    setSectionDirty(sectionId, dirty)
  }, [dirty, setSectionDirty])

  const removeStream = async (streamId) => {
    if (authRequired && !String(apiKey || '').trim()) {
      setError('API key required by server to remove looping streams')
      return
    }

    try {
      const headers = {}
      if (String(apiKey || '').trim()) {
        headers.Authorization = `Bearer ${String(apiKey).trim()}`
      }

      const response = await fetch(`${orchUrl}/api/v1/looping-streams/${encodeURIComponent(streamId)}`, {
        method: 'DELETE',
        headers,
      })
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(payload?.detail || `HTTP ${response.status}`)
      }

      setMessage('Looping stream removed')
      await fetchStreams()
    } catch (removeError) {
      setError(`Failed to remove stream: ${removeError.message || String(removeError)}`)
    }
  }

  const clearStreams = async () => {
    if (authRequired && !String(apiKey || '').trim()) {
      setError('API key required by server to clear looping streams')
      return
    }

    try {
      const headers = {}
      if (String(apiKey || '').trim()) {
        headers.Authorization = `Bearer ${String(apiKey).trim()}`
      }

      const response = await fetch(`${orchUrl}/api/v1/looping-streams/clear`, {
        method: 'POST',
        headers,
      })
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(payload?.detail || `HTTP ${response.status}`)
      }

      setMessage('Looping streams list cleared')
      await fetchStreams()
    } catch (clearError) {
      setError(`Failed to clear streams: ${clearError.message || String(clearError)}`)
    }
  }

  if (loading) {
    return (
      <Card>
        <CardContent className="py-10 text-sm text-muted-foreground">Loading loop detection settings...</CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-5">
      {message && <p className="text-sm text-emerald-600 dark:text-emerald-400">{message}</p>}
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <Card>
        <CardHeader>
          <CardTitle>Loop Detection Policy</CardTitle>
          <CardDescription>Static threshold and check cadence settings are controlled by global save/discard.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <SettingRow label="Detection Enabled" description="Enable automatic stale-stream detection.">
            <Select value={String(Boolean(draft.enabled))} onValueChange={(value) => setDraft((prev) => ({ ...prev, enabled: value === 'true' }))}>
              <SelectTrigger className="max-w-xs"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="true">Enabled</SelectItem>
                <SelectItem value="false">Disabled</SelectItem>
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow label="Threshold (minutes)" description="Stop stream if live position lags this long.">
            <Input type="number" min={1} value={draft.threshold_minutes} onChange={(e) => setDraft((prev) => ({ ...prev, threshold_minutes: Math.max(1, Math.round(toNumber(e.target.value, DEFAULTS.threshold_minutes)))}))} className="max-w-xs" />
          </SettingRow>

          <SettingRow label="Check Interval (seconds)" description="Detection cycle interval.">
            <Input type="number" min={5} value={draft.check_interval_seconds} onChange={(e) => setDraft((prev) => ({ ...prev, check_interval_seconds: Math.max(5, Math.round(toNumber(e.target.value, DEFAULTS.check_interval_seconds)))}))} className="max-w-xs" />
          </SettingRow>

          <SettingRow label="Retention (minutes)" description="How long blocked stream IDs remain in memory (0 = indefinite).">
            <Input type="number" min={0} value={draft.retention_minutes} onChange={(e) => setDraft((prev) => ({ ...prev, retention_minutes: Math.max(0, Math.round(toNumber(e.target.value, DEFAULTS.retention_minutes)))}))} className="max-w-xs" />
          </SettingRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>Active Looping Streams</CardTitle>
              <CardDescription>Operational list. Actions below apply immediately and do not affect global unsaved state.</CardDescription>
            </div>
            {streams.length > 0 && <Button variant="outline" onClick={clearStreams}>Clear All</Button>}
          </div>
        </CardHeader>
        <CardContent className="space-y-2">
          {streamsLoading ? (
            <p className="text-sm text-muted-foreground">Loading looping streams...</p>
          ) : streams.length === 0 ? (
            <p className="text-sm text-muted-foreground">No looping streams detected.</p>
          ) : (
            streams.map((item) => (
              <div key={item.id} className="flex items-center justify-between rounded-md border p-3">
                <div>
                  <p className="font-mono text-sm">{item.id}</p>
                  <p className="text-xs text-muted-foreground">Detected: {new Date(item.time).toLocaleString()}</p>
                </div>
                <Button variant="ghost" size="sm" onClick={() => removeStream(item.id)}>Remove</Button>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  )
}
