import React from 'react'
import EngineList from '@/components/EngineList'

export function EnginesPage({ engines, onDeleteEngine, vpnStatus }) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Engines</h1>
          <p className="text-muted-foreground mt-1">Manage and monitor AceStream engine containers</p>
        </div>
      </div>

      <EngineList
        engines={engines}
        onDeleteEngine={onDeleteEngine}
        vpnStatus={vpnStatus}
      />
    </div>
  )
}
