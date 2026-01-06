import React from 'react'
import StreamsTable from '@/components/StreamsTable'

export function StreamsPage({ 
  streams, 
  orchUrl,
  apiKey,
  onStopStream,
  onDeleteEngine,
  debugMode
}) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Streams</h1>
          <p className="text-muted-foreground mt-1">Monitor active and historical streams</p>
        </div>
      </div>

      <StreamsTable
        streams={streams}
        orchUrl={orchUrl}
        apiKey={apiKey}
        onStopStream={onStopStream}
        onDeleteEngine={onDeleteEngine}
        debugMode={debugMode}
      />
    </div>
  )
}
