import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { SettingsFormProvider, useSettingsForm } from '@/context/SettingsFormContext'
import { useNotifications } from '@/context/NotificationContext'
import { GeneralSettings } from './settings/GeneralSettings'
import { OrchestratorSettings } from './settings/OrchestratorSettings'
import { VPNSettings } from './settings/VPNSettings'
import { ProxySettings } from './settings/ProxySettings'
import { BackupSettings } from './settings/BackupSettings'

async function resolveAuthRequired(orchUrl) {
  try {
    const explicitStatus = await fetch(`${orchUrl}/api/v1/auth/status`)
    if (explicitStatus.ok) {
      const payload = await explicitStatus.json()
      return { required: Boolean(payload?.required), source: 'auth-status-endpoint' }
    }
  } catch { /* fall through */ }

  try {
    const probe = await fetch(`${orchUrl}/api/v1/engine-cache/stats`)
    if (probe.status === 401 || probe.status === 403) return { required: true, source: 'protected-endpoint-probe' }
    return { required: false, source: 'protected-endpoint-probe' }
  } catch {
    return { required: false, source: 'fallback-open' }
  }
}

const NAV_SECTIONS = [
  { id: 'general',      label: 'General' },
  { id: 'orchestrator', label: 'Orchestrator' },
  { id: 'vpn',          label: 'VPN' },
  { id: 'proxy',        label: 'Proxy' },
  { id: 'backup',       label: 'Backup' },
]

function SettingsNav({ active, onSelect }) {
  return (
    <div style={{ width: 180, flexShrink: 0, background: 'var(--bg-1)', border: '1px solid var(--line-soft)' }}>
      {NAV_SECTIONS.map((s, i) => {
        const isActive = s.id === active
        return (
          <div
            key={s.id}
            onClick={() => onSelect(s.id)}
            style={{
              padding: '9px 12px',
              borderLeft: `2px solid ${isActive ? 'var(--acc-green)' : 'transparent'}`,
              borderBottom: i < NAV_SECTIONS.length - 1 ? '1px solid var(--line-soft)' : 'none',
              background: isActive ? 'var(--bg-2)' : 'transparent',
              fontSize: 11,
              color: isActive ? 'var(--fg-0)' : 'var(--fg-1)',
              cursor: 'pointer',
              display: 'flex', alignItems: 'center', gap: 8,
              fontFamily: 'var(--font-mono)',
            }}
          >
            <span style={{ color: 'var(--fg-3)' }}>›</span>
            <span>{s.label}</span>
          </div>
        )
      })}
    </div>
  )
}

