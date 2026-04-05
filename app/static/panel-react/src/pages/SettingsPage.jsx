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
import { LoopDetectionSettings } from './settings/LoopDetectionSettings'
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
    <div className="space-y-6 pb-28">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
          <p className="text-muted-foreground mt-1">Configure orchestrator behavior, networking, proxy pipelines, and diagnostics</p>
          {authChecked && (
            <p className="mt-2 inline-flex items-center gap-2 rounded-full border border-slate-300/80 bg-slate-100/70 px-3 py-1 text-xs text-slate-600 dark:border-slate-700 dark:bg-slate-900/80 dark:text-slate-300">
              <AlertCircle className="h-3.5 w-3.5" />
              {authRequired
                ? 'Server authentication is enforced for protected settings operations.'
                : 'Server authentication is not enforced. Settings save is available without an API key.'}
            </p>
          )}
        </div>
      </div>

      <Tabs value={activeTab} onValueChange={handleTabChange} className="space-y-6">
        <div className="flex flex-wrap items-center gap-3">
          <TabsList className="grid w-full grid-cols-6 border-slate-700 bg-slate-900/90 text-slate-300 lg:w-[960px]">
            <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="general">General</TabsTrigger>
            <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="orchestrator">Orchestrator</TabsTrigger>
            <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="vpn">VPN</TabsTrigger>
            <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="proxy">Proxy</TabsTrigger>
            <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="loop-detection">Loop Detection</TabsTrigger>
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

        <TabsContent value="loop-detection" forceMount className="space-y-6">
          <LoopDetectionSettings
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

      <div
        className={`fixed bottom-4 left-1/2 z-40 w-[min(960px,calc(100vw-2rem))] -translate-x-1/2 rounded-xl border border-slate-300 bg-white/95 p-3 shadow-xl backdrop-blur dark:border-slate-700 dark:bg-slate-950/95 transition-all ${globalDirty ? 'opacity-100' : 'pointer-events-none opacity-0'}`}
      >
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="text-sm text-slate-700 dark:text-slate-200">
            <p className="font-semibold">Unsaved changes</p>
            <p className="text-xs text-slate-500 dark:text-slate-400">
              {dirtySections.length} section{dirtySections.length === 1 ? '' : 's'} pending
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" onClick={discardAll} disabled={globalSaving || savingAll}>
              <Undo2 className="mr-2 h-4 w-4" />
              Discard
            </Button>
            <Button onClick={saveAll} disabled={globalSaving || savingAll || authBlockedSections.length > 0}>
              <Save className="mr-2 h-4 w-4" />
              {savingAll ? 'Saving...' : 'Save Changes'}
            </Button>
          </div>
        </div>
        {authBlockedSections.length > 0 && (
          <p className="mt-2 text-xs text-red-600 dark:text-red-400">
            API key required to save protected sections: {authBlockedSections.map((section) => section.title).join(', ')}
          </p>
        )}
      </div>

      <Dialog open={tabWarningOpen} onOpenChange={setTabWarningOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Unsaved changes detected</DialogTitle>
            <DialogDescription>
              You have unsaved settings changes. You can continue to the next tab and keep editing, or stay on the current tab.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter>
            <Button variant="outline" onClick={() => setTabWarningOpen(false)}>Stay Here</Button>
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
