import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Save, RefreshCw, AlertCircle, Info, Cpu } from 'lucide-react'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { toast } from 'sonner'

// Parameter metadata for UI rendering
const parameterCategories = {
  basic: {
    title: "Basic Settings",
    description: "Essential engine configuration",
    params: [
      { name: "--client-console", label: "Client Console Mode", description: "Run engine in console mode", unit: null },
      { name: "--bind-all", label: "Bind All Interfaces", description: "Listen on all network interfaces (0.0.0.0)", unit: null },
      { name: "--service-remote-access", label: "Service Remote Access", description: "Enable remote access to service", unit: null },
      { name: "--access-token", label: "Public Access Token", description: "Public access token for API", unit: null, placeholder: "acestream" },
      { name: "--service-access-token", label: "Service Access Token", description: "Administrative access token for service", unit: null, placeholder: "root" },
      { name: "--allow-user-config", label: "Allow User Config", description: "Allow per-user custom configuration", unit: null },
    ]
  },
  cache: {
    title: "Cache Configuration",
    description: "Configure memory and disk caching behavior",
    params: [
      { name: "--cache-dir", label: "Cache Directory", description: "Directory for storing cache", unit: null, placeholder: "~/.ACEStream" },
      { name: "--live-cache-type", label: "Live Cache Type", description: "Cache type for live streams", unit: null, options: ["memory", "disk", "hybrid"] },
      { name: "--live-cache-size", label: "Live Cache Size", description: "Live cache size", unit: "MB", divisor: 1048576 },
      { name: "--vod-cache-type", label: "VOD Cache Type", description: "Cache type for VOD", unit: null, options: ["memory", "disk", "hybrid"] },
      { name: "--vod-cache-size", label: "VOD Cache Size", description: "VOD cache size", unit: "MB", divisor: 1048576 },
      { name: "--vod-drop-max-age", label: "VOD Drop Max Age", description: "Maximum age before dropping VOD cache", unit: "seconds" },
      { name: "--max-file-size", label: "Max File Size", description: "Maximum file size to cache", unit: "GB", divisor: 1073741824 },
    ]
  },
  buffer: {
    title: "Buffer Settings",
    description: "Configure streaming buffer behavior",
    params: [
      { name: "--live-buffer", label: "Live Buffer", description: "Live stream buffer", unit: "seconds" },
      { name: "--vod-buffer", label: "VOD Buffer", description: "VOD buffer", unit: "seconds" },
      { name: "--refill-buffer-interval", label: "Refill Buffer Interval", description: "Buffer refill interval", unit: "seconds" },
    ]
  },
  connections: {
    title: "Connection Settings",
    description: "Configure P2P connections and bandwidth",
    params: [
      { name: "--max-connections", label: "Max Connections", description: "Maximum simultaneous connections", unit: "connections" },
      { name: "--max-peers", label: "Max Peers", description: "Maximum peers per torrent", unit: "peers" },
      { name: "--max-upload-slots", label: "Max Upload Slots", description: "Number of simultaneous upload slots", unit: "slots" },
      { name: "--auto-slots", label: "Auto Slots", description: "Automatic slot adjustment", unit: null, boolAsInt: true },
      { name: "--download-limit", label: "Download Limit", description: "Download speed limit (0=unlimited)", unit: "KB/s" },
      { name: "--upload-limit", label: "Upload Limit", description: "Upload speed limit (0=unlimited)", unit: "KB/s" },
      { name: "--port", label: "P2P Port", description: "Port for P2P connections", unit: null, vpnAware: true },
    ]
  },
  webrtc: {
    title: "WebRTC Settings",
    description: "Configure WebRTC connections",
    params: [
      { name: "--webrtc-allow-outgoing-connections", label: "Allow Outgoing WebRTC", description: "Allow outgoing WebRTC connections", unit: null, boolAsInt: true },
      { name: "--webrtc-allow-incoming-connections", label: "Allow Incoming WebRTC", description: "Allow incoming WebRTC connections", unit: null, boolAsInt: true },
    ]
  },
  advanced: {
    title: "Advanced Settings",
    description: "Advanced engine configuration options",
    params: [
      { name: "--stats-report-interval", label: "Stats Report Interval", description: "Interval for statistics reports", unit: "seconds" },
      { name: "--stats-report-peers", label: "Stats Report Peers", description: "Include peer info in statistics", unit: null },
      { name: "--slots-manager-use-cpu-limit", label: "CPU Limit for Slots", description: "Use CPU limit for slot management", unit: null, boolAsInt: true },
      { name: "--core-skip-have-before-playback-pos", label: "Skip Before Playback", description: "Skip downloaded pieces before playback position", unit: null, boolAsInt: true },
      { name: "--core-dlr-periodic-check-interval", label: "DLR Check Interval", description: "Periodic DLR check interval", unit: "seconds" },
      { name: "--check-live-pos-interval", label: "Live Position Check", description: "Interval for checking live position", unit: "seconds" },
    ]
  },
  logging: {
    title: "Logging Settings",
    description: "Configure logging behavior",
    params: [
      { name: "--log-debug", label: "Debug Level", description: "Debug level (0=normal, 1=verbose, 2=very verbose)", unit: null, options: [0, 1, 2] },
      { name: "--log-file", label: "Log File", description: "File for saving logs", unit: null, placeholder: "/var/log/acestream.log" },
      { name: "--log-max-size", label: "Max Log Size", description: "Max log size", unit: "MB", divisor: 1048576 },
      { name: "--log-backup-count", label: "Log Backup Count", description: "Number of backup log files", unit: "files" },
    ]
  }
}

