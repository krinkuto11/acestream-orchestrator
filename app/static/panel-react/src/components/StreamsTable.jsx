import React, { useState, useEffect, useCallback } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
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
  Activity,
  ArrowUpDown,
  ArrowUp,
  ArrowDown
} from 'lucide-react'
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

// Constants for display
const TRUNCATED_STREAM_ID_LENGTH = 16
const TRUNCATED_CONTAINER_ID_LENGTH = 12
const TRUNCATED_CLIENT_ID_LENGTH = 16

// Timestamp validation constants (Unix timestamps in seconds)
const MIN_VALID_TIMESTAMP = 1577836800  // 2020-01-01 00:00:00 UTC
const MAX_VALID_TIMESTAMP = 2524608000  // 2050-01-01 00:00:00 UTC

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
)

function StreamTableRow({ stream, orchUrl, apiKey, onStopStream, onDeleteEngine, debugMode, showSpeedColumns = true, isSelected, onToggleSelect }) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [stats, setStats] = useState([])
  const [loading, setLoading] = useState(false)
  const [extendedStats, setExtendedStats] = useState(null)
  const [extendedStatsLoading, setExtendedStatsLoading] = useState(false)
  const [extendedStatsError, setExtendedStatsError] = useState(null)
  const [clients, setClients] = useState([])
  const [clientsLoading, setClientsLoading] = useState(false)

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

  const fetchClients = useCallback(async () => {
    if (!stream || !isExpanded || !stream.key) return
    
    setClientsLoading(true)
    try {
      const response = await fetch(
        `${orchUrl}/proxy/streams/${encodeURIComponent(stream.key)}/clients`
      )
      
      if (response.ok) {
        const data = await response.json()
        setClients(data.clients || [])
      } else {
        setClients([])
      }
    } catch (err) {
      console.error('Failed to fetch clients:', err)
      setClients([])
    } finally {
      setClientsLoading(false)
    }
  }, [stream, orchUrl, isExpanded])

  const refreshData = useCallback(() => {
    fetchStats()
    fetchClients()
  }, [fetchStats, fetchClients])

  useEffect(() => {
    if (isExpanded && isActive) {
      fetchStats()
      fetchExtendedStats()
      fetchClients()
      const interval = setInterval(refreshData, 10000)
      return () => clearInterval(interval)
    }
  }, [refreshData, fetchStats, fetchExtendedStats, fetchClients, isExpanded, isActive])

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
  // AceStream API returns Unix timestamps (seconds since epoch)
  const formatLiveposTimestamp = (timestamp) => {
    if (!timestamp) return 'N/A'
    
    try {
      const numTimestamp = parseInt(timestamp)
      
      // Validate timestamp is reasonable (between 2020 and 2050)
      if (isNaN(numTimestamp) || numTimestamp < MIN_VALID_TIMESTAMP || numTimestamp > MAX_VALID_TIMESTAMP) {
        console.warn('Invalid livepos timestamp:', timestamp)
        return 'Invalid'
      }
      
      // AceStream uses Unix timestamps in seconds, convert to milliseconds
      const date = new Date(numTimestamp * 1000)
      
      // Additional validation: check if date is valid
      if (isNaN(date.getTime())) {
        return 'Invalid'
      }
      
      return date.toLocaleString()
    } catch (err) {
      console.error('Error formatting livepos timestamp:', err)
      return 'Error'
    }
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
      <TableRow>
        {showSpeedColumns && (
          <TableCell className="w-[40px]">
            <Checkbox
              checked={isSelected}
              onCheckedChange={onToggleSelect}
              aria-label="Select stream"
            />
          </TableCell>
        )}
        <TableCell className="w-[40px]">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0 border border-white/20 hover:bg-white/10"
            onClick={() => setIsExpanded(!isExpanded)}
          >
            {isExpanded ? <ChevronUp className="h-4 w-4 text-white" /> : <ChevronDown className="h-4 w-4 text-white" />}
          </Button>
        </TableCell>
        <TableCell>
          <Badge variant={isActive ? "success" : "secondary"} className="flex items-center gap-1 w-fit">
            {isActive ? <PlayCircle className="h-3 w-3" /> : <Activity className="h-3 w-3" />}
            <span className="text-white">{isActive ? 'ACTIVE' : 'ENDED'}</span>
          </Badge>
        </TableCell>
        <TableCell className="font-medium">
          <div className="flex flex-col gap-1">
            <span className="text-sm text-white truncate max-w-[200px]" title={stream.id}>
              {stream.id.slice(0, TRUNCATED_STREAM_ID_LENGTH)}...
            </span>
            {isActive && bufferDuration !== null && (
              <span className="text-xs text-muted-foreground">
                {bufferDuration}s behind live
              </span>
            )}
          </div>
        </TableCell>
        <TableCell>
          <span className="text-sm text-white truncate max-w-[150px] block" title={stream.container_name || stream.container_id}>
            {stream.container_name || stream.container_id?.slice(0, TRUNCATED_CONTAINER_ID_LENGTH) || 'N/A'}
          </span>
        </TableCell>
        <TableCell>
          <div className="flex items-center gap-1">
            <Clock className="h-3 w-3 text-muted-foreground" />
            <span className="text-sm text-white">{formatTime(stream.started_at)}</span>
          </div>
        </TableCell>
        {showSpeedColumns && (
          <>
            <TableCell className="text-right">
              {isActive ? (
                <div className="flex items-center justify-end gap-1">
                  <Download className="h-3 w-3 text-success" />
                  <span className="text-sm font-semibold text-success">
                    {formatBytesPerSecond((stream.speed_down || 0) * 1024)}
                  </span>
                </div>
              ) : (
                <span className="text-sm text-muted-foreground">—</span>
              )}
            </TableCell>
            <TableCell className="text-right">
              {isActive ? (
                <div className="flex items-center justify-end gap-1">
                  <Upload className="h-3 w-3 text-destructive" />
                  <span className="text-sm font-semibold text-destructive">
                    {formatBytesPerSecond((stream.speed_up || 0) * 1024)}
                  </span>
                </div>
              ) : (
                <span className="text-sm text-muted-foreground">—</span>
              )}
            </TableCell>
            <TableCell className="text-right">
              {isActive ? (
                <div className="flex items-center justify-end gap-1">
                  <Users className="h-3 w-3 text-primary" />
                  <span className="text-sm font-semibold text-primary">
                    {stream.peers != null ? stream.peers : 'N/A'}
                  </span>
                </div>
              ) : (
                <span className="text-sm text-muted-foreground">—</span>
              )}
            </TableCell>
          </>
        )}
        {showSpeedColumns && (
          <TableCell className="text-right">
            {isActive && stream.livepos && stream.livepos.live_last ? (
              <span className="text-sm text-white">
                {formatLiveposTimestamp(stream.livepos.live_last)}
              </span>
            ) : (
              <span className="text-sm text-muted-foreground">—</span>
            )}
          </TableCell>
        )}
        <TableCell className="text-right">
          <span className="text-sm text-white">{formatBytes(stream.downloaded)}</span>
        </TableCell>
        <TableCell className="text-right">
          <span className="text-sm text-white">{formatBytes(stream.uploaded)}</span>
        </TableCell>
      </TableRow>
      {isExpanded && (
        <TableRow>
          {/* colspan: active streams have 12 cols (checkbox + expand + 10 data), ended streams have 7 cols (expand + 6 data) */}
          <TableCell colSpan={showSpeedColumns ? 12 : 7} className="p-6 bg-muted/50">
            <div className="space-y-6">
              {/* Stream Details */}
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">Stream ID</p>
                  <p className="text-sm font-medium text-foreground break-all">{stream.id}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Engine</p>
                  <p className="text-sm font-medium text-foreground">
                    {stream.container_name || stream.container_id?.slice(0, TRUNCATED_CONTAINER_ID_LENGTH) || 'N/A'}
                  </p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Started At</p>
                  <p className="text-sm font-medium text-foreground">{formatTime(stream.started_at)}</p>
                </div>
                {isEnded && stream.ended_at && (
                  <div>
                    <p className="text-xs text-muted-foreground">Ended At</p>
                    <p className="text-sm font-medium text-foreground">{formatTime(stream.ended_at)}</p>
                  </div>
                )}
                
                {/* LivePos Information */}
                {stream.livepos && (
                  <>
                    <div className="col-span-full">
                      <p className="text-sm font-semibold text-foreground mb-2">Live Position Data</p>
                    </div>
                    {stream.livepos.pos && (
                      <div>
                        <p className="text-xs text-muted-foreground">Current Position</p>
                        <p className="text-sm font-medium text-foreground">{formatLiveposTimestamp(stream.livepos.pos)}</p>
                      </div>
                    )}
                    {stream.livepos.live_first && (
                      <div>
                        <p className="text-xs text-muted-foreground">Live Start</p>
                        <p className="text-sm font-medium text-foreground">{formatLiveposTimestamp(stream.livepos.live_first)}</p>
                      </div>
                    )}
                    {stream.livepos.buffer_pieces && (
                      <div>
                        <p className="text-xs text-muted-foreground">Buffer Pieces</p>
                        <p className="text-sm font-medium text-foreground">{stream.livepos.buffer_pieces}</p>
                      </div>
                    )}
                    {bufferDuration !== null && (
                      <div>
                        <p className="text-xs text-muted-foreground">Buffer Duration</p>
                        <p className="text-sm font-medium text-foreground">{bufferDuration}s</p>
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
                        <p className="text-sm font-medium text-foreground break-all">{extendedStats.title}</p>
                      </div>
                    )}
                    {extendedStats.content_type && (
                      <div>
                        <p className="text-xs text-muted-foreground">Content Type</p>
                        <p className="text-sm font-medium text-foreground">{extendedStats.content_type}</p>
                      </div>
                    )}
                    {extendedStats.transport_type && (
                      <div>
                        <p className="text-xs text-muted-foreground">Transport Type</p>
                        <p className="text-sm font-medium text-foreground">{extendedStats.transport_type}</p>
                      </div>
                    )}
                    {extendedStats.infohash && (
                      <div className="col-span-full">
                        <p className="text-xs text-muted-foreground">Infohash</p>
                        <p className="text-sm font-medium text-foreground break-all">{extendedStats.infohash}</p>
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

              {/* Connected Clients */}
              {isActive && (
                <div className="border-t pt-4">
                  <p className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
                    <Users className="h-4 w-4" />
                    Connected Clients ({clients.length})
                  </p>
                  {clientsLoading ? (
                    <p className="text-sm text-muted-foreground">Loading clients...</p>
                  ) : clients.length > 0 ? (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      {clients.map((client, idx) => (
                        <div key={client.client_id || idx} className="p-3 bg-muted/30 rounded-md border border-border">
                          <div className="space-y-1">
                            <div className="flex items-center justify-between">
                              <p className="text-xs text-muted-foreground">Client ID</p>
                              <p className="text-xs font-mono text-foreground truncate max-w-[200px]" title={client.client_id}>
                                {client.client_id?.slice(0, TRUNCATED_CLIENT_ID_LENGTH)}...
                              </p>
                            </div>
                            {client.ip_address && (
                              <div className="flex items-center justify-between">
                                <p className="text-xs text-muted-foreground">IP Address</p>
                                <p className="text-xs font-medium text-foreground">{client.ip_address}</p>
                              </div>
                            )}
                            {client.connected_at && (
                              <div className="flex items-center justify-between">
                                <p className="text-xs text-muted-foreground">Connected</p>
                                <p className="text-xs text-foreground">
                                  {new Date(client.connected_at * 1000).toLocaleTimeString()}
                                </p>
                              </div>
                            )}
                            {client.bytes_sent !== undefined && (
                              <div className="flex items-center justify-between">
                                <p className="text-xs text-muted-foreground">Bytes Sent</p>
                                <p className="text-xs text-foreground">{formatBytes(client.bytes_sent)}</p>
                              </div>
                            )}
                            {client.user_agent && (
                              <div className="col-span-full">
                                <p className="text-xs text-muted-foreground">User Agent</p>
                                <p className="text-xs font-mono text-foreground truncate" title={client.user_agent}>
                                  {client.user_agent}
                                </p>
                              </div>
                            )}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">No clients connected</p>
                  )}
                </div>
              )}

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
  
  // State for sorting
  const [sortColumn, setSortColumn] = useState(null)
  const [sortDirection, setSortDirection] = useState('asc')
  
  // State for selection (only for active streams)
  const [selectedStreams, setSelectedStreams] = useState(new Set())
  
  // State for ended streams collapsible
  const [endedStreamsOpen, setEndedStreamsOpen] = useState(false)
  
  // State for batch operation
  const [batchStopping, setBatchStopping] = useState(false)
  
  // Handle column header click for sorting
  const handleSort = (column) => {
    if (sortColumn === column) {
      // Toggle direction
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc')
    } else {
      setSortColumn(column)
      setSortDirection('asc')
    }
  }
  
  // Sort streams based on current sort settings
  const sortStreams = (streamsList) => {
    if (!sortColumn) return streamsList
    
    return [...streamsList].sort((a, b) => {
      let aVal = a[sortColumn]
      let bVal = b[sortColumn]
      
      // Handle special cases
      if (sortColumn === 'started_at') {
        aVal = new Date(aVal).getTime()
        bVal = new Date(bVal).getTime()
      } else if (sortColumn === 'downloaded' || sortColumn === 'uploaded' || 
                 sortColumn === 'speed_down' || sortColumn === 'speed_up' || 
                 sortColumn === 'peers') {
        aVal = aVal || 0
        bVal = bVal || 0
      } else if (typeof aVal === 'string' && typeof bVal === 'string') {
        aVal = aVal.toLowerCase()
        bVal = bVal.toLowerCase()
      }
      
      if (aVal < bVal) return sortDirection === 'asc' ? -1 : 1
      if (aVal > bVal) return sortDirection === 'asc' ? 1 : -1
      return 0
    })
  }
  
  // Render sort icon
  const SortIcon = ({ column }) => {
    if (sortColumn !== column) {
      return <ArrowUpDown className="ml-2 h-4 w-4 inline-block" />
    }
    return sortDirection === 'asc' 
      ? <ArrowUp className="ml-2 h-4 w-4 inline-block" />
      : <ArrowDown className="ml-2 h-4 w-4 inline-block" />
  }
  
  // Handle select all
  const handleSelectAll = (checked) => {
    if (checked) {
      setSelectedStreams(new Set(activeStreams.map(s => s.id)))
    } else {
      setSelectedStreams(new Set())
    }
  }
  
  // Handle individual selection
  const handleToggleSelect = (streamId) => {
    const newSelected = new Set(selectedStreams)
    if (newSelected.has(streamId)) {
      newSelected.delete(streamId)
    } else {
      newSelected.add(streamId)
    }
    setSelectedStreams(newSelected)
  }
  
  // Check if all are selected
  const allSelected = activeStreams.length > 0 && selectedStreams.size === activeStreams.length
  const someSelected = selectedStreams.size > 0 && selectedStreams.size < activeStreams.length
  
  // Handle batch stop
  const handleBatchStop = async () => {
    if (selectedStreams.size === 0) return
    
    setBatchStopping(true)
    
    try {
      // Get command URLs for selected streams
      const commandUrls = activeStreams
        .filter(s => selectedStreams.has(s.id))
        .map(s => s.command_url)
        .filter(url => url) // Filter out any null/undefined URLs
      
      if (commandUrls.length === 0) {
        console.error('No valid command URLs found for selected streams')
        setBatchStopping(false)
        return
      }
      
      // Call batch stop API
      const headers = {
        'Content-Type': 'application/json'
      }
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }
      
      const response = await fetch(`${orchUrl}/streams/batch-stop`, {
        method: 'POST',
        headers,
        body: JSON.stringify(commandUrls)
      })
      
      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`)
      }
      
      const result = await response.json()
      console.log('Batch stop result:', result)
      
      // Clear selection
      setSelectedStreams(new Set())
      
      // Optionally show a toast notification
      if (result.success_count > 0) {
        console.log(`Successfully stopped ${result.success_count} stream(s)`)
      }
      if (result.failure_count > 0) {
        console.warn(`Failed to stop ${result.failure_count} stream(s)`)
      }
    } catch (error) {
      console.error('Error during batch stop:', error)
    } finally {
      setBatchStopping(false)
    }
  }
  
  const sortedActiveStreams = sortStreams(activeStreams)
  const sortedEndedStreams = sortStreams(endedStreams)

  return (
    <div className="space-y-6">
      {/* Active Streams Section */}
      <div>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-2xl font-semibold">Active Streams ({activeStreams.length})</h2>
          {selectedStreams.size > 0 && (
            <Button
              variant="destructive"
              onClick={handleBatchStop}
              disabled={batchStopping}
              className="flex items-center gap-2"
            >
              <StopCircle className="h-4 w-4" />
              {batchStopping ? 'Stopping...' : `Stop Selected (${selectedStreams.size})`}
            </Button>
          )}
        </div>
        {activeStreams.length === 0 ? (
          <div className="text-center py-8 text-muted-foreground">
            No active streams
          </div>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[40px]">
                    <Checkbox
                      checked={someSelected ? "indeterminate" : allSelected}
                      onCheckedChange={handleSelectAll}
                      aria-label="Select all"
                    />
                  </TableHead>
                  <TableHead className="w-[40px]"></TableHead>
                  <TableHead 
                    className="cursor-pointer select-none"
                    onClick={() => handleSort('status')}
                  >
                    Status <SortIcon column="status" />
                  </TableHead>
                  <TableHead 
                    className="cursor-pointer select-none"
                    onClick={() => handleSort('id')}
                  >
                    Stream ID <SortIcon column="id" />
                  </TableHead>
                  <TableHead 
                    className="cursor-pointer select-none"
                    onClick={() => handleSort('container_name')}
                  >
                    Engine <SortIcon column="container_name" />
                  </TableHead>
                  <TableHead 
                    className="cursor-pointer select-none"
                    onClick={() => handleSort('started_at')}
                  >
                    Started <SortIcon column="started_at" />
                  </TableHead>
                  <TableHead 
                    className="text-right cursor-pointer select-none"
                    onClick={() => handleSort('speed_down')}
                  >
                    Download <SortIcon column="speed_down" />
                  </TableHead>
                  <TableHead 
                    className="text-right cursor-pointer select-none"
                    onClick={() => handleSort('speed_up')}
                  >
                    Upload <SortIcon column="speed_up" />
                  </TableHead>
                  <TableHead 
                    className="text-right cursor-pointer select-none"
                    onClick={() => handleSort('peers')}
                  >
                    Peers <SortIcon column="peers" />
                  </TableHead>
                  <TableHead 
                    className="text-right"
                  >
                    Broadcast Position
                  </TableHead>
                  <TableHead 
                    className="text-right cursor-pointer select-none"
                    onClick={() => handleSort('downloaded')}
                  >
                    Downloaded <SortIcon column="downloaded" />
                  </TableHead>
                  <TableHead 
                    className="text-right cursor-pointer select-none"
                    onClick={() => handleSort('uploaded')}
                  >
                    Uploaded <SortIcon column="uploaded" />
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {sortedActiveStreams.map((stream) => (
                  <StreamTableRow
                    key={stream.id}
                    stream={stream}
                    orchUrl={orchUrl}
                    apiKey={apiKey}
                    onStopStream={onStopStream}
                    onDeleteEngine={onDeleteEngine}
                    debugMode={debugMode}
                    isSelected={selectedStreams.has(stream.id)}
                    onToggleSelect={() => handleToggleSelect(stream.id)}
                  />
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </div>

      {/* Ended Streams Section - Collapsible */}
      {endedStreams.length > 0 && (
        <Collapsible open={endedStreamsOpen} onOpenChange={setEndedStreamsOpen}>
          <div className="flex items-center justify-between">
            <CollapsibleTrigger asChild>
              <Button
                variant="ghost"
                className="flex items-center gap-2 p-0 hover:bg-transparent"
              >
                <h2 className="text-2xl font-semibold">Ended Streams ({endedStreams.length})</h2>
                {endedStreamsOpen ? (
                  <ChevronUp className="h-5 w-5" />
                ) : (
                  <ChevronDown className="h-5 w-5" />
                )}
              </Button>
            </CollapsibleTrigger>
          </div>
          <CollapsibleContent className="mt-4">
            <div className="rounded-md border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[40px]"></TableHead>
                    <TableHead 
                      className="cursor-pointer select-none"
                      onClick={() => handleSort('status')}
                    >
                      Status <SortIcon column="status" />
                    </TableHead>
                    <TableHead 
                      className="cursor-pointer select-none"
                      onClick={() => handleSort('id')}
                    >
                      Stream ID <SortIcon column="id" />
                    </TableHead>
                    <TableHead 
                      className="cursor-pointer select-none"
                      onClick={() => handleSort('container_name')}
                    >
                      Engine <SortIcon column="container_name" />
                    </TableHead>
                    <TableHead 
                      className="cursor-pointer select-none"
                      onClick={() => handleSort('started_at')}
                    >
                      Started <SortIcon column="started_at" />
                    </TableHead>
                    <TableHead 
                      className="text-right cursor-pointer select-none"
                      onClick={() => handleSort('downloaded')}
                    >
                      Downloaded <SortIcon column="downloaded" />
                    </TableHead>
                    <TableHead 
                      className="text-right cursor-pointer select-none"
                      onClick={() => handleSort('uploaded')}
                    >
                      Uploaded <SortIcon column="uploaded" />
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {sortedEndedStreams.map((stream) => (
                    <StreamTableRow
                      key={stream.id}
                      stream={stream}
                      orchUrl={orchUrl}
                      apiKey={apiKey}
                      onStopStream={onStopStream}
                      onDeleteEngine={onDeleteEngine}
                      debugMode={debugMode}
                      showSpeedColumns={false}
                    />
                  ))}
                </TableBody>
              </Table>
            </div>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  )
}

export default StreamsTable
