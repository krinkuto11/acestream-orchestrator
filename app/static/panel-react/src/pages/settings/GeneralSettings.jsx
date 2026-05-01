import React, { useEffect, useMemo, useState } from 'react'
import { SettingRow } from '@/components/settings/SettingRow'
import { useSettingsForm } from '@/context/SettingsFormContext'

const inputStyle = {
  background: 'var(--bg-0)', border: '1px solid var(--line)', color: 'var(--fg-0)',
  padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 11, outline: 'none',
}
const selectStyle = { ...inputStyle, cursor: 'pointer', minWidth: 140 }

export function GeneralSettings({
  apiKey, setApiKey,
  refreshInterval, setRefreshInterval,
  maxEventsDisplay, setMaxEventsDisplay,
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

    registerSection(sectionId, { title: 'General', requiresAuth: false, save, discard })
    return () => unregisterSection(sectionId)
  }, [draft, initialState, registerSection, setApiKey, setMaxEventsDisplay, setRefreshInterval, setSectionDirty, unregisterSection])

  useEffect(() => {
    setSectionDirty(sectionId, dirty)
  }, [dirty, setSectionDirty])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-soft)' }}>
        <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)' }}>
          <span className="label">CONNECTION SETTINGS</span>
          <div style={{ fontSize: 10, color: 'var(--fg-2)', marginTop: 2 }}>Configure API access and authentication</div>
        </div>
        <div style={{ padding: '12px 14px' }}>
          <SettingRow label="API Key" description="Used for protected operations when server authentication is enabled." htmlFor="api-key">
            <input
              id="api-key"
              type="password"
              value={draft.apiKey}
              onChange={(e) => setDraft((prev) => ({ ...prev, apiKey: e.target.value }))}
              placeholder="Enter your API key"
              style={{ ...inputStyle, minWidth: 240 }}
            />
          </SettingRow>
          <SettingRow label=" " description="">
            <div style={{ fontSize: 10, color: authRequired ? 'var(--acc-amber)' : 'var(--fg-3)' }}>
              {authRequired
                ? '⚠ Auth enforced · API key required for protected endpoints'
                : '✓ Auth not enforced · API key is optional'}
            </div>
          </SettingRow>
        </div>
      </div>

      <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-soft)' }}>
        <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)' }}>
          <span className="label">DISPLAY SETTINGS</span>
          <div style={{ fontSize: 10, color: 'var(--fg-2)', marginTop: 2 }}>Customize dashboard refresh and display options</div>
        </div>
        <div style={{ padding: '12px 14px' }}>
          <SettingRow label="Auto Refresh Interval" description="How often dashboard data refreshes from the server." htmlFor="refresh-interval">
            <select
              id="refresh-interval"
              value={String(draft.refreshInterval)}
              onChange={(e) => setDraft((prev) => ({ ...prev, refreshInterval: Number(e.target.value) }))}
              style={selectStyle}
            >
              <option value="1000">1 second</option>
              <option value="2000">2 seconds</option>
              <option value="5000">5 seconds</option>
              <option value="10000">10 seconds</option>
              <option value="30000">30 seconds</option>
              <option value="60000">1 minute</option>
            </select>
          </SettingRow>
          <SettingRow label="Event Log Display Limit" description="Maximum number of events shown in the Events page list." htmlFor="max-events">
            <select
              id="max-events"
              value={String(draft.maxEventsDisplay)}
              onChange={(e) => setDraft((prev) => ({ ...prev, maxEventsDisplay: Number(e.target.value) }))}
              style={selectStyle}
            >
              <option value="50">50 events</option>
              <option value="100">100 events</option>
              <option value="200">200 events</option>
              <option value="500">500 events</option>
            </select>
          </SettingRow>
        </div>
      </div>
    </div>
  )
}
