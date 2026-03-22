import React, { useState } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { GeneralSettings } from './settings/GeneralSettings'
import { OrchestratorSettings } from './settings/OrchestratorSettings'
import { VPNSettings } from './settings/VPNSettings'
import { ProxySettings } from './settings/ProxySettings'
import { LoopDetectionSettings } from './settings/LoopDetectionSettings'
import { BackupSettings } from './settings/BackupSettings'

export function SettingsPage({
  apiKey,
  setApiKey,
  refreshInterval,
  setRefreshInterval,
  maxEventsDisplay,
  setMaxEventsDisplay,
  orchUrl
}) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Settings</h1>
          <p className="text-muted-foreground mt-1">Configure the orchestrator, VPN, proxy, and dashboard settings</p>
        </div>
      </div>

      <Tabs defaultValue="general" className="space-y-6">
        <TabsList className="grid w-full grid-cols-6 border-slate-700 bg-slate-900/90 text-slate-300 lg:w-[960px]">
          <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="general">General</TabsTrigger>
          <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="orchestrator">Orchestrator</TabsTrigger>
          <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="vpn">VPN</TabsTrigger>
          <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="proxy">Proxy</TabsTrigger>
          <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="loop-detection">Loop Detection</TabsTrigger>
          <TabsTrigger className="text-slate-300 hover:bg-slate-800/80 hover:text-slate-100 data-[state=active]:bg-slate-700 data-[state=active]:text-slate-50" value="backup">Backup</TabsTrigger>
        </TabsList>

        <TabsContent value="general" className="space-y-6">
          <GeneralSettings
            apiKey={apiKey}
            setApiKey={setApiKey}
            refreshInterval={refreshInterval}
            setRefreshInterval={setRefreshInterval}
            maxEventsDisplay={maxEventsDisplay}
            setMaxEventsDisplay={setMaxEventsDisplay}
          />
        </TabsContent>

        <TabsContent value="orchestrator" className="space-y-6">
          <OrchestratorSettings
            apiKey={apiKey}
            orchUrl={orchUrl}
          />
        </TabsContent>

        <TabsContent value="vpn" className="space-y-6">
          <VPNSettings
            apiKey={apiKey}
            orchUrl={orchUrl}
          />
        </TabsContent>

        <TabsContent value="proxy" className="space-y-6">
          <ProxySettings
            apiKey={apiKey}
            orchUrl={orchUrl}
          />
        </TabsContent>

        <TabsContent value="loop-detection" className="space-y-6">
          <LoopDetectionSettings
            apiKey={apiKey}
            orchUrl={orchUrl}
          />
        </TabsContent>

        <TabsContent value="backup" className="space-y-6">
          <BackupSettings
            apiKey={apiKey}
            orchUrl={orchUrl}
          />
        </TabsContent>
      </Tabs>
    </div>
  )
}
