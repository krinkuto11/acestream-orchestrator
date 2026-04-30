import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { AlertCircle, Save, Undo2 } from 'lucide-react'
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
      return {
        required: Boolean(payload?.required),
        source: 'auth-status-endpoint',
      }
    }
  } catch {
    // Fall back to protected endpoint probing if status endpoint is unavailable.
  }

  try {
    const probe = await fetch(`${orchUrl}/api/v1/engine-cache/stats`)
    if (probe.status === 401 || probe.status === 403) {
      return { required: true, source: 'protected-endpoint-probe' }
    }
    return { required: false, source: 'protected-endpoint-probe' }
  } catch {
    return { required: false, source: 'fallback-open' }
  }
}

function SettingsPageInner({
  apiKey,
  setApiKey,
  refreshInterval,
  setRefreshInterval,
  maxEventsDisplay,
  setMaxEventsDisplay,
  orchUrl,
}) {
  const { addNotification } = useNotifications()
  const {
    authRequired,
    authChecked,
    dirtySections,
    globalDirty,
    globalSaving,
  } = useSettingsForm()

  const [activeTab, setActiveTab] = useState('general')
  const [pendingTab, setPendingTab] = useState(null)
  const [tabWarningOpen, setTabWarningOpen] = useState(false)
  const [savingAll, setSavingAll] = useState(false)

  const authBlockedSections = useMemo(() => {
    if (!authRequired || String(apiKey || '').trim()) return []
    return dirtySections.filter((section) => section.requiresAuth)
  }, [authRequired, apiKey, dirtySections])

  const handleTabChange = useCallback((nextTab) => {
    if (nextTab === activeTab) return
    if (globalDirty) {
      setPendingTab(nextTab)
      setTabWarningOpen(true)
      return
    }
    setActiveTab(nextTab)
  }, [activeTab, globalDirty])

  const proceedTabChange = useCallback(() => {
    if (pendingTab) {
      setActiveTab(pendingTab)
    }
    setPendingTab(null)
    setTabWarningOpen(false)
  }, [pendingTab])

  const discardAll = useCallback(() => {
    dirtySections.forEach((section) => {
      if (typeof section.discard === 'function') {
        section.discard()
      }
    })
    addNotification('Discarded unsaved settings changes', 'info')
  }, [addNotification, dirtySections])

  const saveAll = useCallback(async () => {
    if (!dirtySections.length) return

    if (authBlockedSections.length) {
      const names = authBlockedSections.map((section) => section.title).join(', ')
      addNotification(`Authentication required to save: ${names}`, 'warning')
      return
    }

    setSavingAll(true)
    try {
      for (const section of dirtySections) {
        if (typeof section.save === 'function') {
          await section.save()
        }
      }
      addNotification('Settings saved successfully', 'success')
    } catch (error) {
      addNotification(`Failed to save settings: ${error.message || String(error)}`, 'error')
    } finally {
      setSavingAll(false)
    }
  }, [addNotification, authBlockedSections, dirtySections])

  useEffect(() => {
    const onBeforeUnload = (event) => {
      if (!globalDirty) return
      event.preventDefault()
      event.returnValue = ''
    }

    window.addEventListener('beforeunload', onBeforeUnload)
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [globalDirty])

  useEffect(() => {
    const onDocumentClick = (event) => {
      if (!globalDirty) return

      const anchor = event.target instanceof Element ? event.target.closest('a[href]') : null
      if (!anchor) return

      const destination = anchor.getAttribute('href') || ''
      if (!destination || destination.includes('/settings')) return

      const shouldLeave = window.confirm('You have unsaved settings changes. Leave this page and lose changes?')
      if (!shouldLeave) {
        event.preventDefault()
        event.stopPropagation()
      }
    }

    document.addEventListener('click', onDocumentClick, true)
    return () => document.removeEventListener('click', onDocumentClick, true)
  }, [globalDirty])

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12, paddingBottom: 112 }}>
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div>
          <h1 style={{ fontFamily: 'var(--font-display)', fontSize: 18, fontWeight: 600, color: 'var(--fg-0)', margin: 0 }}>
            Settings
          </h1>
          <div style={{ fontSize: 11, color: 'var(--fg-2)', marginTop: 2 }}>
            runtime · persisted to app/config/*.json
          </div>
          {authChecked && (
            <div style={{
              marginTop: 6,
              display: 'inline-flex', alignItems: 'center', gap: 6,
              padding: '3px 8px',
              background: 'var(--bg-2)',
              border: '1px solid var(--line)',
              fontSize: 10,
              color: 'var(--fg-2)',
              fontFamily: 'var(--font-mono)',
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
      </div>

      <Tabs value={activeTab} onValueChange={handleTabChange} className="space-y-6">
        <div className="flex flex-wrap items-center gap-3">
          <TabsList className="grid w-full grid-cols-5 border-slate-700 bg-slate-900/90 text-slate-300 lg:w-[800px]">
            <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="general">General</TabsTrigger>
            <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="orchestrator">Orchestrator</TabsTrigger>
            <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="vpn">VPN</TabsTrigger>
            <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="proxy">Proxy</TabsTrigger>
            <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="backup">Backup</TabsTrigger>
          </TabsList>
        </div>

        <TabsContent value="general" forceMount className="space-y-6">
          <GeneralSettings
            apiKey={apiKey}
            setApiKey={setApiKey}
            refreshInterval={refreshInterval}
            setRefreshInterval={setRefreshInterval}
            maxEventsDisplay={maxEventsDisplay}
            setMaxEventsDisplay={setMaxEventsDisplay}
            authRequired={authRequired}
          />
        </TabsContent>

        <TabsContent value="orchestrator" forceMount className="space-y-6">
          <OrchestratorSettings
            apiKey={apiKey}
            orchUrl={orchUrl}
            authRequired={authRequired}
          />
        </TabsContent>

        <TabsContent value="vpn" forceMount className="space-y-6">
          <VPNSettings
            apiKey={apiKey}
            orchUrl={orchUrl}
            authRequired={authRequired}
          />
        </TabsContent>

        <TabsContent value="proxy" forceMount className="space-y-6">
          <ProxySettings
            apiKey={apiKey}
            orchUrl={orchUrl}
            authRequired={authRequired}
          />
        </TabsContent>



        <TabsContent value="backup" forceMount className="space-y-6">
          <BackupSettings
            apiKey={apiKey}
            orchUrl={orchUrl}
          />
        </TabsContent>
      </Tabs>

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
              cursor: globalSaving || savingAll || authBlockedSections.length > 0 ? 'not-allowed' : 'pointer',
              padding: '4px 12px',
              opacity: globalSaving || savingAll || authBlockedSections.length > 0 ? 0.5 : 1,
            }}
          >
            {savingAll ? '⟳ SAVING...' : '✓ SAVE ALL'}
          </button>
        </div>
      )}

      <Dialog open={tabWarningOpen} onOpenChange={setTabWarningOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Unsaved changes detected</DialogTitle>
            <DialogDescription>
              You have unsaved settings changes. You can continue to the next tab and keep editing, or stay on the current tab.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" className="text-foreground dark:text-slate-100" onClick={() => setTabWarningOpen(false)}>Stay Here</Button>
            <Button onClick={proceedTabChange}>Continue to Tab</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

export function SettingsPage(props) {
  const { orchUrl } = props
  const [authRequired, setAuthRequired] = useState(false)
  const [authChecked, setAuthChecked] = useState(false)

  useEffect(() => {
    let mounted = true

    const loadAuthStatus = async () => {
      const result = await resolveAuthRequired(orchUrl)
      if (!mounted) return
      setAuthRequired(result.required)
      setAuthChecked(true)
    }

    loadAuthStatus()

    return () => {
      mounted = false
    }
  }, [orchUrl])

  return (
    <SettingsFormProvider authRequired={authRequired} authChecked={authChecked}>
      <SettingsPageInner {...props} />
    </SettingsFormProvider>
  )
}