function SettingsPageInner({
  apiKey, setApiKey,
  refreshInterval, setRefreshInterval,
  maxEventsDisplay, setMaxEventsDisplay,
  orchUrl,
}) {
  const { addNotification } = useNotifications()
  const { authRequired, authChecked, dirtySections, globalDirty, globalSaving } = useSettingsForm()

  const [activeSection, setActiveSection] = useState('general')
  const [savingAll, setSavingAll] = useState(false)
  const [leaveWarningVisible, setLeaveWarningVisible] = useState(false)
  const [pendingSection, setPendingSection] = useState(null)

  const authBlockedSections = useMemo(() => {
    if (!authRequired || String(apiKey || '').trim()) return []
    return dirtySections.filter(s => s.requiresAuth)
  }, [authRequired, apiKey, dirtySections])

  const handleNavSelect = useCallback((sectionId) => {
    if (sectionId === activeSection) return
    if (globalDirty) {
      setPendingSection(sectionId)
      setLeaveWarningVisible(true)
      return
    }
    setActiveSection(sectionId)
  }, [activeSection, globalDirty])

  const confirmNavChange = useCallback(() => {
    if (pendingSection) setActiveSection(pendingSection)
    setPendingSection(null)
    setLeaveWarningVisible(false)
  }, [pendingSection])

  const discardAll = useCallback(() => {
    dirtySections.forEach(s => { if (typeof s.discard === 'function') s.discard() })
    addNotification('Discarded unsaved settings changes', 'info')
  }, [dirtySections, addNotification])

  const saveAll = useCallback(async () => {
    if (!dirtySections.length) return
    if (authBlockedSections.length) {
      const names = authBlockedSections.map(s => s.title).join(', ')
      addNotification(`Authentication required to save: ${names}`, 'warning')
      return
    }
    setSavingAll(true)
    try {
      for (const section of dirtySections) {
        if (typeof section.save === 'function') await section.save()
      }
      addNotification('Settings saved successfully', 'success')
    } catch (err) {
      addNotification(`Failed to save settings: ${err.message || String(err)}`, 'error')
    } finally {
      setSavingAll(false)
    }
  }, [addNotification, authBlockedSections, dirtySections])

  useEffect(() => {
    const handler = (e) => {
      if (!globalDirty) return
      e.preventDefault(); e.returnValue = ''
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [globalDirty])

  const sharedProps = { apiKey, orchUrl, authRequired }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingBottom: 112 }}>
      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div>
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 18, fontWeight: 600, color: 'var(--fg-0)', margin: 0 }}>Settings</h1>
          <div style={{ fontSize: 11, color: 'var(--fg-2)', marginTop: 2 }}>runtime · persisted to app/config/*.json</div>
          {authChecked && (
            <div style={{
              marginTop: 6,
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '3px 8px',
              background: 'var(--bg-2)', border: '1px solid var(--line)',
              fontSize: 10, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)',
            }}>
              <span style={{ color: authRequired ? 'var(--acc-amber)' : 'var(--acc-green)' }}>
                {authRequired ? '⚠' : '✓'}
              </span>
              {authRequired
                ? 'Auth enforced · API key required for protected settings'
                : 'Auth not enforced · settings save available without API key'}
            </div>
          )}
        </div>
        <div style={{ flex: 1 }}/>
        <button
          onClick={saveAll}
          disabled={!globalDirty || globalSaving || savingAll || authBlockedSections.length > 0}
          className="tag tag-green"
          style={{ cursor: !globalDirty || authBlockedSections.length > 0 ? 'not-allowed' : 'pointer', padding: '4px 14px', opacity: !globalDirty ? 0.4 : 1 }}
        >
          {savingAll ? '⟳ SAVING...' : '✓ SAVE'}
        </button>
        <button
          onClick={saveAll}
          disabled={!globalDirty || globalSaving || savingAll || authBlockedSections.length > 0}
          className="tag"
          style={{ cursor: !globalDirty || authBlockedSections.length > 0 ? 'not-allowed' : 'pointer', padding: '4px 14px', opacity: !globalDirty ? 0.4 : 1 }}
        >
          SAVE &amp; REPROVISION
        </button>
      </div>

      {/* Body: left nav + content */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <SettingsNav active={activeSection} onSelect={handleNavSelect}/>

        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: 12 }}>
          {activeSection === 'general' && (
            <GeneralSettings
              apiKey={apiKey} setApiKey={setApiKey}
              refreshInterval={refreshInterval} setRefreshInterval={setRefreshInterval}
              maxEventsDisplay={maxEventsDisplay} setMaxEventsDisplay={setMaxEventsDisplay}
              authRequired={authRequired}
            />
          )}
          {activeSection === 'orchestrator' && (
            <OrchestratorSettings apiKey={apiKey} orchUrl={orchUrl} authRequired={authRequired}/>
          )}
          {activeSection === 'vpn' && (
            <VPNSettings apiKey={apiKey} orchUrl={orchUrl} authRequired={authRequired}/>
          )}
          {activeSection === 'proxy' && (
            <ProxySettings apiKey={apiKey} orchUrl={orchUrl} authRequired={authRequired}/>
          )}
          {activeSection === 'backup' && (
            <BackupSettings apiKey={apiKey} orchUrl={orchUrl}/>
          )}
        </div>
      </div>

      {/* Sticky unsaved-changes bar */}
      {globalDirty && (
        <div style={{
          position: 'fixed', bottom: 16, left: '50%', transform: 'translateX(-50%)',
          zIndex: 50,
          width: 'min(900px, calc(100vw - 2rem))',
          background: 'var(--bg-1)',
          border: '1px solid var(--acc-amber-dim)',
          padding: '10px 14px',
          display: 'flex', alignItems: 'center', gap: 12,
          boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
        }}>
          <span className="dot pulse" style={{ color: 'var(--acc-amber)' }}/>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 11, color: 'var(--fg-0)', fontWeight: 600 }}>UNSAVED CHANGES</div>
            <div style={{ fontSize: 10, color: 'var(--fg-3)' }}>
              {dirtySections.length} section{dirtySections.length === 1 ? '' : 's'} pending
            </div>
          </div>
          {authBlockedSections.length > 0 && (
            <div style={{ fontSize: 10, color: 'var(--acc-red)' }}>
              API key required: {authBlockedSections.map(s => s.title).join(', ')}
            </div>
          )}
          <button
            onClick={discardAll}
            disabled={globalSaving || savingAll}
            className="tag"
            style={{ cursor: 'pointer', padding: '4px 12px', opacity: globalSaving || savingAll ? 0.5 : 1 }}
          >
            ↩ DISCARD
          </button>
          <button
            onClick={saveAll}
            disabled={globalSaving || savingAll || authBlockedSections.length > 0}
            className="tag tag-green"
            style={{
              cursor: authBlockedSections.length > 0 ? 'not-allowed' : 'pointer',
              padding: '4px 12px',
              opacity: globalSaving || savingAll || authBlockedSections.length > 0 ? 0.5 : 1,
            }}
          >
            {savingAll ? '⟳ SAVING...' : '✓ SAVE ALL'}
          </button>
        </div>
      )}

      {/* Leave-section warning */}
      {leaveWarningVisible && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 100,
          background: 'rgba(0,0,0,0.5)',
          display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          <div style={{
            background: 'var(--bg-1)', border: '1px solid var(--line)',
            padding: 24, width: 360,
          }}>
            <div style={{ fontFamily: 'var(--font-display)', fontSize: 14, fontWeight: 600, color: 'var(--fg-0)', marginBottom: 8 }}>
              Unsaved changes
            </div>
            <div style={{ fontSize: 11, color: 'var(--fg-2)', marginBottom: 20 }}>
              You have unsaved settings changes. Continue to the next section and keep editing, or stay here.
            </div>
            <div style={{ display: 'flex', gap: 8, justifyContent: 'flex-end' }}>
              <button
                onClick={() => setLeaveWarningVisible(false)}
                className="tag"
                style={{ cursor: 'pointer', padding: '6px 16px' }}
              >Stay Here</button>
              <button
                onClick={confirmNavChange}
                className="tag tag-green"
                style={{ cursor: 'pointer', padding: '6px 16px' }}
              >Continue</button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

export function SettingsPage(props) {
  const { orchUrl } = props
  const [authRequired, setAuthRequired] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    let mounted = true
    resolveAuthRequired(orchUrl).then(result => {
      if (!mounted) return
      setAuthRequired(result.required)
      setAuthChecked(true)
    })
    return () => { mounted = false }
  }, [orchUrl])

  return (
    <SettingsFormProvider authRequired={authRequired} authChecked={authChecked}>
      <SettingsPageInner {...props}/>
    </SettingsFormProvider>
  )
}
