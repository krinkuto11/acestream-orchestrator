import React, { useState, useEffect, useCallback } from 'react'
import EngineList from '@/components/EngineList'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Progress } from '@/components/ui/progress'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { RefreshCw, AlertCircle, CheckCircle, Save, Settings2 } from 'lucide-react'
import { toast } from 'sonner'
import { AdvancedEngineSettingsPage } from './AdvancedEngineSettingsPage'

// Platform-specific variants mapping
const VARIANT_OPTIONS = {
  amd64: [
    { value: 'krinkuto11-amd64', label: 'Krinkuto11 AMD64' },
    { value: 'jopsis-amd64', label: 'Jopsis AMD64' },
    { value: 'custom', label: 'Custom Variant' },
  ],
  arm32: [
    { value: 'jopsis-arm32', label: 'Jopsis ARM32' },
    { value: 'custom', label: 'Custom Variant' },
  ],
  arm64: [
    { value: 'jopsis-arm64', label: 'Jopsis ARM64' },
    { value: 'custom', label: 'Custom Variant' },
  ],
}

export function EnginesPage({ engines, onDeleteEngine, vpnStatus, orchUrl, apiKey, fetchJSON }) {
  const [reprovisionStatus, setReprovisionStatus] = useState(null)
  const [isReprovisioning, setIsReprovisioning] = useState(false)
  const [showSuccessMessage, setShowSuccessMessage] = useState(false)
  const [showErrorMessage, setShowErrorMessage] = useState(false)
  
  // Engine settings state
  const [engineSettings, setEngineSettings] = useState({
    min_replicas: 2,
    max_replicas: 6,
    auto_delete: true,
    engine_variant: 'krinkuto11-amd64',
    use_custom_variant: false,
    platform: 'amd64',
  })
  const [loadingSettings, setLoadingSettings] = useState(true)
  const [savingSettings, setSavingSettings] = useState(false)
  const [settingsChanged, setSettingsChanged] = useState(false)

  // Load engine settings
  const loadEngineSettings = useCallback(async () => {
    try {
      setLoadingSettings(true)
      const settings = await fetchJSON(`${orchUrl}/settings/engine`)
      setEngineSettings(settings)
      setSettingsChanged(false)
    } catch (err) {
      console.error('Failed to load engine settings:', err)
      toast.error(`Failed to load engine settings: ${err.message}`)
    } finally {
      setLoadingSettings(false)
    }
  }, [orchUrl, fetchJSON])

  useEffect(() => {
    loadEngineSettings()
  }, [loadEngineSettings])

  // Poll for reprovision status
  useEffect(() => {
    const checkReprovisionStatus = async () => {
      try {
        const status = await fetchJSON(`${orchUrl}/custom-variant/reprovision/status`)
        const wasReprovisioning = isReprovisioning
        
        setReprovisionStatus(status)
        setIsReprovisioning(status.in_progress)
        
        // When reprovisioning completes, show success/error message briefly
        if (wasReprovisioning && !status.in_progress) {
          if (status.status === 'success') {
            setShowSuccessMessage(true)
            // Auto-dismiss success message after 10 seconds
            setTimeout(() => setShowSuccessMessage(false), 10000)
          } else if (status.status === 'error') {
            setShowErrorMessage(true)
            // Auto-dismiss error message after 15 seconds
            setTimeout(() => setShowErrorMessage(false), 15000)
          }
        }
      } catch (err) {
        // Ignore errors
      }
    }

    // Initial check
    checkReprovisionStatus()

    // Poll every 2 seconds
    const interval = setInterval(checkReprovisionStatus, 2000)
    return () => clearInterval(interval)
  }, [orchUrl, fetchJSON, isReprovisioning])

  // Clear success/error message when component unmounts (user navigates away)
  useEffect(() => {
    return () => {
      // Clear the status when leaving the page
      setReprovisionStatus(null)
      setIsReprovisioning(false)
      setShowSuccessMessage(false)
      setShowErrorMessage(false)
    }
  }, [])

  // Handle settings change
  const handleSettingChange = (key, value) => {
    setEngineSettings(prev => ({ ...prev, [key]: value }))
    setSettingsChanged(true)
  }

  // Save engine settings
  const handleSaveSettings = useCallback(async () => {
    try {
      setSavingSettings(true)
      
      await fetchJSON(`${orchUrl}/settings/engine`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify(engineSettings)
      })
      
      toast.success('Engine settings saved successfully')
      setSettingsChanged(false)
    } catch (err) {
      toast.error(`Failed to save engine settings: ${err.message}`)
    } finally {
      setSavingSettings(false)
    }
  }, [orchUrl, fetchJSON, engineSettings])

  // Get available variants for current platform
  const availableVariants = VARIANT_OPTIONS[engineSettings.platform] || VARIANT_OPTIONS.amd64

  // Determine which variant is selected (custom or specific variant)
  const selectedVariant = engineSettings.use_custom_variant ? 'custom' : engineSettings.engine_variant

  // Handle variant change
  const handleVariantChange = (value) => {
    if (value === 'custom') {
      handleSettingChange('use_custom_variant', true)
    } else {
      handleSettingChange('use_custom_variant', false)
      handleSettingChange('engine_variant', value)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Engines</h1>
          <p className="text-muted-foreground mt-1">Manage and monitor AceStream engine containers</p>
        </div>
      </div>

      {/* Reprovisioning Progress */}
      {isReprovisioning && (
        <Card>
          <CardContent className="pt-6">
            <Alert>
              <RefreshCw className="h-4 w-4 animate-spin" />
              <AlertDescription>
                <div className="space-y-2">
                  <p className="font-medium">Reprovisioning in progress...</p>
                  <p className="text-sm text-muted-foreground">
                    {reprovisionStatus?.message || 'Engines are being reprovisioned with new settings.'}
                  </p>
                  {(() => {
                    // Calculate progress percentage based on current phase and counts
                    let progress = 0
                    if (reprovisionStatus) {
                      const { current_phase, engines_stopped = 0, total_engines = 0 } = reprovisionStatus
                      
                      if (current_phase === 'stopping' && total_engines > 0) {
                        // Stopping phase: 0-40% of progress
                        progress = Math.round((engines_stopped / total_engines) * 40)
                      } else if (current_phase === 'cleaning') {
                        // Cleaning phase: 40-50% of progress
                        progress = 45
                      } else if (current_phase === 'provisioning') {
                        // Provisioning phase: 50-100% of progress
                        progress = 50 + Math.round((engines_stopped / Math.max(total_engines, 1)) * 50)
                      } else if (current_phase === 'complete') {
                        progress = 100
                      }
                    }
                    return <Progress value={progress} className="w-full mt-2" />
                  })()}
                </div>
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      )}

      {/* Success message after reprovisioning */}
      {!isReprovisioning && showSuccessMessage && reprovisionStatus?.status === 'success' && (
        <Card>
          <CardContent className="pt-6">
            <Alert variant="default" className="border-green-500 bg-green-50 dark:bg-green-950">
              <CheckCircle className="h-4 w-4 text-green-600" />
              <AlertDescription className="text-green-800 dark:text-green-200">
                {reprovisionStatus.message}
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      )}

      {/* Error message after reprovisioning */}
      {!isReprovisioning && showErrorMessage && reprovisionStatus?.status === 'error' && (
        <Card>
          <CardContent className="pt-6">
            <Alert variant="destructive">
              <AlertCircle className="h-4 w-4" />
              <AlertDescription>
                {reprovisionStatus.message}
              </AlertDescription>
            </Alert>
          </CardContent>
        </Card>
      )}

      {/* Tabs for Engine Status and Configuration */}
      <Tabs defaultValue="status" className="w-full">
        <TabsList className="grid w-full grid-cols-2">
          <TabsTrigger value="status">Engine Status</TabsTrigger>
          <TabsTrigger value="configuration">Engine Configuration</TabsTrigger>
        </TabsList>

        {/* Engine Status Tab */}
        <TabsContent value="status" className="space-y-6 mt-6">
          <EngineList
            engines={engines}
            onDeleteEngine={onDeleteEngine}
            vpnStatus={vpnStatus}
            orchUrl={orchUrl}
          />
        </TabsContent>

        {/* Engine Configuration Tab */}
        <TabsContent value="configuration" className="space-y-6 mt-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Settings2 className="h-5 w-5" />
                Engine Configuration
              </CardTitle>
              <CardDescription>
                Configure engine variant, replica counts, and automatic cleanup settings
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Engine Variant Selector */}
              <div className="space-y-2">
                <Label htmlFor="engine-variant">Engine Variant</Label>
                <Select
                  value={selectedVariant}
                  onValueChange={handleVariantChange}
                  disabled={loadingSettings}
                >
                  <SelectTrigger id="engine-variant">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {availableVariants.map(variant => (
                      <SelectItem key={variant.value} value={variant.value}>
                        {variant.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-xs text-muted-foreground">
                  Select the AceStream engine variant to use. Custom variant allows full parameter configuration.
                  Detected platform: <strong>{engineSettings.platform}</strong>
                </p>
              </div>

              {/* MIN_REPLICAS */}
              <div className="space-y-2">
                <Label htmlFor="min-replicas">Minimum Replicas</Label>
                <Input
                  id="min-replicas"
                  type="number"
                  min="0"
                  max="50"
                  value={engineSettings.min_replicas}
                  onChange={(e) => handleSettingChange('min_replicas', parseInt(e.target.value) || 0)}
                  disabled={loadingSettings}
                />
                <p className="text-xs text-muted-foreground">
                  Minimum number of engine replicas to maintain (0-50, default: 2)
                </p>
              </div>

              {/* MAX_REPLICAS */}
              <div className="space-y-2">
                <Label htmlFor="max-replicas">Maximum Replicas</Label>
                <Input
                  id="max-replicas"
                  type="number"
                  min="1"
                  max="100"
                  value={engineSettings.max_replicas}
                  onChange={(e) => handleSettingChange('max_replicas', parseInt(e.target.value) || 1)}
                  disabled={loadingSettings}
                />
                <p className="text-xs text-muted-foreground">
                  Maximum number of engine replicas allowed (1-100, default: 6)
                </p>
              </div>

              {/* AUTO_DELETE */}
              <div className="flex items-center justify-between">
                <div className="space-y-1">
                  <Label htmlFor="auto-delete">Automatic Engine Cleanup</Label>
                  <p className="text-xs text-muted-foreground">
                    Automatically delete engines when they are stopped (default: true)
                  </p>
                </div>
                <Switch
                  id="auto-delete"
                  checked={engineSettings.auto_delete}
                  onCheckedChange={(checked) => handleSettingChange('auto_delete', checked)}
                  disabled={loadingSettings}
                />
              </div>

              {/* Save Settings Button */}
              <div className="flex justify-end gap-2 pt-4 border-t">
                <Button
                  onClick={handleSaveSettings}
                  disabled={savingSettings || loadingSettings || !settingsChanged}
                  className="flex items-center gap-2"
                >
                  <Save className="h-4 w-4" />
                  {savingSettings ? 'Saving...' : 'Save Settings'}
                </Button>
                {settingsChanged && (
                  <Button
                    variant="outline"
                    onClick={() => {
                      if (window.confirm('Are you sure you want to reprovision all engines with the new settings? This will interrupt all active streams.')) {
                        handleSaveSettings().then(() => {
                          // Trigger reprovision after saving
                          fetchJSON(`${orchUrl}/custom-variant/reprovision`, {
                            method: 'POST',
                          }).then(() => {
                            toast.success('Reprovisioning started')
                          }).catch(err => {
                            toast.error(`Failed to start reprovision: ${err.message}`)
                          })
                        })
                      }
                    }}
                    className="flex items-center gap-2"
                  >
                    <RefreshCw className="h-4 w-4" />
                    Save & Reprovision
                  </Button>
                )}
              </div>

              {!settingsChanged && (
                <Alert>
                  <AlertCircle className="h-4 w-4" />
                  <AlertDescription>
                    Changes to these settings require saving. Some changes may also require reprovisioning engines.
                  </AlertDescription>
                </Alert>
              )}
            </CardContent>
          </Card>

          {/* Advanced Engine Settings - Only show when custom variant is selected */}
          {engineSettings.use_custom_variant && (
            <div className="mt-6">
              <AdvancedEngineSettingsPage
                orchUrl={orchUrl}
                apiKey={apiKey}
                fetchJSON={fetchJSON}
              />
            </div>
          )}
        </TabsContent>
      </Tabs>
    </div>
  )
}
