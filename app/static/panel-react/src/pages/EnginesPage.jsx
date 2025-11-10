import React from 'react'
import EngineList from '@/components/EngineList'

export function EnginesPage({ engines, onDeleteEngine, vpnStatus }) {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold">Engines</h1>
        <p className="text-muted-foreground">Manage and monitor AceStream engines</p>
      </div>

      <EngineList
        engines={engines}
        onDeleteEngine={onDeleteEngine}
        vpnStatus={vpnStatus}
      />
    </div>
  )
}
