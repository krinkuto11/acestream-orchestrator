import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { AlertCircle, CheckCircle2, ChevronDown, ChevronUp, Info, ShieldCheck, ShieldOff } from 'lucide-react'

const DEFAULTS = {
  enabled: false,
  vpn_mode: 'single',
  container_name: '',
  container_name_2: '',
  api_port: 8001,
  port_range_1: '',
  port_range_2: '',
  health_check_interval_s: 5,
  port_cache_ttl_s: 60,
  restart_engines_on_reconnect: true,
  unhealthy_restart_timeout_s: 60,
}

export function VPNSettings({ apiKey, orchUrl }) {
  const [loading, setLoading] = useState(false)
  const [message, setMessage] = useState(null)
  const [error, setError] = useState(null)
  const [showExpert, setShowExpert] = useState(false)

  // Basic settings
  const [enabled, setEnabled] = useState(DEFAULTS.enabled)
  const [vpnMode, setVpnMode] = useState(DEFAULTS.vpn_mode)
  const [containerName, setContainerName] = useState(DEFAULTS.container_name)
  const [containerName2, setContainerName2] = useState(DEFAULTS.container_name_2)
  const [apiPort, setApiPort] = useState(DEFAULTS.api_port)

  // Expert settings
  const [portRange1, setPortRange1] = useState(DEFAULTS.port_range_1)
  const [portRange2, setPortRange2] = useState(DEFAULTS.port_range_2)
  const [healthCheckIntervalS, setHealthCheckIntervalS] = useState(DEFAULTS.health_check_interval_s)
  const [portCacheTtlS, setPortCacheTtlS] = useState(DEFAULTS.port_cache_ttl_s)
  const [restartEnginesOnReconnect, setRestartEnginesOnReconnect] = useState(DEFAULTS.restart_engines_on_reconnect)
  const [unhealthyRestartTimeoutS, setUnhealthyRestartTimeoutS] = useState(DEFAULTS.unhealthy_restart_timeout_s)

  const isRedundant = vpnMode === 'redundant'

  useEffect(() => {
    fetchConfig()
  }, [orchUrl])

  const fetchConfig = async () => {
    try {
      const response = await fetch(`${orchUrl}/api/v1/settings/vpn`)
      if (response.ok) {
        const data = await response.json()
        setEnabled(data.enabled ?? DEFAULTS.enabled)
        setVpnMode(data.vpn_mode ?? DEFAULTS.vpn_mode)
        setContainerName(data.container_name ?? DEFAULTS.container_name)
        setContainerName2(data.container_name_2 ?? DEFAULTS.container_name_2)
        setApiPort(data.api_port ?? DEFAULTS.api_port)
        setPortRange1(data.port_range_1 ?? DEFAULTS.port_range_1)
        setPortRange2(data.port_range_2 ?? DEFAULTS.port_range_2)
        setHealthCheckIntervalS(data.health_check_interval_s ?? DEFAULTS.health_check_interval_s)
        setPortCacheTtlS(data.port_cache_ttl_s ?? DEFAULTS.port_cache_ttl_s)
        setRestartEnginesOnReconnect(data.restart_engines_on_reconnect ?? DEFAULTS.restart_engines_on_reconnect)
        setUnhealthyRestartTimeoutS(data.unhealthy_restart_timeout_s ?? DEFAULTS.unhealthy_restart_timeout_s)
      }
    } catch (err) {
      console.error('Failed to fetch VPN config:', err)
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
      enabled,
      vpn_mode: vpnMode,
      container_name: containerName,
      container_name_2: containerName2,
      api_port: apiPort,
      port_range_1: portRange1,
      port_range_2: portRange2,
      health_check_interval_s: healthCheckIntervalS,
      port_cache_ttl_s: portCacheTtlS,
      restart_engines_on_reconnect: restartEnginesOnReconnect,
      unhealthy_restart_timeout_s: unhealthyRestartTimeoutS,
    }

    try {
      const response = await fetch(`${orchUrl}/api/v1/settings/vpn`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`,
        },
        body: JSON.stringify(payload),
      })
      if (response.ok) {
        const data = await response.json()
        setMessage(data.message || 'VPN settings saved successfully')
        await fetchConfig()
      } else {
        const errorData = await response.json()
        setError(errorData.detail || 'Failed to update VPN configuration')
      }
    } catch (err) {
      setError('Failed to save VPN configuration: ' + err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Master VPN toggle */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {enabled
              ? <ShieldCheck className="h-5 w-5 text-green-500" />
              : <ShieldOff className="h-5 w-5 text-muted-foreground" />
            }
            VPN Integration (Gluetun)
          </CardTitle>
          <CardDescription>
            Connect to a Gluetun VPN container for routing engine traffic through a VPN.
            When disabled, engines connect directly without VPN.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <Switch
              id="vpn-enabled"
              checked={enabled}
              onCheckedChange={setEnabled}
            />
            <div>
              <Label htmlFor="vpn-enabled" className="text-base font-medium">
                {enabled ? 'VPN Enabled' : 'VPN Disabled'}
              </Label>
              <p className="text-xs text-muted-foreground">
                {enabled
                  ? 'Engines will route traffic through the configured Gluetun container.'
                  : 'Engines connect directly — no VPN routing.'}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* VPN connection config — only when enabled */}
      {enabled && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>VPN Connection</CardTitle>
              <CardDescription>Configure the Gluetun container and VPN mode</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <Label htmlFor="vpn-mode">VPN Mode</Label>
                <Select value={vpnMode} onValueChange={setVpnMode}>
                  <SelectTrigger id="vpn-mode" className="max-w-xs">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="single">Single — one Gluetun container</SelectItem>
                    <SelectItem value="redundant">Redundant — two Gluetun containers for high-availability</SelectItem>
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  Single: all engines use one VPN. Redundant: engines split between two VPNs.
                </p>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                <div className="space-y-1">
                  <Label htmlFor="container-name">
                    {isRedundant ? 'VPN 1 Container Name' : 'Container Name'}
                  </Label>
                  <Input
                    id="container-name"
                    type="text"
                    value={containerName}
                    onChange={(e) => setContainerName(e.target.value)}
                    placeholder="gluetun"
                  />
                  <p className="text-xs text-muted-foreground">Docker container name of the Gluetun VPN. Default: gluetun</p>
                </div>

                <div className="space-y-1">
                  <Label htmlFor="api-port">Gluetun HTTP API Port</Label>
                  <Input
                    id="api-port"
                    type="number"
                    min={1}
                    max={65535}
                    value={apiPort}
                    onChange={(e) => setApiPort(parseInt(e.target.value) || 8001)}
                    className="max-w-xs"
                  />
                  <p className="text-xs text-muted-foreground">
                    Must match <code>HTTP_CONTROL_SERVER_ADDRESS</code> in Gluetun. Default: 8001
                  </p>
                </div>

                {isRedundant && (
                  <div className="space-y-1 sm:col-span-2">
                    <Label htmlFor="container-name-2">VPN 2 Container Name</Label>
                    <Input
                      id="container-name-2"
                      type="text"
                      value={containerName2}
                      onChange={(e) => setContainerName2(e.target.value)}
                      placeholder="gluetun2"
                      className="max-w-xs"
                    />
                    <p className="text-xs text-muted-foreground">Docker container name of the second Gluetun VPN. Default: gluetun2</p>
                  </div>
                )}
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
              {/* Port Ranges for Redundant mode */}
              {isRedundant && (
                <Card>
                  <CardHeader>
                    <CardTitle>Redundant Mode Port Ranges</CardTitle>
                    <CardDescription>
                      Each VPN needs its own port range to route engines correctly.
                      These must match the Docker Compose port mappings.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                      <div className="space-y-1">
                        <Label htmlFor="port-range-1">VPN 1 Port Range</Label>
                        <Input
                          id="port-range-1"
                          type="text"
                          value={portRange1}
                          onChange={(e) => setPortRange1(e.target.value)}
                          placeholder="19000-19499"
                        />
                        <p className="text-xs text-muted-foreground">Port range for first VPN. Example: 19000-19499</p>
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="port-range-2">VPN 2 Port Range</Label>
                        <Input
                          id="port-range-2"
                          type="text"
                          value={portRange2}
                          onChange={(e) => setPortRange2(e.target.value)}
                          placeholder="19500-19999"
                        />
                        <p className="text-xs text-muted-foreground">Port range for second VPN. Example: 19500-19999</p>
                      </div>
                    </div>
                  </CardContent>
                </Card>
              )}

              {/* Health & Recovery */}
              <Card>
                <CardHeader>
                  <CardTitle>Health & Recovery Settings</CardTitle>
                  <CardDescription>How the orchestrator monitors VPN health and recovers from failures</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div className="space-y-1">
                      <Label htmlFor="health-check-interval">Health Check Interval (seconds)</Label>
                      <Input
                        id="health-check-interval"
                        type="number"
                        min={1} max={60}
                        value={healthCheckIntervalS}
                        onChange={(e) => setHealthCheckIntervalS(parseInt(e.target.value) || 5)}
                        className="max-w-xs"
                      />
                      <p className="text-xs text-muted-foreground">How often to check VPN health. Default: 5s</p>
                    </div>

                    <div className="space-y-1">
                      <Label htmlFor="port-cache-ttl">Port Cache TTL (seconds)</Label>
                      <Input
                        id="port-cache-ttl"
                        type="number"
                        min={1} max={300}
                        value={portCacheTtlS}
                        onChange={(e) => setPortCacheTtlS(parseInt(e.target.value) || 60)}
                        className="max-w-xs"
                      />
                      <p className="text-xs text-muted-foreground">How long to cache forwarded port info. Default: 60s</p>
                    </div>

                    <div className="space-y-1">
                      <Label htmlFor="unhealthy-restart-timeout">Unhealthy Restart Timeout (seconds)</Label>
                      <Input
                        id="unhealthy-restart-timeout"
                        type="number"
                        min={10} max={600}
                        value={unhealthyRestartTimeoutS}
                        onChange={(e) => setUnhealthyRestartTimeoutS(parseInt(e.target.value) || 60)}
                        className="max-w-xs"
                      />
                      <p className="text-xs text-muted-foreground">
                        Force-restart VPN container after being unhealthy for this long. Default: 60s
                      </p>
                    </div>

                    <div className="flex items-start gap-3 pt-1">
                      <Switch
                        id="restart-engines"
                        checked={restartEnginesOnReconnect}
                        onCheckedChange={setRestartEnginesOnReconnect}
                      />
                      <div>
                        <Label htmlFor="restart-engines">Restart Engines on VPN Reconnect</Label>
                        <p className="text-xs text-muted-foreground">
                          Restart engine containers when VPN reconnects to refresh their network routes. Default: on
                        </p>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </>
          )}
        </>
      )}

      {/* Save Button */}
      <div className="pt-2">
        <Button onClick={saveConfig} disabled={loading || !apiKey}>
          {loading ? 'Saving...' : 'Save VPN Settings'}
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
          <strong>Note:</strong> VPN settings are persisted and applied immediately. The Gluetun monitor will restart
          automatically to pick up changes. Existing engines are not restarted unless "Restart Engines on VPN Reconnect" is enabled.{' '}
          <strong>Container names</strong> must match the Docker Compose service names exactly.
        </p>
      </div>
    </div>
  )
}