export function AdvancedEngineSettingsPage({ orchUrl, apiKey, fetchJSON }) {
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [reprovisioning, setReprovisioning] = useState(false)
  const [config, setConfig] = useState(null)
  const [platform, setPlatform] = useState(null)
  const [vpnEnabled, setVpnEnabled] = useState(false)

  // Fetch current configuration
  const fetchConfig = useCallback(async () => {
    try {
      setLoading(true)
      const [configData, platformData, vpnStatus] = await Promise.all([
        fetchJSON(`${orchUrl}/custom-variant/config`),
        fetchJSON(`${orchUrl}/custom-variant/platform`),
        fetchJSON(`${orchUrl}/vpn/status`).catch(() => ({ enabled: false }))
      ])
      
      setConfig(configData)
      setPlatform(platformData)
      setVpnEnabled(vpnStatus.enabled || false)
    } catch (err) {
      toast.error(`Failed to load configuration: ${err.message}`)
    } finally {
      setLoading(false)
    }
  }, [orchUrl, fetchJSON])

  useEffect(() => {
    fetchConfig()
  }, [fetchConfig])

  // Update a parameter value
  const updateParameter = useCallback((paramName, field, value) => {
    setConfig(prev => ({
      ...prev,
      parameters: prev.parameters.map(p =>
        p.name === paramName ? { ...p, [field]: value } : p
      )
    }))
  }, [])

  // Save configuration
  const handleSave = useCallback(async () => {
    try {
      setSaving(true)
      
      await fetchJSON(`${orchUrl}/custom-variant/config`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-KEY': apiKey
        },
        body: JSON.stringify(config)
      })
      
      toast.success('Configuration saved successfully')
    } catch (err) {
      toast.error(`Failed to save configuration: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }, [orchUrl, apiKey, config, fetchJSON])

  // Reprovision all engines
  const handleReprovision = useCallback(async () => {
    if (!window.confirm(
      'This will delete ALL engines and recreate them with the new settings. ' +
      'All active streams will be interrupted. Are you sure?'
    )) {
      return
    }

    try {
      setReprovisioning(true)
      
      await fetchJSON(`${orchUrl}/custom-variant/reprovision`, {
        method: 'POST',
        headers: {
          'X-API-KEY': apiKey
        }
      })
      
      toast.success('Reprovisioning started. Engines will be recreated shortly.')
    } catch (err) {
      toast.error(`Failed to reprovision: ${err.message}`)
    } finally {
      setReprovisioning(false)
    }
  }, [orchUrl, apiKey, fetchJSON])

  // Render a parameter input based on its type
  const renderParameter = useCallback((paramMeta, param) => {
    if (!param) return null

    const { name, label, description, unit, options, boolAsInt, divisor, placeholder, vpnAware } = paramMeta
    const isFlag = param.type === 'flag'
    const isInt = param.type === 'int' || param.type === 'bytes'
    const isString = param.type === 'string' || param.type === 'path'

    // Show VPN warning for P2P port
    const showVpnWarning = vpnAware && vpnEnabled

    return (
      <div key={name} className="space-y-2 p-4 border rounded-lg">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <Label htmlFor={name} className="font-medium">{label}</Label>
              {unit && <Badge variant="outline" className="text-xs">{unit}</Badge>}
            </div>
            <p className="text-xs text-muted-foreground mt-1">{description}</p>
            {showVpnWarning && (
              <p className="text-xs text-amber-600 dark:text-amber-500 mt-1 flex items-center gap-1">
                <AlertCircle className="h-3 w-3" />
                When VPN is enabled, you can use Gluetun's forwarded port
              </p>
            )}
          </div>
          <Switch
            checked={param.enabled}
            onCheckedChange={(checked) => updateParameter(name, 'enabled', checked)}
          />
        </div>

        {param.enabled && (
          <div className="mt-2">
            {isFlag && (
              <div className="flex items-center space-x-2">
                <Switch
                  id={name}
                  checked={param.value}
                  onCheckedChange={(checked) => updateParameter(name, 'value', checked)}
                />
                <Label htmlFor={name} className="text-sm">
                  {param.value ? 'Enabled' : 'Disabled'}
                </Label>
              </div>
            )}

            {boolAsInt && (
              <div className="flex items-center space-x-2">
                <Switch
                  id={name}
                  checked={param.value === 1}
                  onCheckedChange={(checked) => updateParameter(name, 'value', checked ? 1 : 0)}
                />
                <Label htmlFor={name} className="text-sm">
                  {param.value === 1 ? 'Enabled' : 'Disabled'}
                </Label>
              </div>
            )}

            {options && !boolAsInt && (
              <Select
                value={String(param.value)}
                onValueChange={(val) => {
                  // Convert back to number if it was originally a number
                  const newVal = isInt ? parseInt(val) : val
                  updateParameter(name, 'value', newVal)
                }}
              >
                <SelectTrigger id={name}>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {options.map(opt => (
                    <SelectItem key={opt} value={String(opt)}>{String(opt)}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            )}

            {!isFlag && !boolAsInt && !options && (
              <Input
                id={name}
                type={isInt ? 'number' : 'text'}
                value={divisor ? Math.round(param.value / divisor) : param.value}
                onChange={(e) => {
                  let val = isInt ? parseInt(e.target.value) || 0 : e.target.value
                  if (divisor) val = val * divisor
                  updateParameter(name, 'value', val)
                }}
                placeholder={placeholder}
              />
            )}
          </div>
        )}
      </div>
    )
  }, [updateParameter, vpnEnabled])

  if (loading || !config || !platform) {
    return (
      <div className="flex items-center justify-center h-96">
        <div className="text-center">
          <RefreshCw className="h-8 w-8 animate-spin mx-auto mb-2 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">Loading configuration...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Advanced Engine Settings</h1>
          <p className="text-muted-foreground mt-1">
            Configure custom AceStream engine parameters
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            onClick={handleReprovision}
            disabled={reprovisioning || !config.enabled}
            className="flex items-center gap-2"
          >
            <RefreshCw className={reprovisioning ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            Reprovision All Engines
          </Button>
          <Button
            onClick={handleSave}
            disabled={saving}
            className="flex items-center gap-2"
          >
            <Save className="h-4 w-4" />
            {saving ? 'Saving...' : 'Save Settings'}
          </Button>
        </div>
      </div>

      {/* Platform Info */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Cpu className="h-5 w-5" />
            Platform Configuration
          </CardTitle>
          <CardDescription>
            System architecture and variant settings
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <Label>Detected Platform</Label>
              <p className="text-sm text-muted-foreground mt-1">
                Automatically detected system architecture
              </p>
            </div>
            <Badge variant="secondary" className="text-lg px-3 py-1">
              {platform.platform}
            </Badge>
          </div>

          <div className="flex items-center justify-between">
            <div>
              <Label>Enable Custom Engine Variant</Label>
              <p className="text-sm text-muted-foreground mt-1">
                Use custom parameters instead of ENV variant
              </p>
            </div>
            <Switch
              checked={config.enabled}
              onCheckedChange={(checked) => setConfig(prev => ({ ...prev, enabled: checked }))}
            />
          </div>

          {config.enabled && (platform.platform === 'arm32' || platform.platform === 'arm64') && (
            <div className="space-y-2">
              <Label>AceStream Engine Version</Label>
              <Select
                value={config.arm_version}
                onValueChange={(val) => setConfig(prev => ({ ...prev, arm_version: val }))}
              >
                <SelectTrigger>
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="3.2.13">v3.2.13</SelectItem>
                  <SelectItem value="3.2.14">v3.2.14</SelectItem>
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                Choose the AceStream engine version for ARM platform
              </p>
            </div>
          )}

          {!config.enabled && (
            <Alert>
              <Info className="h-4 w-4" />
              <AlertDescription>
                Custom variant is disabled. The system will use the ENGINE_VARIANT from environment variables.
                Enable it above to configure custom parameters.
              </AlertDescription>
            </Alert>
          )}
        </CardContent>
      </Card>

      {/* Parameters */}
      {config.enabled && (
        <Card>
          <CardHeader>
            <CardTitle>Engine Parameters</CardTitle>
            <CardDescription>
              Configure AceStream engine behavior. Toggle each parameter to enable/disable it.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Tabs defaultValue="basic" className="w-full">
              <TabsList className="grid w-full grid-cols-7">
                {Object.keys(parameterCategories).map(key => (
                  <TabsTrigger key={key} value={key}>
                    {parameterCategories[key].title.split(' ')[0]}
                  </TabsTrigger>
                ))}
              </TabsList>

              {Object.entries(parameterCategories).map(([key, category]) => (
                <TabsContent key={key} value={key} className="space-y-4">
                  <div className="mb-4">
                    <h3 className="font-semibold">{category.title}</h3>
                    <p className="text-sm text-muted-foreground">{category.description}</p>
                  </div>
                  <div className="space-y-3">
                    {category.params.map(paramMeta => {
                      const param = config.parameters.find(p => p.name === paramMeta.name)
                      return renderParameter(paramMeta, param)
                    })}
                  </div>
                </TabsContent>
              ))}
            </Tabs>
          </CardContent>
        </Card>
      )}

      {/* Warning about reprovisioning */}
      {config.enabled && (
        <Alert variant="warning">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>
            <strong>Note:</strong> Changes to these settings require reprovisioning all engines to take effect.
            Use the "Reprovision All Engines" button after saving to apply changes immediately.
          </AlertDescription>
        </Alert>
      )}
    </div>
  )
}
