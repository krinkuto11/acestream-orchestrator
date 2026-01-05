import React, { useState, useEffect, useCallback } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { 
  PlayCircle, 
  Download, 
  Upload, 
  Users, 
  ChevronDown, 
  ChevronUp, 
  StopCircle, 
  Trash2, 
  ExternalLink,
  Clock,
  Activity
} from 'lucide-react'
import { formatTime, formatBytes, formatBytesPerSecond } from '../utils/formatters'
import StreamProgressBar from './StreamProgressBar'
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

function StreamTableRow({ stream, orchUrl, apiKey, onStopStream, onDeleteEngine, debugMode }) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [stats, setStats] = useState([])
  const [loading, setLoading] = useState(false)
  const [extendedStats, setExtendedStats] = useState(null)
  const [extendedStatsLoading, setExtendedStatsLoading] = useState(false)
  const [extendedStatsError, setExtendedStatsError] = useState(null)

  const isActive = stream.status === 'started'
  const isEnded = stream.status === 'ended'

  const fetchStats = useCallback(async () => {
    if (!stream || !isExpanded) return
    
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
  }, [stream, orchUrl, apiKey, isExpanded])

  const fetchExtendedStats = useCallback(async () => {
    if (!stream || !isExpanded) return
    
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
  }, [stream, orchUrl, apiKey, isExpanded])

  useEffect(() => {
    if (isExpanded && isActive) {
      fetchStats()
      fetchExtendedStats()
      const interval = setInterval(fetchStats, 10000)
      return () => clearInterval(interval)
    }
  }, [fetchStats, fetchExtendedStats, isExpanded, isActive])

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

  // Format livepos timestamp for display
  const formatLiveposTimestamp = (timestamp) => {
    if (!timestamp) return 'N/A'
    const date = new Date(parseInt(timestamp) * 1000)
    return date.toLocaleString()
  }

  // Calculate buffer duration in seconds
  const calculateBufferDuration = () => {
    if (!stream.livepos || !stream.livepos.live_last || !stream.livepos.pos) {
      return null
    }
    const lastPos = parseInt(stream.livepos.live_last)
    const currentPos = parseInt(stream.livepos.pos)
    return lastPos - currentPos
  }

  const bufferDuration = calculateBufferDuration()

  return (
    <>
      <TableRow 
        className={`cursor-pointer ${isEnded ? 'opacity-60' : ''}`}
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <TableCell className="w-[40px]">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0"
          >
            {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </TableCell>
        <TableCell>
          <Badge variant={isActive ? "success" : "secondary"} className="flex items-center gap-1 w-fit">
            {isActive ? <PlayCircle className="h-3 w-3" /> : <Activity className="h-3 w-3" />}
            {isActive ? 'ACTIVE' : 'ENDED'}
          </Badge>
        </TableCell>
        <TableCell className="font-medium">
          <div className="flex flex-col gap-1">
            <span className="text-sm truncate max-w-[200px]" title={stream.id}>
              {stream.id.slice(0, 16)}...
            </span>
            {isActive && stream.livepos && (
              <div className="w-48">
                <StreamProgressBar 
                  streamId={stream.id} 
                  orchUrl={orchUrl} 
                  apiKey={apiKey} 
                />
              </div>
            )}
          </div>
        </TableCell>
        <TableCell>
          <span className="text-sm truncate max-w-[150px] block" title={stream.container_name || stream.container_id}>
            {stream.container_name || stream.container_id?.slice(0, 12) || 'N/A'}
          </span>
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-1">
            <Clock className="h-3 w-3 text-muted-foreground" />
            <span className="text-sm">{formatTime(stream.started_at)}</span>
          </div>
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-1">
            <Download className="h-3 w-3 text-green-600 dark:text-green-400" />
            <span className="text-sm font-semibold text-green-600 dark:text-green-400">
              {formatBytesPerSecond((stream.speed_down || 0) * 1024)}
            </span>
          </div>
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-1">
            <Upload className="h-3 w-3 text-red-600 dark:text-red-400" />
            <span className="text-sm font-semibold text-red-600 dark:text-red-400">
              {formatBytesPerSecond((stream.speed_up || 0) * 1024)}
            </span>
          </div>
        </TableCell>
        <TableCell className="text-right">
          <div className="flex items-center justify-end gap-1">
            <Users className="h-3 w-3 text-blue-600 dark:text-blue-400" />
            <span className="text-sm font-semibold text-blue-600 dark:text-blue-400">
              {stream.peers != null ? stream.peers : 'N/A'}
            </span>
          </div>
        </TableCell>
        <TableCell className="text-right">
          <span className="text-sm">{formatBytes(stream.downloaded)}</span>
        </TableCell>
        <TableCell className="text-right">
          <span className="text-sm">{formatBytes(stream.uploaded)}</span>
        </TableCell>
      </TableRow>
      {isExpanded && (
        <TableRow>
          <TableCell colSpan={10} className="p-6 bg-muted/50">
            <div className="space-y-6">
              {/* Stream Details */}
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
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
                {isEnded && stream.ended_at && (
                  <div>
                    <p className="text-xs text-muted-foreground">Ended At</p>
                    <p className="text-sm font-medium">{formatTime(stream.ended_at)}</p>
                  </div>
                )}
                
                {/* LivePos Information */}
                {stream.livepos && (
                  <>
                    <div className="col-span-full">
                      <p className="text-sm font-semibold mb-2">Live Position Data</p>
                    </div>
                    {stream.livepos.pos && (
                      <div>
                        <p className="text-xs text-muted-foreground">Current Position</p>
                        <p className="text-sm font-medium">{formatLiveposTimestamp(stream.livepos.pos)}</p>
                      </div>
                    )}
                    {stream.livepos.live_first && (
                      <div>
                        <p className="text-xs text-muted-foreground">Live Start</p>
                        <p className="text-sm font-medium">{formatLiveposTimestamp(stream.livepos.live_first)}</p>
                      </div>
                    )}
                    {stream.livepos.live_last && (
                      <div>
                        <p className="text-xs text-muted-foreground">Live End</p>
                        <p className="text-sm font-medium">{formatLiveposTimestamp(stream.livepos.live_last)}</p>
                      </div>
                    )}
                    {stream.livepos.buffer_pieces && (
                      <div>
                        <p className="text-xs text-muted-foreground">Buffer Pieces</p>
                        <p className="text-sm font-medium">{stream.livepos.buffer_pieces}</p>
                      </div>
                    )}
                    {bufferDuration !== null && (
                      <div>
                        <p className="text-xs text-muted-foreground">Buffer Duration</p>
                        <p className="text-sm font-medium">{bufferDuration}s</p>
                      </div>
                    )}
                  </>
                )}
                
                {/* Extended Stats */}
                {extendedStats && (
                  <>
                    {extendedStats.title && (
                      <div className="col-span-full">
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
                      <div className="col-span-full">
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
                  </>
                )}
              </div>

              {/* Links */}
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

              {/* Chart */}
              {isActive && (
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
              )}

              {/* Actions */}
              {isActive && (
                <div className="flex gap-3 pt-4 border-t">
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
              )}
            </div>
          </TableCell>
        </TableRow>
      )}
    </>
  )
}

function StreamsTable({ streams, orchUrl, apiKey, onStopStream, onDeleteEngine, debugMode }) {
  // Separate active and ended streams
  const activeStreams = streams.filter(s => s.status === 'started')
  const endedStreams = streams.filter(s => s.status === 'ended')

  return (
    <div className="space-y-6">
      {/* Active Streams Section */}
      <div>
        <h2 className="text-2xl font-semibold mb-4">Active Streams ({activeStreams.length})</h2>
        {activeStreams.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            No active streams
          </div>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[40px]"></TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Stream ID</TableHead>
                  <TableHead>Engine</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead className="text-right">Download</TableHead>
                  <TableHead className="text-right">Upload</TableHead>
                  <TableHead className="text-right">Peers</TableHead>
                  <TableHead className="text-right">Downloaded</TableHead>
                  <TableHead className="text-right">Uploaded</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {activeStreams.map((stream) => (
                  <StreamTableRow
                    key={stream.id}
                    stream={stream}
                    orchUrl={orchUrl}
                    apiKey={apiKey}
                    onStopStream={onStopStream}
                    onDeleteEngine={onDeleteEngine}
                    debugMode={debugMode}
                  />
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {/* Ended Streams Section */}
      {endedStreams.length > 0 && (
        <div>
          <h2 className="text-2xl font-semibold mb-4">Ended Streams ({endedStreams.length})</h2>
          <p className="text-sm text-muted-foreground mb-4">
            These streams have ended. Reload the page to clear them.
          </p>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[40px]"></TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Stream ID</TableHead>
                  <TableHead>Engine</TableHead>
                  <TableHead>Started</TableHead>
                  <TableHead className="text-right">Download</TableHead>
                  <TableHead className="text-right">Upload</TableHead>
                  <TableHead className="text-right">Peers</TableHead>
                  <TableHead className="text-right">Downloaded</TableHead>
                  <TableHead className="text-right">Uploaded</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {endedStreams.map((stream) => (
                  <StreamTableRow
                    key={stream.id}
                    stream={stream}
                    orchUrl={orchUrl}
                    apiKey={apiKey}
                    onStopStream={onStopStream}
                    onDeleteEngine={onDeleteEngine}
                    debugMode={debugMode}
                  />
                ))}
              </TableBody>
            </Table>
          </div>
        </div>
      )}
    </div>
  )
}

export default StreamsTable
