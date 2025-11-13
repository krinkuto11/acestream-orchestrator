import React from 'react'
import StreamList from '@/components/StreamList'
import StreamDetail from '@/components/StreamDetail'

export function StreamsPage({ 
  streams, 
  selectedStream, 
  onSelectStream, 
  orchUrl,
  apiKey,
  onStopStream,
  onDeleteEngine 
}) {
  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Streams</h1>
          <p className="text-muted-foreground mt-1">Monitor active and historical streams</p>
        </div>
      </div>

      <StreamList
        streams={streams}
        selectedStream={selectedStream}
        onSelectStream={onSelectStream}
      />

      {selectedStream && (
        <StreamDetail
          stream={selectedStream}
          orchUrl={orchUrl}
          apiKey={apiKey}
          onStopStream={onStopStream}
          onDeleteEngine={onDeleteEngine}
          onClose={() => onSelectStream(null)}
        />
      )}
    </div>
  )
}
