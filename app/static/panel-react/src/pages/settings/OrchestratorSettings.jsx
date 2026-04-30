import React, { useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { SettingRow } from '@/components/settings/SettingRow'
import { useSettingsForm } from '@/context/SettingsFormContext'

const DEFAULTS = {
  monitor_interval_s: 10,
  engine_grace_period_s: 30,
  autoscale_interval_s: 30,
  startup_timeout_s: 25,
  health_check_interval_s: 20,
  health_failure_threshold: 3,
  health_unhealthy_grace_period_s: 60,
  health_replacement_cooldown_s: 60,
  circuit_breaker_failure_threshold: 5,
  circuit_breaker_recovery_timeout_s: 300,
  circuit_breaker_replacement_threshold: 3,
  circuit_breaker_replacement_timeout_s: 180,
  port_range_host: '19000-19999',
  ace_http_range: '40000-44999',
  ace_https_range: '45000-49999',
  docker_network: '',
}

const toNumber = (value, fallback = 0) => {
  const next = Number(value)
  return Number.isFinite(next) ? next : fallback
}

export function OrchestratorSettings({ apiKey, orchUrl, authRequired }) {
  const sectionId = 'orchestrator'
  const {
    registerSection,
    unregisterSection,
    setSectionDirty,
    setSectionSaving,
  } = useSettingsForm()

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [initialState, setInitialState] = useState(DEFAULTS)
  const [draft, setDraft] = useState(DEFAULTS)

  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(initialState),
    [draft, initialState],
  )

  const fetchConfig = async () => {
    setLoading(true)
    setError('')
    try {
      let payload = null

      const consolidated = await fetch(`${orchUrl}/api/v1/settings`)
      if (consolidated.ok) {
        const settingsBundle = await consolidated.json().catch(() => ({}))
        payload = settingsBundle?.orchestrator_settings || null
      }

      if (!payload) {
        const response = await fetch(`${orchUrl}/api/v1/settings/orchestrator`)
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}`)
        }
        payload = await response.json()
      }

      const normalized = {
        ...DEFAULTS,
        ...payload,
        docker_network: String(payload?.docker_network || ''),
      }
      setInitialState(normalized)
      setDraft(normalized)
      setSectionDirty(sectionId, false)
    } catch (fetchError) {
      setError(`Failed to load orchestrator settings: ${fetchError.message || String(fetchError)}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchConfig()
  }, [orchUrl])

  useEffect(() => {
    const save = async () => {
      if (authRequired && !String(apiKey || '').trim()) {
        throw new Error('API key required by server for orchestrator settings updates')
      }

      setSectionSaving(sectionId, true)
      setMessage('')
      setError('')
      try {
        const headers = { 'Content-Type': 'application/json' }
        if (String(apiKey || '').trim()) {
          headers.Authorization = `Bearer ${String(apiKey).trim()}`
        }

        const response = await fetch(`${orchUrl}/api/v1/settings/orchestrator`, {
          method: 'POST',
          headers,
          body: JSON.stringify({
            ...draft,
            monitor_interval_s: toNumber(draft.monitor_interval_s, DEFAULTS.monitor_interval_s),
            engine_grace_period_s: toNumber(draft.engine_grace_period_s, DEFAULTS.engine_grace_period_s),
            autoscale_interval_s: toNumber(draft.autoscale_interval_s, DEFAULTS.autoscale_interval_s),
            startup_timeout_s: toNumber(draft.startup_timeout_s, DEFAULTS.startup_timeout_s),
            health_check_interval_s: toNumber(draft.health_check_interval_s, DEFAULTS.health_check_interval_s),
            health_failure_threshold: toNumber(draft.health_failure_threshold, DEFAULTS.health_failure_threshold),
            health_unhealthy_grace_period_s: toNumber(draft.health_unhealthy_grace_period_s, DEFAULTS.health_unhealthy_grace_period_s),
            health_replacement_cooldown_s: toNumber(draft.health_replacement_cooldown_s, DEFAULTS.health_replacement_cooldown_s),
            circuit_breaker_failure_threshold: toNumber(draft.circuit_breaker_failure_threshold, DEFAULTS.circuit_breaker_failure_threshold),
            circuit_breaker_recovery_timeout_s: toNumber(draft.circuit_breaker_recovery_timeout_s, DEFAULTS.circuit_breaker_recovery_timeout_s),
            circuit_breaker_replacement_threshold: toNumber(draft.circuit_breaker_replacement_threshold, DEFAULTS.circuit_breaker_replacement_threshold),
            circuit_breaker_replacement_timeout_s: toNumber(draft.circuit_breaker_replacement_timeout_s, DEFAULTS.circuit_breaker_replacement_timeout_s),
            docker_network: String(draft.docker_network || ''),
          }),
        })

        if (!response.ok) {
          const failure = await response.json().catch(() => ({}))
          throw new Error(failure?.detail || `HTTP ${response.status}`)
        }

        const payload = await response.json()
        const normalized = {
          ...DEFAULTS,
          ...payload,
          docker_network: String(payload?.docker_network || ''),
        }

        setInitialState(normalized)
        setDraft(normalized)
        setSectionDirty(sectionId, false)
        setMessage('Orchestrator settings saved')
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
      title: 'Orchestrator',
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

  const update = (field, value) => {
    setDraft((prev) => ({ ...prev, [field]: value }))
    setMessage('')
    setError('')
  }

  if (loading) {
    return (
      <Card>
        <CardContent className="py-10 text-sm text-muted-foreground">Loading orchestrator settings...</CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-5">
      {message && <p className="text-sm text-emerald-600 dark:text-emerald-400">{message}</p>}
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <Card>
        <CardHeader>
          <CardTitle>Timeouts</CardTitle>
          <CardDescription>Core lifecycle timing controls for startup and autoscaling loops.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <SettingRow label="Startup Timeout (s)" description="Max wait for an engine to become ready.">
            <Input value={draft.startup_timeout_s} type="number" min={5} max={180} onChange={(e) => update('startup_timeout_s', toNumber(e.target.value, DEFAULTS.startup_timeout_s))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Engine Grace Period (s)" description="Delay before stopping engine after last stream ends.">
            <Input value={draft.engine_grace_period_s} type="number" min={1} max={600} onChange={(e) => update('engine_grace_period_s', toNumber(e.target.value, DEFAULTS.engine_grace_period_s))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Autoscale Interval (s)" description="Frequency of autoscale decision cycles.">
            <Input value={draft.autoscale_interval_s} type="number" min={5} max={300} onChange={(e) => update('autoscale_interval_s', toNumber(e.target.value, DEFAULTS.autoscale_interval_s))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Monitor Interval (s)" description="Docker monitor sweep interval.">
            <Input value={draft.monitor_interval_s} type="number" min={1} max={60} onChange={(e) => update('monitor_interval_s', toNumber(e.target.value, DEFAULTS.monitor_interval_s))} className="max-w-xs" />
          </SettingRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Health Checks</CardTitle>
          <CardDescription>Controls for unhealthy detection and engine replacement behavior.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <SettingRow label="Health Check Interval (s)" description="How often engine health checks run.">
            <Input value={draft.health_check_interval_s} type="number" min={1} max={120} onChange={(e) => update('health_check_interval_s', toNumber(e.target.value, DEFAULTS.health_check_interval_s))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Failure Threshold" description="Consecutive failures before unhealthy state.">
            <Input value={draft.health_failure_threshold} type="number" min={1} max={20} onChange={(e) => update('health_failure_threshold', toNumber(e.target.value, DEFAULTS.health_failure_threshold))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Unhealthy Grace Period (s)" description="Delay before unhealthy engine replacement.">
            <Input value={draft.health_unhealthy_grace_period_s} type="number" min={10} max={600} onChange={(e) => update('health_unhealthy_grace_period_s', toNumber(e.target.value, DEFAULTS.health_unhealthy_grace_period_s))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Replacement Cooldown (s)" description="Minimum time between replacement operations.">
            <Input value={draft.health_replacement_cooldown_s} type="number" min={10} max={600} onChange={(e) => update('health_replacement_cooldown_s', toNumber(e.target.value, DEFAULTS.health_replacement_cooldown_s))} className="max-w-xs" />
          </SettingRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Circuit Breakers</CardTitle>
          <CardDescription>Failure isolation and recovery windows for provisioning and replacement paths.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <SettingRow label="Provision Failure Threshold" description="Failures before opening provisioning breaker.">
            <Input value={draft.circuit_breaker_failure_threshold} type="number" min={1} max={20} onChange={(e) => update('circuit_breaker_failure_threshold', toNumber(e.target.value, DEFAULTS.circuit_breaker_failure_threshold))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Provision Recovery Timeout (s)" description="Breaker timeout before retry window opens.">
            <Input value={draft.circuit_breaker_recovery_timeout_s} type="number" min={30} max={1800} onChange={(e) => update('circuit_breaker_recovery_timeout_s', toNumber(e.target.value, DEFAULTS.circuit_breaker_recovery_timeout_s))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Replacement Failure Threshold" description="Failures before replacement breaker opens.">
            <Input value={draft.circuit_breaker_replacement_threshold} type="number" min={1} max={20} onChange={(e) => update('circuit_breaker_replacement_threshold', toNumber(e.target.value, DEFAULTS.circuit_breaker_replacement_threshold))} className="max-w-xs" />
          </SettingRow>
          <SettingRow label="Replacement Recovery Timeout (s)" description="Replacement breaker cooldown.">
            <Input value={draft.circuit_breaker_replacement_timeout_s} type="number" min={30} max={1800} onChange={(e) => update('circuit_breaker_replacement_timeout_s', toNumber(e.target.value, DEFAULTS.circuit_breaker_replacement_timeout_s))} className="max-w-xs" />
          </SettingRow>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Network Settings</CardTitle>
          <CardDescription>Port ranges and network configuration for new engines.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <SettingRow label="Host Port Range" description="Engine host-side exposed ports." warning="Requires matching docker-compose publish range.">
            <Input value={draft.port_range_host} onChange={(e) => update('port_range_host', e.target.value)} className="max-w-sm" />
          </SettingRow>
          <SettingRow label="Ace HTTP Range" description="Internal Ace HTTP port range." warning="Applies to newly provisioned engines.">
            <Input value={draft.ace_http_range} onChange={(e) => update('ace_http_range', e.target.value)} className="max-w-sm" />
          </SettingRow>
          <SettingRow label="Ace HTTPS Range" description="Internal Ace HTTPS port range." warning="Applies to newly provisioned engines.">
            <Input value={draft.ace_https_range} onChange={(e) => update('ace_https_range', e.target.value)} className="max-w-sm" />
          </SettingRow>
          <SettingRow label="Docker Network" description="Override the auto-detected Docker network for engine provisioning.">
            <Input value={draft.docker_network} placeholder="e.g. acestream-net" onChange={(e) => update('docker_network', e.target.value)} className="max-w-sm" />
          </SettingRow>
        </CardContent>
      </Card>
    </div>
  )
}
