import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, Info, Settings2 } from 'lucide-react'

const DEFAULTS = {
  monitor_interval_s: 10,
  engine_grace_period_s: 30,
  autoscale_interval_s: 30,
  startup_timeout_s: 25,
  idle_ttl_s: 600,
  collect_interval_s: 1,
  stats_history_max: 720,
  health_check_interval_s: 20,
  health_failure_threshold: 3,
  health_unhealthy_grace_period_s: 60,
  health_replacement_cooldown_s: 60,
  circuit_breaker_failure_threshold: 5,
  circuit_breaker_recovery_timeout_s: 300,
  circuit_breaker_replacement_threshold: 3,
  circuit_breaker_replacement_timeout_s: 180,
  max_concurrent_provisions: 5,
  min_provision_interval_s: 0.5,
  port_range_host: '19000-19999',
  ace_http_range: '40000-44999',
  ace_https_range: '45000-49999',
  debug_mode: false,
}

export function OrchestratorSettings({ apiKey, orchUrl }) {
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const [error, setError] = useState(null)
  const [showExpert, setShowExpert] = useState(false)

  // Basic settings
  const [engineGracePeriodS, setEngineGracePeriodS] = useState(DEFAULTS.engine_grace_period_s)
  const [startupTimeoutS, setStartupTimeoutS] = useState(DEFAULTS.startup_timeout_s)
  const [idleTtlS, setIdleTtlS] = useState(DEFAULTS.idle_ttl_s)
  const [autoscaleIntervalS, setAutoscaleIntervalS] = useState(DEFAULTS.autoscale_interval_s)
  const [debugMode, setDebugMode] = useState(DEFAULTS.debug_mode)

  // Expert settings
  const [monitorIntervalS, setMonitorIntervalS] = useState(DEFAULTS.monitor_interval_s)
  const [collectIntervalS, setCollectIntervalS] = useState(DEFAULTS.collect_interval_s)
  const [statsHistoryMax, setStatsHistoryMax] = useState(DEFAULTS.stats_history_max)
  const [healthCheckIntervalS, setHealthCheckIntervalS] = useState(DEFAULTS.health_check_interval_s)
  const [healthFailureThreshold, setHealthFailureThreshold] = useState(DEFAULTS.health_failure_threshold)
  const [healthUnhealthyGracePeriodS, setHealthUnhealthyGracePeriodS] = useState(DEFAULTS.health_unhealthy_grace_period_s)
  const [healthReplacementCooldownS, setHealthReplacementCooldownS] = useState(DEFAULTS.health_replacement_cooldown_s)
  const [cbFailureThreshold, setCbFailureThreshold] = useState(DEFAULTS.circuit_breaker_failure_threshold)
  const [cbRecoveryTimeoutS, setCbRecoveryTimeoutS] = useState(DEFAULTS.circuit_breaker_recovery_timeout_s)
  const [cbReplacementThreshold, setCbReplacementThreshold] = useState(DEFAULTS.circuit_breaker_replacement_threshold)
  const [cbReplacementTimeoutS, setCbReplacementTimeoutS] = useState(DEFAULTS.circuit_breaker_replacement_timeout_s)
  const [maxConcurrentProvisions, setMaxConcurrentProvisions] = useState(DEFAULTS.max_concurrent_provisions)
  const [minProvisionIntervalS, setMinProvisionIntervalS] = useState(DEFAULTS.min_provision_interval_s)
  const [portRangeHost, setPortRangeHost] = useState(DEFAULTS.port_range_host)
  const [aceHttpRange, setAceHttpRange] = useState(DEFAULTS.ace_http_range)
  const [aceHttpsRange, setAceHttpsRange] = useState(DEFAULTS.ace_https_range)

  useEffect(() => {
    fetchConfig()
  }, [orchUrl])

  const fetchConfig = async () => {
    try {
      const response = await fetch(`${orchUrl}/api/v1/settings/orchestrator`)
      if (response.ok) {
        const data = await response.json()
        setEngineGracePeriodS(data.engine_grace_period_s ?? DEFAULTS.engine_grace_period_s)
        setStartupTimeoutS(data.startup_timeout_s ?? DEFAULTS.startup_timeout_s)
        setIdleTtlS(data.idle_ttl_s ?? DEFAULTS.idle_ttl_s)
        setAutoscaleIntervalS(data.autoscale_interval_s ?? DEFAULTS.autoscale_interval_s)
        setDebugMode(data.debug_mode ?? DEFAULTS.debug_mode)
        setMonitorIntervalS(data.monitor_interval_s ?? DEFAULTS.monitor_interval_s)
        setCollectIntervalS(data.collect_interval_s ?? DEFAULTS.collect_interval_s)
        setStatsHistoryMax(data.stats_history_max ?? DEFAULTS.stats_history_max)
        setHealthCheckIntervalS(data.health_check_interval_s ?? DEFAULTS.health_check_interval_s)
        setHealthFailureThreshold(data.health_failure_threshold ?? DEFAULTS.health_failure_threshold)
        setHealthUnhealthyGracePeriodS(data.health_unhealthy_grace_period_s ?? DEFAULTS.health_unhealthy_grace_period_s)
        setHealthReplacementCooldownS(data.health_replacement_cooldown_s ?? DEFAULTS.health_replacement_cooldown_s)
        setCbFailureThreshold(data.circuit_breaker_failure_threshold ?? DEFAULTS.circuit_breaker_failure_threshold)
        setCbRecoveryTimeoutS(data.circuit_breaker_recovery_timeout_s ?? DEFAULTS.circuit_breaker_recovery_timeout_s)
        setCbReplacementThreshold(data.circuit_breaker_replacement_threshold ?? DEFAULTS.circuit_breaker_replacement_threshold)
        setCbReplacementTimeoutS(data.circuit_breaker_replacement_timeout_s ?? DEFAULTS.circuit_breaker_replacement_timeout_s)
        setMaxConcurrentProvisions(data.max_concurrent_provisions ?? DEFAULTS.max_concurrent_provisions)
        setMinProvisionIntervalS(data.min_provision_interval_s ?? DEFAULTS.min_provision_interval_s)
        setPortRangeHost(data.port_range_host ?? DEFAULTS.port_range_host)
        setAceHttpRange(data.ace_http_range ?? DEFAULTS.ace_http_range)
        setAceHttpsRange(data.ace_https_range ?? DEFAULTS.ace_https_range)
      }
    } catch (err) {
      console.error('Failed to fetch orchestrator config:', err)
    }
  }

  const saveConfig = async () => {
    if (!apiKey) {
      setError('API Key is required to update settings')
      return
    }
    setLoading(true)
    setMessage(null)
    setError(null)

    const payload = {
      engine_grace_period_s: engineGracePeriodS,
      startup_timeout_s: startupTimeoutS,
      idle_ttl_s: idleTtlS,
      autoscale_interval_s: autoscaleIntervalS,
      debug_mode: debugMode,
      monitor_interval_s: monitorIntervalS,
      collect_interval_s: collectIntervalS,
      stats_history_max: statsHistoryMax,
      health_check_interval_s: healthCheckIntervalS,
      health_failure_threshold: healthFailureThreshold,
      health_unhealthy_grace_period_s: healthUnhealthyGracePeriodS,
      health_replacement_cooldown_s: healthReplacementCooldownS,
      circuit_breaker_failure_threshold: cbFailureThreshold,
      circuit_breaker_recovery_timeout_s: cbRecoveryTimeoutS,
      circuit_breaker_replacement_threshold: cbReplacementThreshold,
      circuit_breaker_replacement_timeout_s: cbReplacementTimeoutS,
      max_concurrent_provisions: maxConcurrentProvisions,
      min_provision_interval_s: minProvisionIntervalS,
      port_range_host: portRangeHost,
      ace_http_range: aceHttpRange,
      ace_https_range: aceHttpsRange,
    }

    try {
      const response = await fetch(`${orchUrl}/api/v1/settings/orchestrator`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`,
        },
        body: JSON.stringify(payload),
      })
      if (response.ok) {
        const data = await response.json()
        setMessage(data.message || 'Orchestrator settings saved successfully')
        await fetchConfig()
      } else {
        const errorData = await response.json()
        setError(errorData.detail || 'Failed to update configuration')
      }
    } catch (err) {
      setError('Failed to save configuration: ' + err.message)
    } finally {
      setLoading(false)
    }
  }

  const FieldRow = ({ id, label, description, value, onChange, type = 'number', min, max, step }) => (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      <Input
        id={id}
        type={type}
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(type === 'number' ? (step ? parseFloat(e.target.value) : parseInt(e.target.value)) || 0 : e.target.value)}
        className="max-w-xs"
      />
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
    </div>
  )

  return (
    <div className="space-y-6">
      {/* Basic Settings */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Settings2 className="h-5 w-5" />
            Engine Lifecycle
          </CardTitle>
          <CardDescription>Core settings controlling how engines are started, idled, and cleaned up</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <FieldRow
              id="startup-timeout"
              label="Startup Timeout (seconds)"
              description="Max time to wait for an engine to become ready. Default: 25s"
              value={startupTimeoutS}
              onChange={setStartupTimeoutS}
              min={5} max={120}
            />
            <FieldRow
              id="idle-ttl"
              label="Idle Engine TTL (seconds)"
              description="How long an idle engine lives before being cleaned up. Default: 600s"
              value={idleTtlS}
              onChange={setIdleTtlS}
              min={30} max={7200}
            />
            <FieldRow
              id="engine-grace-period"
              label="Engine Grace Period (seconds)"
              description="Delay before stopping an engine after its last stream ends. Default: 30s"
              value={engineGracePeriodS}
              onChange={setEngineGracePeriodS}
              min={1} max={300}
            />
            <FieldRow
              id="autoscale-interval"
              label="Autoscale Check Interval (seconds)"
              description="How often to evaluate and adjust engine count. Default: 30s"
              value={autoscaleIntervalS}
              onChange={setAutoscaleIntervalS}
              min={5} max={300}
            />
          </div>

          <div className="flex items-center gap-3 pt-2">
            <Switch
              id="debug-mode"
              checked={debugMode}
              onCheckedChange={setDebugMode}
            />
            <div>
              <Label htmlFor="debug-mode">Debug Mode</Label>
              <p className="text-xs text-muted-foreground">Enables verbose logging. Restart required for full effect.</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Expert Toggle */}
      <button
        type="button"
        onClick={() => setShowExpert(!showExpert)}
        className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
      >
        {showExpert ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
        {showExpert ? 'Hide Expert Settings' : 'Show Expert Settings'}
      </button>

      {showExpert && (
        <>
          {/* Health Management */}
          <Card>
            <CardHeader>
              <CardTitle>Health Management</CardTitle>
              <CardDescription>How the orchestrator detects and replaces unhealthy engines</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <FieldRow
                  id="health-check-interval"
                  label="Health Check Interval (seconds)"
                  description="How often engine health is evaluated. Default: 20s"
                  value={healthCheckIntervalS}
                  onChange={setHealthCheckIntervalS}
                  min={5} max={120}
                />
                <FieldRow
                  id="health-failure-threshold"
                  label="Failure Threshold"
                  description="Consecutive failures before engine is marked unhealthy. Default: 3"
                  value={healthFailureThreshold}
                  onChange={setHealthFailureThreshold}
                  min={1} max={20}
                />
                <FieldRow
                  id="health-unhealthy-grace"
                  label="Unhealthy Grace Period (seconds)"
                  description="Time before replacing an unhealthy engine. Default: 60s"
                  value={healthUnhealthyGracePeriodS}
                  onChange={setHealthUnhealthyGracePeriodS}
                  min={10} max={600}
                />
                <FieldRow
                  id="health-replacement-cooldown"
                  label="Replacement Cooldown (seconds)"
                  description="Minimum time between engine replacements. Default: 60s"
                  value={healthReplacementCooldownS}
                  onChange={setHealthReplacementCooldownS}
                  min={10} max={600}
                />
              </div>
            </CardContent>
          </Card>

          {/* Circuit Breaker */}
          <Card>
            <CardHeader>
              <CardTitle>Circuit Breaker</CardTitle>
              <CardDescription>Protects against cascading failures during provisioning issues</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <FieldRow
                  id="cb-failure-threshold"
                  label="Failure Threshold"
                  description="Failures before opening the circuit breaker. Default: 5"
                  value={cbFailureThreshold}
                  onChange={setCbFailureThreshold}
                  min={1} max={20}
                />
                <FieldRow
                  id="cb-recovery-timeout"
                  label="Recovery Timeout (seconds)"
                  description="Time before attempting recovery. Default: 300s (5 min)"
                  value={cbRecoveryTimeoutS}
                  onChange={setCbRecoveryTimeoutS}
                  min={30} max={1800}
                />
                <FieldRow
                  id="cb-replacement-threshold"
                  label="Replacement Failure Threshold"
                  description="Failed replacements before circuit opens. Default: 3"
                  value={cbReplacementThreshold}
                  onChange={setCbReplacementThreshold}
                  min={1} max={10}
                />
                <FieldRow
                  id="cb-replacement-timeout"
                  label="Replacement Circuit Timeout (seconds)"
                  description="Timeout for replacement circuit. Default: 180s (3 min)"
                  value={cbReplacementTimeoutS}
                  onChange={setCbReplacementTimeoutS}
                  min={30} max={1800}
                />
              </div>
            </CardContent>
          </Card>

          {/* Performance & Stats */}
          <Card>
            <CardHeader>
              <CardTitle>Performance & Stats</CardTitle>
              <CardDescription>Provisioning rate limits and stats collection settings</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <FieldRow
                  id="monitor-interval"
                  label="Docker Monitor Interval (seconds)"
                  description="How often Docker containers are scanned. Default: 10s"
                  value={monitorIntervalS}
                  onChange={setMonitorIntervalS}
                  min={1} max={60}
                />
                <FieldRow
                  id="collect-interval"
                  label="Stats Collection Interval (seconds)"
                  description="How often stream stats are collected. Default: 1s"
                  value={collectIntervalS}
                  onChange={setCollectIntervalS}
                  min={1} max={30}
                />
                <FieldRow
                  id="stats-history-max"
                  label="Max Stats History Entries"
                  description="Maximum stat snapshots per stream. Default: 720"
                  value={statsHistoryMax}
                  onChange={setStatsHistoryMax}
                  min={10} max={10000}
                />
                <FieldRow
                  id="max-concurrent-provisions"
                  label="Max Concurrent Provisions"
                  description="Max engines provisioned simultaneously. Default: 5"
                  value={maxConcurrentProvisions}
                  onChange={setMaxConcurrentProvisions}
                  min={1} max={20}
                />
                <FieldRow
                  id="min-provision-interval"
                  label="Min Provision Interval (seconds)"
                  description="Minimum delay between engine provisioning. Default: 0.5s"
                  value={minProvisionIntervalS}
                  onChange={setMinProvisionIntervalS}
                  type="number"
                  min={0} max={10} step={0.1}
                />
              </div>
            </CardContent>
          </Card>

          {/* Port Ranges */}
          <Card>
            <CardHeader>
              <CardTitle>Port Ranges</CardTitle>
              <CardDescription>
                Dynamic port ranges used for engine containers.{' '}
                <span className="text-amber-600 dark:text-amber-400 font-medium">
                  ⚠ Requires Docker port mapping to be updated too — changes affect newly provisioned engines only.
                </span>
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                <div className="space-y-1">
                  <Label htmlFor="port-range-host">Host Port Range</Label>
                  <Input
                    id="port-range-host"
                    type="text"
                    value={portRangeHost}
                    onChange={(e) => setPortRangeHost(e.target.value)}
                    placeholder="19000-19999"
                  />
                  <p className="text-xs text-muted-foreground">Docker host ports. Default: 19000-19999</p>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="ace-http-range">AceStream HTTP Range</Label>
                  <Input
                    id="ace-http-range"
                    type="text"
                    value={aceHttpRange}
                    onChange={(e) => setAceHttpRange(e.target.value)}
                    placeholder="40000-44999"
                  />
                  <p className="text-xs text-muted-foreground">AceStream HTTP ports. Default: 40000-44999</p>
                </div>
                <div className="space-y-1">
                  <Label htmlFor="ace-https-range">AceStream HTTPS Range</Label>
                  <Input
                    id="ace-https-range"
                    type="text"
                    value={aceHttpsRange}
                    onChange={(e) => setAceHttpsRange(e.target.value)}
                    placeholder="45000-49999"
                  />
                  <p className="text-xs text-muted-foreground">AceStream HTTPS ports. Default: 45000-49999</p>
                </div>
              </div>
            </CardContent>
          </Card>
        </>
      )}

      {/* Save Button */}
      <div className="pt-2">
        <Button onClick={saveConfig} disabled={loading || !apiKey}>
          {loading ? 'Saving...' : 'Save Orchestrator Settings'}
        </Button>
        {!apiKey && (
          <p className="text-xs text-destructive mt-2">API Key is required to update settings</p>
        )}
      </div>

      {message && (
        <div className="flex items-center gap-2 p-3 bg-green-500/10 border border-green-500/30 rounded-md">
          <CheckCircle2 className="h-4 w-4 text-green-500" />
          <span className="text-sm text-green-600 dark:text-green-400">{message}</span>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 p-3 bg-destructive/10 border border-destructive rounded-md">
          <AlertCircle className="h-4 w-4 text-destructive" />
          <span className="text-sm text-destructive">{error}</span>
        </div>
      )}

      <div className="flex items-start gap-2 p-3 bg-blue-500/10 border border-blue-500/20 rounded-md">
        <Info className="h-4 w-4 text-blue-500 mt-0.5 flex-shrink-0" />
        <p className="text-xs text-blue-600 dark:text-blue-400">
          <strong>Note:</strong> Settings are applied immediately to the running instance and persisted to a JSON file.
          Interval-based services (health monitor, autoscaler) will use new values on their next cycle.
          Port range changes only affect newly provisioned engines.
        </p>
      </div>
    </div>
  )
}
