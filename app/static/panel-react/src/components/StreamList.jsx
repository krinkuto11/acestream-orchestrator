import React from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { PlayCircle, Download, Upload, Users } from 'lucide-react'
import { formatTime, formatBytes, formatBytesPerSecond } from '../utils/formatters'

function StreamCard({ stream, isSelected, onSelect }) {
  return (
    <Card 
      className={`mb-4 cursor-pointer transition-all hover:shadow-md ${
        isSelected ? 'ring-2 ring-primary' : ''
      }`}
      onClick={() => onSelect(stream)}
    >
      <CardContent className="pt-6">
        <div className="flex justify-between items-start mb-4">
          <div className="flex-1 overflow-hidden">
            <h3 className="font-semibold text-lg mb-1">
              {stream.id.slice(0, 16)}...
            </h3>
            <p className="text-sm text-muted-foreground truncate">
              {stream.content_key || 'N/A'}
            </p>
          </div>
          <Badge variant="success" className="ml-2 flex items-center gap-1">
            <PlayCircle className="h-3 w-3" />
            ACTIVE
          </Badge>
        </div>

        <div className="border-t pt-3 space-y-3">
          <div>
            <p className="text-xs text-muted-foreground">Engine</p>
            <p className="text-sm font-medium truncate">
              {stream.container_name || stream.container_id?.slice(0, 12) || 'N/A'}
            </p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Started</p>
            <p className="text-sm font-medium">{formatTime(stream.started_at)}</p>
          </div>
          
          <div className="grid grid-cols-3 gap-3">
            <div>
              <p className="text-xs text-muted-foreground flex items-center gap-1">
                <Download className="h-3 w-3" /> Download
              </p>
              <p className="text-sm font-semibold text-green-600 dark:text-green-400">
                {formatBytesPerSecond((stream.speed_down || 0) * 1024)}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground flex items-center gap-1">
                <Upload className="h-3 w-3" /> Upload
              </p>
              <p className="text-sm font-semibold text-red-600 dark:text-red-400">
                {formatBytesPerSecond((stream.speed_up || 0) * 1024)}
              </p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground flex items-center gap-1">
                <Users className="h-3 w-3" /> Peers
              </p>
              <p className="text-sm font-semibold text-blue-600 dark:text-blue-400">
                {stream.peers != null ? stream.peers : 'N/A'}
              </p>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-3">
            <div>
              <p className="text-xs text-muted-foreground">Total Downloaded</p>
              <p className="text-sm font-semibold">{formatBytes(stream.downloaded)}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Total Uploaded</p>
              <p className="text-sm font-semibold">{formatBytes(stream.uploaded)}</p>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}

function StreamList({ streams, selectedStream, onSelectStream }) {
  return (
    <div>
      <h2 className="text-2xl font-semibold mb-6">Active Streams ({streams.length})</h2>
      {streams.length === 0 ? (
        <Card>
          <CardContent className="pt-6 pb-6">
            <p className="text-muted-foreground">No active streams</p>
          </CardContent>
        </Card>
      ) : (
        streams.map((stream) => (
          <StreamCard
            key={stream.id}
            stream={stream}
            isSelected={selectedStream?.id === stream.id}
            onSelect={onSelectStream}
          />
        ))
      )}
    </div>
  )
}

export default StreamList
