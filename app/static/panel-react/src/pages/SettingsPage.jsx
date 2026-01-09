import React, { useState } from 'react'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { GeneralSettings } from './settings/GeneralSettings'
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
          <p className="text-muted-foreground mt-1">Configure dashboard and proxy settings</p>
        </div>
      </div>

      <Tabs defaultValue="general" className="space-y-6">
        <TabsList className="grid w-full grid-cols-4 lg:w-[800px]">
          <TabsTrigger value="general">General</TabsTrigger>
          <TabsTrigger value="proxy">Proxy</TabsTrigger>
          <TabsTrigger value="loop-detection">Loop Detection</TabsTrigger>
          <TabsTrigger value="backup">Backup & Restore</TabsTrigger>
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
