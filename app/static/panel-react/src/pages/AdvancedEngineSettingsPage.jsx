import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Save, RefreshCw, AlertCircle, Info, Cpu, Upload, Download, Trash2, Plus, Edit, Pencil } from 'lucide-react'
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
  const [reprovisionStatus, setReprovisionStatus] = useState(null)
  const [config, setConfig] = useState(null)
  const [platform, setPlatform] = useState(null)
  const [vpnEnabled, setVpnEnabled] = useState(false)
  
  // Template management state
  const [templates, setTemplates] = useState([])
  const [activeTemplateId, setActiveTemplateId] = useState(null)
  const [selectedTemplateSlot, setSelectedTemplateSlot] = useState(null)
  const [templateName, setTemplateName] = useState('')
  const [showTemplateDialog, setShowTemplateDialog] = useState(false)
  const [showRenameDialog, setShowRenameDialog] = useState(false)
  const [renameSlotId, setRenameSlotId] = useState(null)
  const [renameValue, setRenameValue] = useState('')
  const [editingTemplateSlot, setEditingTemplateSlot] = useState(null)  // Track which template is being edited

  // Fetch templates
  const fetchTemplates = useCallback(async () => {
    try {
      const data = await fetchJSON(`${orchUrl}/custom-variant/templates`)
      setTemplates(data.templates)
      setActiveTemplateId(data.active_template_id)
    } catch (err) {
      console.error('Failed to load templates:', err)
    }
  }, [orchUrl, fetchJSON])

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

  // Poll reprovision status
  const checkReprovisionStatus = useCallback(async () => {
    try {
      const status = await fetchJSON(`${orchUrl}/custom-variant/reprovision/status`)
      setReprovisionStatus(status)
      
      // Update reprovisioning state based on status
      if (status.in_progress) {
        setReprovisioning(true)
      } else {
        setReprovisioning(false)
        
        // Show success or error toast based on final status
        if (status.status === 'success' && status.message) {
          // Only show toast if status changed recently (within last 5 seconds)
          const statusTime = new Date(status.timestamp)
          const now = new Date()
          if ((now - statusTime) < 5000) {
            toast.success(status.message)
          }
        } else if (status.status === 'error' && status.message) {
          // Only show toast if status changed recently
          const statusTime = new Date(status.timestamp)
          const now = new Date()
          if ((now - statusTime) < 5000) {
            toast.error(status.message)
          }
        }
      }
      
      return status.in_progress
    } catch (err) {
      // Ignore errors when checking status
      return false
    }
  }, [orchUrl, fetchJSON])

  useEffect(() => {
    fetchConfig()
    fetchTemplates()
  }, [fetchConfig, fetchTemplates])

  // Poll reprovision status every 2 seconds
  useEffect(() => {
    const interval = setInterval(async () => {
      await checkReprovisionStatus()
    }, 2000)
    
    // Initial check
    checkReprovisionStatus()
    
    return () => clearInterval(interval)
  }, [checkReprovisionStatus])

  // Update a parameter value
  const updateParameter = useCallback((paramName, field, value) => {
    setConfig(prev => ({
      ...prev,
      parameters: prev.parameters.map(p =>
        p.name === paramName ? { ...p, [field]: value } : p
      )
    }))
  }, [])

  // Save platform configuration (enabled/arm_version)
  const handleSavePlatformConfig = useCallback(async () => {
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
      
      toast.success('Platform configuration saved successfully')
    } catch (err) {
      toast.error(`Failed to save platform configuration: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }, [orchUrl, apiKey, config, fetchJSON])

  // Save template being edited
  const handleSaveTemplate = useCallback(async () => {
    if (!editingTemplateSlot) {
      toast.error('No template is being edited')
      return
    }

    try {
      setSaving(true)
      
      await fetchJSON(`${orchUrl}/custom-variant/templates/${editingTemplateSlot}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-KEY': apiKey
        },
        body: JSON.stringify({
          name: templateName,
          config: config
        })
      })
      
      toast.success(`Template ${editingTemplateSlot} saved successfully`)
      await fetchTemplates()
      setEditingTemplateSlot(null)
      setTemplateName('')
    } catch (err) {
      toast.error(`Failed to save template: ${err.message}`)
    } finally {
      setSaving(false)
    }
  }, [orchUrl, apiKey, config, templateName, editingTemplateSlot, fetchJSON, fetchTemplates])

  // Save configuration (kept for backward compatibility, now just calls platform save)
  const handleSave = useCallback(async () => {
    await handleSavePlatformConfig()
  }, [handleSavePlatformConfig])

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
      
      // Start polling for status
      await checkReprovisionStatus()
    } catch (err) {
      if (err.message.includes('409')) {
        toast.error('Reprovisioning operation already in progress')
      } else {
        toast.error(`Failed to reprovision: ${err.message}`)
      }
      setReprovisioning(false)
    }
  }, [orchUrl, apiKey, fetchJSON, checkReprovisionStatus])

  // Template management functions
  const handleSaveAsTemplate = useCallback(async (slotId, name) => {
    try {
      await fetchJSON(`${orchUrl}/custom-variant/templates/${slotId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-KEY': apiKey
        },
        body: JSON.stringify({
          name: name,
          config: config
        })
      })
      
      toast.success(`Template saved to slot ${slotId}`)
      await fetchTemplates()
      setShowTemplateDialog(false)
      setEditingTemplateSlot(null)  // Exit editing mode
      setTemplateName('')
    } catch (err) {
      toast.error(`Failed to save template: ${err.message}`)
    }
  }, [orchUrl, apiKey, config, fetchJSON, fetchTemplates])

  const handleNewTemplate = useCallback((slotId) => {
    // Enter editing mode for a new template
    setEditingTemplateSlot(slotId)
    setTemplateName(`Template ${slotId}`)
    toast.info(`Creating new template in slot ${slotId}. Configure parameters below and save.`)
  }, [])

  const handleLoadTemplate = useCallback(async (slotId) => {
    try {
      const response = await fetchJSON(`${orchUrl}/custom-variant/templates/${slotId}/activate`, {
        method: 'POST',
        headers: {
          'X-API-KEY': apiKey
        }
      })
      
      toast.success(response.message)
      await fetchConfig()
      await fetchTemplates()
    } catch (err) {
      toast.error(`Failed to load template: ${err.message}`)
    }
  }, [orchUrl, apiKey, fetchJSON, fetchConfig, fetchTemplates])

  const handleDeleteTemplate = useCallback(async (slotId) => {
    if (!window.confirm('Are you sure you want to delete this template?')) {
      return
    }

    try {
      await fetchJSON(`${orchUrl}/custom-variant/templates/${slotId}`, {
        method: 'DELETE',
        headers: {
          'X-API-KEY': apiKey
        }
      })
      
      toast.success(`Template ${slotId} deleted`)
      await fetchTemplates()
    } catch (err) {
      toast.error(`Failed to delete template: ${err.message}`)
    }
  }, [orchUrl, apiKey, fetchJSON, fetchTemplates])

  const handleExportTemplate = useCallback(async (slotId) => {
    try {
      const response = await fetch(`${orchUrl}/custom-variant/templates/${slotId}/export`)
      const blob = await response.blob()
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = `template_${slotId}.json`
      document.body.appendChild(a)
      a.click()
      window.URL.revokeObjectURL(url)
      document.body.removeChild(a)
      
      toast.success(`Template ${slotId} exported`)
    } catch (err) {
      toast.error(`Failed to export template: ${err.message}`)
    }
  }, [orchUrl])

  const handleImportTemplate = useCallback(async (slotId, fileContent) => {
    try {
      await fetchJSON(`${orchUrl}/custom-variant/templates/${slotId}/import`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-API-KEY': apiKey
        },
        body: JSON.stringify({
          json_data: fileContent
        })
      })
      
      toast.success(`Template imported to slot ${slotId}`)
      await fetchTemplates()
    } catch (err) {
      toast.error(`Failed to import template: ${err.message}`)
    }
  }, [orchUrl, apiKey, fetchJSON, fetchTemplates])

  const handleRenameTemplate = useCallback(async (slotId, newName) => {
    try {
      await fetchJSON(`${orchUrl}/custom-variant/templates/${slotId}/rename`, {
        method: 'PATCH',
        headers: {
          'Content-Type': 'application/json',
          'X-API-KEY': apiKey
        },
        body: JSON.stringify({
          name: newName
        })
      })
      
      toast.success(`Template renamed successfully`)
      await fetchTemplates()
      setShowRenameDialog(false)
    } catch (err) {
      toast.error(`Failed to rename template: ${err.message}`)
    }
  }, [orchUrl, apiKey, fetchJSON, fetchTemplates])

  const handleEditTemplate = useCallback(async (slotId) => {
    try {
      // Load the template configuration
      const template = await fetchJSON(`${orchUrl}/custom-variant/templates/${slotId}`)
      
      // Update the current config with the template's config
      setConfig(template.config)
      
      // Set editing mode with the template name
      setEditingTemplateSlot(slotId)
      setTemplateName(template.name)
      
      toast.success(`Loaded template ${slotId} for editing. Make your changes and save below.`)
    } catch (err) {
      toast.error(`Failed to load template for editing: ${err.message}`)
    }
  }, [orchUrl, fetchJSON])

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
                When VPN is enabled, leaving this off will let the provisioner use Gluetun's forwarded port automatically
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
            variant="secondary"
            onClick={handleReprovision}
            disabled={reprovisioning}
            className="flex items-center gap-2"
          >
            <RefreshCw className={reprovisioning ? "h-4 w-4 animate-spin" : "h-4 w-4"} />
            {reprovisioning ? 'Reprovisioning...' : 'Reprovision All Engines'}
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

          <div className="flex justify-end">
            <Button
              onClick={handleSavePlatformConfig}
              disabled={saving}
              className="flex items-center gap-2"
            >
              <Save className="h-4 w-4" />
              {saving ? 'Saving...' : 'Save Platform Settings'}
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Template Management */}
      <Card>
        <CardHeader>
          <CardTitle>Template Management</CardTitle>
          <CardDescription>
            Save and load custom variant configurations as templates (10 slots available)
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          {/* Template grid */}
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
            {templates.map((template) => (
              <div
                key={template.slot_id}
                className={`p-3 border rounded-lg ${
                  activeTemplateId === template.slot_id ? 'border-primary bg-primary/5' : ''
                } ${!template.exists ? 'border-dashed' : ''}`}
              >
                <div className="flex items-center justify-between mb-2">
                  <span className="text-xs font-medium">Slot {template.slot_id}</span>
                  {activeTemplateId === template.slot_id && (
                    <Badge variant="default" className="text-xs">Active</Badge>
                  )}
                </div>
                <p className="text-sm font-medium mb-2 truncate" title={template.name}>
                  {template.name}
                </p>
                <div className="flex gap-1">
                  {template.exists ? (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        className="flex-1 text-xs h-7"
                        onClick={() => handleLoadTemplate(template.slot_id)}
                      >
                        Load
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 w-7 p-0"
                        onClick={() => handleEditTemplate(template.slot_id)}
                        title="Edit"
                      >
                        <Edit className="h-3 w-3" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 w-7 p-0"
                        onClick={() => {
                          setRenameSlotId(template.slot_id)
                          setRenameValue(template.name)
                          setShowRenameDialog(true)
                        }}
                        title="Rename"
                      >
                        <Pencil className="h-3 w-3" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 w-7 p-0"
                        onClick={() => handleExportTemplate(template.slot_id)}
                        title="Export"
                      >
                        <Download className="h-3 w-3" />
                      </Button>
                      <Button
                        size="sm"
                        variant="ghost"
                        className="h-7 w-7 p-0 text-destructive"
                        onClick={() => handleDeleteTemplate(template.slot_id)}
                        title="Delete"
                      >
                        <Trash2 className="h-3 w-3" />
                      </Button>
                    </>
                  ) : (
                    <>
                      <Button
                        size="sm"
                        variant="outline"
                        className="flex-1 text-xs h-7"
                        onClick={() => handleNewTemplate(template.slot_id)}
                      >
                        <Plus className="h-3 w-3 mr-1" />
                        New Template
                      </Button>
                      <label htmlFor={`import-${template.slot_id}`}>
                        <Button
                          size="sm"
                          variant="outline"
                          className="flex-1 text-xs h-7"
                          title="Import Template"
                          onClick={() => document.getElementById(`import-${template.slot_id}`).click()}
                        >
                          <Upload className="h-3 w-3 mr-1" />
                          Import
                        </Button>
                      </label>
                      <input
                        id={`import-${template.slot_id}`}
                        type="file"
                        accept=".json"
                        className="hidden"
                        onChange={(e) => {
                          const file = e.target.files[0]
                          if (file) {
                            const reader = new FileReader()
                            reader.onload = (event) => {
                              handleImportTemplate(template.slot_id, event.target.result)
                            }
                            reader.readAsText(file)
                          }
                          e.target.value = null
                        }}
                      />
                    </>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Save as template dialog */}
          {showTemplateDialog && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
              <div className="bg-background p-6 rounded-lg max-w-md w-full mx-4 border">
                <h3 className="text-lg font-semibold mb-4">Save as Template</h3>
                <div className="space-y-4">
                  <div>
                    <Label htmlFor="template-name">Template Name</Label>
                    <Input
                      id="template-name"
                      value={templateName}
                      onChange={(e) => setTemplateName(e.target.value)}
                      placeholder="Enter template name"
                      className="mt-1"
                    />
                  </div>
                  <div className="flex gap-2 justify-end">
                    <Button
                      variant="outline"
                      onClick={() => setShowTemplateDialog(false)}
                    >
                      Cancel
                    </Button>
                    <Button
                      onClick={() => handleSaveAsTemplate(selectedTemplateSlot, templateName)}
                      disabled={!templateName.trim()}
                    >
                      Save
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Rename template dialog */}
          {showRenameDialog && (
            <div className="fixed inset-0 bg-black/50 flex items-center justify-center z-50">
              <div className="bg-background p-6 rounded-lg max-w-md w-full mx-4 border">
                <h3 className="text-lg font-semibold mb-4">Rename Template</h3>
                <div className="space-y-4">
                  <div>
                    <Label htmlFor="rename-template">New Template Name</Label>
                    <Input
                      id="rename-template"
                      value={renameValue}
                      onChange={(e) => setRenameValue(e.target.value)}
                      placeholder="Enter new name"
                      className="mt-1"
                    />
                  </div>
                  <div className="flex gap-2 justify-end">
                    <Button
                      variant="outline"
                      onClick={() => setShowRenameDialog(false)}
                    >
                      Cancel
                    </Button>
                    <Button
                      onClick={() => handleRenameTemplate(renameSlotId, renameValue)}
                      disabled={!renameValue.trim()}
                    >
                      Rename
                    </Button>
                  </div>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Parameters - Only shown when editing a template */}
      {editingTemplateSlot && (
        <Card>
          <CardHeader>
            <CardTitle>Engine Parameters</CardTitle>
            <CardDescription>
              Editing template for slot {editingTemplateSlot}. Configure parameters below and save.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-4">
            {/* Template name input */}
            <div className="space-y-2">
              <Label htmlFor="template-name">Template Name</Label>
              <Input
                id="template-name"
                value={templateName}
                onChange={(e) => setTemplateName(e.target.value)}
                placeholder="Enter template name"
              />
            </div>

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

            {/* Save template button */}
            <div className="flex justify-end gap-2 pt-4">
              <Button
                variant="outline"
                onClick={() => {
                  setEditingTemplateSlot(null)
                  setTemplateName('')
                  fetchConfig()  // Reset config to original
                }}
              >
                Cancel
              </Button>
              <Button
                onClick={handleSaveTemplate}
                disabled={saving || !templateName.trim()}
                className="flex items-center gap-2"
              >
                <Save className="h-4 w-4" />
                {saving ? 'Saving...' : 'Save Template'}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
