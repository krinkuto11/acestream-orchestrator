import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import { toast } from 'sonner'

export function SettingsPage({
  orchUrl,
  setOrchUrl,
  apiKey,
  setApiKey,
  refreshInterval,
  setRefreshInterval,
  maxEventsDisplay,
  setMaxEventsDisplay
}) {
  const [inactiveStreamSettings, setInactiveStreamSettings] = useState({
    livepos_threshold_s: 15,
    prebuf_threshold_s: 10,
    zero_speed_threshold_s: 10,
    low_speed_threshold_kb: 400,
    low_speed_threshold_s: 20
  })
  const [loadingSettings, setLoadingSettings] = useState(true)
  const [savingSettings, setSavingSettings] = useState(false)

  useEffect(() => {
    fetchInactiveStreamSettings()
  }, [orchUrl])

  const fetchInactiveStreamSettings = async () => {
    try {
      const response = await fetch(`${orchUrl}/inactive-stream-tracker/settings`)
      if (response.ok) {
        const data = await response.json()
        setInactiveStreamSettings(data)
      }
    } catch (err) {
      console.error('Failed to fetch inactive stream settings:', err)
    } finally {
      setLoadingSettings(false)
    }
  }

  const handleSaveInactiveStreamSettings = async () => {
    if (!apiKey) {
      toast.error('API Key is required to update settings')
      return
    }

    setSavingSettings(true)
    try {
      const response = await fetch(`${orchUrl}/inactive-stream-tracker/settings`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify(inactiveStreamSettings)
      })

      if (response.ok) {
        const data = await response.json()
        toast.success('Inactive stream tracker settings updated successfully')
        setInactiveStreamSettings(data.settings)
      } else {
        const error = await response.json()
        toast.error(`Failed to update settings: ${error.detail || 'Unknown error'}`)
      }
    } catch (err) {
      toast.error(`Failed to update settings: ${err.message}`)
    } finally {
      setSavingSettings(false)
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
          <p className="text-muted-foreground mt-1">Configure dashboard connection and preferences</p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Connection Settings</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="server-url">Server URL</Label>
            <Input
              id="server-url"
              value={orchUrl}
              onChange={(e) => setOrchUrl(e.target.value)}
              placeholder={typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8000'}
            />
            <p className="text-xs text-muted-foreground">
              The base URL of the AceStream Orchestrator API. Defaults to the current browser origin.
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="api-key">API Key</Label>
            <Input
              id="api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="Enter your API key"
            />
            <p className="text-xs text-muted-foreground">
              Required for protected endpoints (provisioning, deletion, etc.)
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Display Settings</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="refresh-interval">Auto Refresh Interval</Label>
            <Select 
              value={refreshInterval.toString()} 
              onValueChange={(val) => setRefreshInterval(Number(val))}
            >
              <SelectTrigger id="refresh-interval">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="2000">2 seconds</SelectItem>
                <SelectItem value="5000">5 seconds</SelectItem>
                <SelectItem value="10000">10 seconds</SelectItem>
                <SelectItem value="30000">30 seconds</SelectItem>
                <SelectItem value="60000">1 minute</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              How often the dashboard refreshes data from the server
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="max-events">Event Log Display Limit</Label>
            <Select 
              value={maxEventsDisplay.toString()} 
              onValueChange={(val) => setMaxEventsDisplay(Number(val))}
            >
              <SelectTrigger id="max-events">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="50">50 events</SelectItem>
                <SelectItem value="100">100 events</SelectItem>
                <SelectItem value="200">200 events</SelectItem>
                <SelectItem value="500">500 events</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              Maximum number of events to display in the Event Log page
            </p>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Inactive Stream Detection</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <p className="text-sm text-muted-foreground mb-4">
            Configure thresholds for detecting and automatically stopping inactive streams. 
            Click Save Settings to apply changes immediately (not persisted to environment variables).
          </p>
          
          {loadingSettings ? (
            <p className="text-sm text-muted-foreground">Loading settings...</p>
          ) : (
            <>
              <div className="space-y-2">
                <Label htmlFor="livepos-threshold">Live Position Unchanged Threshold (seconds)</Label>
                <Input
                  id="livepos-threshold"
                  type="number"
                  min="1"
                  value={inactiveStreamSettings.livepos_threshold_s}
                  onChange={(e) => setInactiveStreamSettings({
                    ...inactiveStreamSettings,
                    livepos_threshold_s: parseInt(e.target.value) || 15
                  })}
                />
                <p className="text-xs text-muted-foreground">
                  Stop stream if live position doesn't change for this duration (default: 15s)
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="prebuf-threshold">Prebuffering Status Threshold (seconds)</Label>
                <Input
                  id="prebuf-threshold"
                  type="number"
                  min="1"
                  value={inactiveStreamSettings.prebuf_threshold_s}
                  onChange={(e) => setInactiveStreamSettings({
                    ...inactiveStreamSettings,
                    prebuf_threshold_s: parseInt(e.target.value) || 10
                  })}
                />
                <p className="text-xs text-muted-foreground">
                  Stop stream if stuck in prebuffering for this duration (default: 10s)
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="zero-speed-threshold">Zero Speed Threshold (seconds)</Label>
                <Input
                  id="zero-speed-threshold"
                  type="number"
                  min="1"
                  value={inactiveStreamSettings.zero_speed_threshold_s}
                  onChange={(e) => setInactiveStreamSettings({
                    ...inactiveStreamSettings,
                    zero_speed_threshold_s: parseInt(e.target.value) || 10
                  })}
                />
                <p className="text-xs text-muted-foreground">
                  Stop stream if both download and upload speeds are 0 for this duration (default: 10s)
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="low-speed-kb">Low Speed Threshold (KB/s)</Label>
                <Input
                  id="low-speed-kb"
                  type="number"
                  min="1"
                  value={inactiveStreamSettings.low_speed_threshold_kb}
                  onChange={(e) => setInactiveStreamSettings({
                    ...inactiveStreamSettings,
                    low_speed_threshold_kb: parseInt(e.target.value) || 400
                  })}
                />
                <p className="text-xs text-muted-foreground">
                  Minimum download speed in KB/s to consider stream active (default: 400 KB/s)
                </p>
              </div>

              <div className="space-y-2">
                <Label htmlFor="low-speed-duration-threshold">Low Speed Duration Threshold (seconds)</Label>
                <Input
                  id="low-speed-duration-threshold"
                  type="number"
                  min="1"
                  value={inactiveStreamSettings.low_speed_threshold_s}
                  onChange={(e) => setInactiveStreamSettings({
                    ...inactiveStreamSettings,
                    low_speed_threshold_s: parseInt(e.target.value) || 20
                  })}
                />
                <p className="text-xs text-muted-foreground">
                  Stop stream if download speed stays below threshold for this duration (default: 20s)
                </p>
              </div>

              <Button 
                onClick={handleSaveInactiveStreamSettings}
                disabled={savingSettings || !apiKey}
                className="w-full"
              >
                {savingSettings ? 'Saving...' : 'Save Settings'}
              </Button>
              
              {!apiKey && (
                <p className="text-xs text-destructive">
                  API Key is required to save these settings
                </p>
              )}
            </>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
