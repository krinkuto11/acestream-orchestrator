import React, { useState, useEffect, useCallback } from 'react'
import EngineList from '@/components/EngineList'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'
import { AlertCircle, Save, Settings2 } from 'lucide-react'
import { useNotifications } from '@/context/NotificationContext'
import { EngineConfiguration } from '@/components/CustomEngineBlocks'
import { ManualEngineList } from '@/components/ManualEngineList'

export function EnginesPage({ engines, onDeleteEngine, vpnStatus, orchUrl, apiKey, fetchJSON }) {
  const { addNotification } = useNotifications()

  const [engineSettings, setEngineSettings] = useState({
    min_replicas: 2,
    max_replicas: 6,
    auto_delete: true,
    live_cache_type: 'memory',
    total_max_download_rate: 0,
    total_max_upload_rate: 0,
    buffer_time: 10,
    max_peers: 50,
    memory_limit: null,
    manual_mode: false,
    manual_engines: []
  })
  const [loadingSettings, setLoadingSettings] = useState(true)
  const [savingSettings, setSavingSettings] = useState(false)
  const [settingsChanged, setSettingsChanged] = useState(false)

  const [cacheStats, setCacheStats] = useState({ total_bytes: 0, volume_count: 0 })

  const loadEngineSettings = useCallback(async () => {
    try {
      setLoadingSettings(true)
      const settings = await fetchJSON(`${orchUrl}/api/v1/settings/engine`)
      setEngineSettings(settings)
      setSettingsChanged(false)
    } catch (err) {
      console.error('Failed to load engine settings:', err)
      addNotification(`Failed to load engine settings: ${err.message}`, 'error')
    } finally {
      setLoadingSettings(false)
    }
  }, [orchUrl, fetchJSON])

  const loadCacheStats = useCallback(async () => {
    try {
      const stats = await fetchJSON(`${orchUrl}/api/v1/engine-cache/stats`)
      setCacheStats(stats)
    } catch (err) {
      console.error('Failed to load cache stats:', err)
    }
  }, [orchUrl, fetchJSON])

  useEffect(() => {
    loadEngineSettings()
    loadCacheStats()
    const interval = setInterval(loadCacheStats, 30000)
    return () => clearInterval(interval)
  }, [loadEngineSettings, loadCacheStats])

  const formatBytes = (bytes) => {
    if (bytes === 0) return '0 Bytes'
    const k = 1024
    const sizes = ['Bytes', 'KB', 'MB', 'GB', 'TB']
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i]
  }

  const handleSettingChange = (key, value) => {
    setEngineSettings(prev => ({ ...prev, [key]: value }))
    setSettingsChanged(true)
  }

  const handleSaveSettings = useCallback(async () => {
    try {
      setSavingSettings(true)
      await fetchJSON(`${orchUrl}/api/v1/settings/engine`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify(engineSettings)
      })
      addNotification('Engine settings saved successfully', 'success')
      setSettingsChanged(false)
    } catch (err) {
      addNotification(`Failed to save engine settings: ${err.message}`, 'error')
    } finally {
      setSavingSettings(false)
    }
  }, [orchUrl, fetchJSON, engineSettings, apiKey])

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Engines</h1>
          <p className="text-muted-foreground mt-1">Manage and monitor AceStream engine containers</p>
        </div>
        {cacheStats.volume_count > 0 && (
          <div className="text-right">
            <div className="flex items-center gap-2 text-sm text-muted-foreground justify-end">
              <span>Engine Cache Size:</span>
              <span className="font-semibold text-foreground">{formatBytes(cacheStats.total_bytes)}</span>
            </div>
            <p className="text-[10px] opacity-60">across {cacheStats.volume_count} volumes</p>
          </div>
        )}
      </div>

      <Tabs defaultValue="status" className="w-full">
        <TabsList className="grid w-full grid-cols-2 border-slate-700 bg-slate-900/90 text-slate-300">
          <TabsTrigger
            value="status"
            className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50"
          >
            Engine Status
          </TabsTrigger>
          <TabsTrigger
            value="configuration"
            className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50"
          >
            Engine Settings
          </TabsTrigger>
        </TabsList>

        <TabsContent value="status" className="space-y-6 mt-6">
          <EngineList
            engines={engines}
            onDeleteEngine={onDeleteEngine}
            vpnStatus={vpnStatus}
          />
        </TabsContent>

        <TabsContent value="configuration" className="space-y-6 mt-6">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Settings2 className="h-5 w-5" />
                  Engine Settings
              </CardTitle>
              <CardDescription>
                Configure global engine customization, replica counts, and automatic cleanup settings
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Manual Mode Toggle */}
              <div className="flex items-center justify-between pb-4 border-b">
                <div className="space-y-0.5">
                  <Label className="text-base">Engine Pool Management</Label>
                  <p className="text-sm text-muted-foreground">
                    {engineSettings.manual_mode
                      ? "Manual Mode: Directly specify external AceStream engines"
                      : "Auto-Provisioned: Automatically manage Docker engine lifecycle"}
                  </p>
                </div>
                <div className="flex items-center gap-2">
                  <span className={`text-xs font-medium ${!engineSettings.manual_mode ? 'text-primary' : 'text-muted-foreground'}`}>Auto</span>
                  <Switch
                    checked={engineSettings.manual_mode}
                    onCheckedChange={(checked) => handleSettingChange('manual_mode', checked)}
                    disabled={loadingSettings}
                  />
                  <span className={`text-xs font-medium ${engineSettings.manual_mode ? 'text-primary' : 'text-muted-foreground'}`}>Manual</span>
                </div>
              </div>

              {!engineSettings.manual_mode ? (
                <>
                  <EngineConfiguration
                    engineSettings={engineSettings}
                    onSettingChange={handleSettingChange}
                    disabled={loadingSettings}
                  />

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
                      onChange={(e) => handleSettingChange('max_replicas', parseInt(e.target.value) || 6)}
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
                </>
              ) : (
                <div className="space-y-4">
                  <div className="rounded-lg border border-blue-200 bg-blue-50 p-4 dark:bg-blue-950/30 dark:border-blue-900">
                    <div className="flex items-start gap-3 text-blue-800 dark:text-blue-300">
                      <AlertCircle className="h-5 w-5 mt-0.5" />
                      <div>
                        <p className="font-semibold text-sm italic">Manual Mode Active</p>
                        <p className="text-xs mt-1">Docker provisioning is disabled. The orchestrator will only use the engines specified below. High availability and automated scaling are disabled.</p>
                      </div>
                    </div>
                  </div>

                  <ManualEngineList
                    engines={engineSettings.manual_engines || []}
                    onChange={(newList) => handleSettingChange('manual_engines', newList)}
                    disabled={loadingSettings}
                  />
                </div>
              )}

              {/* Save Settings Button */}
              <div className="flex justify-end pt-4 border-t">
                <Button
                  onClick={handleSaveSettings}
                  disabled={savingSettings || loadingSettings || !settingsChanged}
                  className="flex items-center gap-2"
                >
                  <Save className="h-4 w-4" />
                  {savingSettings ? 'Saving...' : 'Save Settings'}
                </Button>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  )
}
