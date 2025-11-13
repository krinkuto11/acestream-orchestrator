import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { PlayCircle, Download, Upload, Users, ChevronDown, ChevronUp, StopCircle, Trash2, ExternalLink } from 'lucide-react'
import { formatTime, formatBytes, formatBytesPerSecond } from '../utils/formatters'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Line } from 'react-chartjs-2'
import {
  Chart as ChartJS,
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
} from 'chart.js'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
)

function StreamCard({ stream, orchUrl, apiKey, onStopStream, onDeleteEngine, debugMode }) {
  const [isOpen, setIsOpen] = useState(false)
  const [stats, setStats] = useState([])
  const [loading, setLoading] = useState(false)
  const [extendedStats, setExtendedStats] = useState(null)
  const [extendedStatsLoading, setExtendedStatsLoading] = useState(false)
  const [extendedStatsError, setExtendedStatsError] = useState(null)

  const fetchStats = useCallback(async () => {
    if (!stream || !isOpen) return
    
    setLoading(true)
    try {
      const since = new Date(Date.now() - 60 * 60 * 1000).toISOString()
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }
      
      const response = await fetch(
        `${orchUrl}/streams/${encodeURIComponent(stream.id)}/stats?since=${encodeURIComponent(since)}`,
        { headers }
      )
      
      if (response.ok) {
        const data = await response.json()
        setStats(data)
      }
    } catch (err) {
      console.error('Failed to fetch stats:', err)
    } finally {
      setLoading(false)
    }
  }, [stream, orchUrl, apiKey, isOpen])

  const fetchExtendedStats = useCallback(async () => {
    if (!stream || !isOpen) return
    
    setExtendedStatsLoading(true)
    setExtendedStatsError(null)
    try {
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }
      
      const response = await fetch(
        `${orchUrl}/streams/${encodeURIComponent(stream.id)}/extended-stats`,
        { headers }
      )
      
      if (response.ok) {
        const data = await response.json()
        setExtendedStats(data)
      } else {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }
    } catch (err) {
      console.error('Failed to fetch extended stats:', err)
      setExtendedStatsError(err.message || String(err))
    } finally {
      setExtendedStatsLoading(false)
    }
  }, [stream, orchUrl, apiKey, isOpen])

  useEffect(() => {
    if (isOpen) {
      fetchStats()
      fetchExtendedStats()
      const interval = setInterval(fetchStats, 10000)
      return () => clearInterval(interval)
    }
  }, [fetchStats, fetchExtendedStats, isOpen])

  const chartData = {
    labels: stats.map(s => new Date(s.ts).toLocaleTimeString()),
    datasets: [
      {
        label: 'Download (MB/s)',
        data: stats.map(s => (s.speed_down || 0) / 1024),
        borderColor: 'rgb(75, 192, 192)',
        backgroundColor: 'rgba(75, 192, 192, 0.2)',
        yAxisID: 'y',
      },
      {
        label: 'Upload (MB/s)',
        data: stats.map(s => (s.speed_up || 0) / 1024),
        borderColor: 'rgb(255, 99, 132)',
        backgroundColor: 'rgba(255, 99, 132, 0.2)',
        yAxisID: 'y',
      },
      {
        label: 'Peers',
        data: stats.map(s => s.peers || 0),
        borderColor: 'rgb(153, 102, 255)',
        backgroundColor: 'rgba(153, 102, 255, 0.2)',
        yAxisID: 'y1',
      },
    ],
  }

  const chartOptions = {
    responsive: true,
    maintainAspectRatio: false,
    interaction: {
      mode: 'index',
      intersect: false,
    },
    plugins: {
      legend: {
        position: 'top',
      },
      title: {
        display: true,
        text: 'Stream Statistics (Last Hour)',
      },
    },
    scales: {
      y: {
        type: 'linear',
        display: true,
        position: 'left',
        title: {
          display: true,
          text: 'Speed (MB/s)',
        },
      },
      y1: {
        type: 'linear',
        display: true,
        position: 'right',
        title: {
          display: true,
          text: 'Peers',
        },
        grid: {
          drawOnChartArea: false,
        },
      },
    },
  }

  return (
    <Card className="mb-4">
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

        <Collapsible open={isOpen} onOpenChange={setIsOpen} className="mt-4">
          <CollapsibleTrigger asChild>
            <Button variant="outline" className="w-full flex justify-between items-center">
              <span className="font-semibold">Stream Details</span>
              {isOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="mt-4 space-y-6">
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              <div>
                <p className="text-xs text-muted-foreground">Stream ID</p>
                <p className="text-sm font-medium break-all">{stream.id}</p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Engine</p>
                <p className="text-sm font-medium">
                  {stream.container_name || stream.container_id?.slice(0, 12) || 'N/A'}
                </p>
              </div>
              <div>
                <p className="text-xs text-muted-foreground">Started At</p>
                <p className="text-sm font-medium">{formatTime(stream.started_at)}</p>
              </div>
              
              {extendedStatsLoading && (
                <div className="col-span-2">
                  <p className="text-sm text-muted-foreground">Loading extended stats...</p>
                </div>
              )}
              
              {debugMode && extendedStatsError && (
                <div className="col-span-2">
                  <p className="text-sm text-red-600 dark:text-red-400">
                    Error loading extended stats: {extendedStatsError}
                  </p>
                </div>
              )}
              
              {extendedStats && (
                <>
                  {extendedStats.title && (
                    <div className="col-span-2">
                      <p className="text-xs text-muted-foreground">Title</p>
                      <p className="text-sm font-medium break-all">{extendedStats.title}</p>
                    </div>
                  )}
                  {extendedStats.content_type && (
                    <div>
                      <p className="text-xs text-muted-foreground">Content Type</p>
                      <p className="text-sm font-medium">{extendedStats.content_type}</p>
                    </div>
                  )}
                  {extendedStats.transport_type && (
                    <div>
                      <p className="text-xs text-muted-foreground">Transport Type</p>
                      <p className="text-sm font-medium">{extendedStats.transport_type}</p>
                    </div>
                  )}
                  {extendedStats.infohash && (
                    <div className="col-span-2">
                      <p className="text-xs text-muted-foreground">Infohash</p>
                      <p className="text-sm font-medium break-all">{extendedStats.infohash}</p>
                    </div>
                  )}
                  {extendedStats.is_live !== undefined && (
                    <div>
                      <p className="text-xs text-muted-foreground">Live Stream</p>
                      <Badge variant={extendedStats.is_live ? "success" : "secondary"}>
                        {extendedStats.is_live ? 'Yes' : 'No'}
                      </Badge>
                    </div>
                  )}
                  {extendedStats.mime && (
                    <div>
                      <p className="text-xs text-muted-foreground">MIME Type</p>
                      <p className="text-sm font-medium">{extendedStats.mime}</p>
                    </div>
                  )}
                  {extendedStats.categories && extendedStats.categories.length > 0 && (
                    <div className="col-span-2">
                      <p className="text-xs text-muted-foreground">Categories</p>
                      <div className="flex gap-2 flex-wrap mt-1">
                        {extendedStats.categories.map((cat, idx) => (
                          <Badge key={idx} variant="outline">{cat}</Badge>
                        ))}
                      </div>
                    </div>
                  )}
                  {extendedStats.suggested_filename && (
                    <div className="col-span-2">
                      <p className="text-xs text-muted-foreground">Suggested Filename</p>
                      <p className="text-sm font-medium break-all">{extendedStats.suggested_filename}</p>
                    </div>
                  )}
                  {extendedStats.media_files_count !== undefined && (
                    <div>
                      <p className="text-xs text-muted-foreground">Media Files Count</p>
                      <p className="text-sm font-medium">{extendedStats.media_files_count}</p>
                    </div>
                  )}
                </>
              )}
            </div>

            <div className="flex gap-4">
              <a 
                href={stream.stat_url} 
                target="_blank" 
                rel="noopener noreferrer"
                className="text-sm text-primary hover:underline flex items-center gap-1"
              >
                Statistics URL <ExternalLink className="h-3 w-3" />
              </a>
              <a 
                href={stream.command_url} 
                target="_blank" 
                rel="noopener noreferrer"
                className="text-sm text-primary hover:underline flex items-center gap-1"
              >
                Command URL <ExternalLink className="h-3 w-3" />
              </a>
            </div>

            <div className="border-t pt-4">
              <div className="h-80">
                {stats.length > 0 ? (
                  <Line data={chartData} options={chartOptions} />
                ) : (
                  <div className="flex items-center justify-center h-full">
                    <p className="text-muted-foreground">
                      {loading ? 'Loading statistics...' : 'No statistics available'}
                    </p>
                  </div>
                )}
              </div>
            </div>

            <div className="flex gap-3">
              <Button
                variant="destructive"
                onClick={(e) => {
                  e.stopPropagation()
                  onStopStream(stream.id, stream.container_id)
                }}
                className="flex items-center gap-2"
              >
                <StopCircle className="h-4 w-4" />
                Stop Stream
              </Button>
              <Button
                variant="outline"
                onClick={(e) => {
                  e.stopPropagation()
                  onDeleteEngine(stream.container_id)
                }}
                className="flex items-center gap-2 text-destructive hover:text-destructive"
              >
                <Trash2 className="h-4 w-4" />
                Delete Engine
              </Button>
            </div>
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  )
}

function StreamList({ streams, orchUrl, apiKey, onStopStream, onDeleteEngine, debugMode }) {
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
            orchUrl={orchUrl}
            apiKey={apiKey}
            onStopStream={onStopStream}
            onDeleteEngine={onDeleteEngine}
            debugMode={debugMode}
          />
        ))
      )}
    </div>
  )
}

export default StreamList
