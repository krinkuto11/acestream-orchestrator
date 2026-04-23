import React, { useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { SettingRow } from '@/components/settings/SettingRow'
import { useSettingsForm } from '@/context/SettingsFormContext'

export function GeneralSettings({
  apiKey,
  setApiKey,
  refreshInterval,
  setRefreshInterval,
  maxEventsDisplay,
  setMaxEventsDisplay,
  authRequired,
}) {
  const sectionId = 'general'
  const { registerSection, unregisterSection, setSectionDirty } = useSettingsForm()

  const [initialState, setInitialState] = useState({
    apiKey: apiKey || '',
    refreshInterval: Number(refreshInterval || 1000),
    maxEventsDisplay: Number(maxEventsDisplay || 100),
  })
  const [draft, setDraft] = useState(initialState)

  useEffect(() => {
    const next = {
      apiKey: apiKey || '',
      refreshInterval: Number(refreshInterval || 1000),
      maxEventsDisplay: Number(maxEventsDisplay || 100),
    }
    setInitialState(next)
    setDraft(next)
  }, [apiKey, refreshInterval, maxEventsDisplay])

  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(initialState),
    [draft, initialState],
  )

  useEffect(() => {
    const save = async () => {
      const normalized = {
        apiKey: String(draft.apiKey || '').trim(),
        refreshInterval: Number(draft.refreshInterval || 1000),
        maxEventsDisplay: Number(draft.maxEventsDisplay || 100),
      }
      setApiKey(normalized.apiKey)
      setRefreshInterval(normalized.refreshInterval)
      setMaxEventsDisplay(normalized.maxEventsDisplay)
      setInitialState(normalized)
      setSectionDirty(sectionId, false)
    }

    const discard = () => {
      setDraft(initialState)
      setSectionDirty(sectionId, false)
    }

    registerSection(sectionId, {
      title: 'General',
      requiresAuth: false,
      save,
      discard,
    })

    return () => unregisterSection(sectionId)
  }, [
    draft,
    initialState,
    registerSection,
    setApiKey,
    setMaxEventsDisplay,
    setRefreshInterval,
    setSectionDirty,
    unregisterSection,
  ])

  useEffect(() => {
    setSectionDirty(sectionId, dirty)
  }, [dirty, setSectionDirty])

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Connection Settings</CardTitle>
          <CardDescription>Configure API access and authentication</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <SettingRow
            label="API Key"
            description="Used for protected operations when server authentication is enabled."
            htmlFor="api-key"
          >
            <Input
              id="api-key"
              type="password"
              value={draft.apiKey}
              onChange={(e) => setDraft((prev) => ({ ...prev, apiKey: e.target.value }))}
              placeholder="Enter your API key"
              className="max-w-md"
            />
          </SettingRow>
          <p className="text-xs text-slate-500 dark:text-slate-400">
            {authRequired
              ? 'Server status: authentication is required for protected endpoints.'
              : 'Server status: authentication is currently disabled and API key is optional.'}
          </p>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Display Settings</CardTitle>
          <CardDescription>Customize dashboard refresh and display options</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <SettingRow
            label="Auto Refresh Interval"
            description="How often dashboard data refreshes from the server."
            htmlFor="refresh-interval"
          >
            <Select
              value={String(draft.refreshInterval)}
              onValueChange={(val) => setDraft((prev) => ({ ...prev, refreshInterval: Number(val) }))}
            >
              <SelectTrigger id="refresh-interval">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="1000">1 second</SelectItem>
                <SelectItem value="2000">2 seconds</SelectItem>
                <SelectItem value="5000">5 seconds</SelectItem>
                <SelectItem value="10000">10 seconds</SelectItem>
                <SelectItem value="30000">30 seconds</SelectItem>
                <SelectItem value="60000">1 minute</SelectItem>
              </SelectContent>
            </Select>
          </SettingRow>

          <SettingRow
            label="Event Log Display Limit"
            description="Maximum number of events shown in the Events page list."
            htmlFor="max-events"
          >
            <Select
              value={String(draft.maxEventsDisplay)}
              onValueChange={(val) => setDraft((prev) => ({ ...prev, maxEventsDisplay: Number(val) }))}
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
          </SettingRow>
        </CardContent>
      </Card>
    </div>
  )
}
