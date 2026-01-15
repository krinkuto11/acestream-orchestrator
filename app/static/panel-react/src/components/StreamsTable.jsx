import React, { useState, useEffect, useCallback, useRef } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
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
  ArrowDown,
  Globe
} from 'lucide-react'
import { formatTime, formatBytes, formatBytesPerSecond, countryCodeToFlag } from '../utils/formatters'
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
  const [streamStatus, setStreamStatus] = useState(null) // For tracking AceStream stat URL status
  const [peers, setPeers] = useState([])
  const [peersLoading, setPeersLoading] = useState(false)
  const [peersError, setPeersError] = useState(null)
  
  // Track if we have fetched data at least once to prevent loading flicker on refreshes
  const hasClientsDataRef = useRef(false)
  const hasStatsDataRef = useRef(false)
  const hasExtendedStatsDataRef = useRef(false)
  const hasPeersDataRef = useRef(false)

  const isActive = stream.status === 'started'
  const isEnded = stream.status === 'ended'
  
  // Determine if stream is prebuffering based on stat URL response
  const isPrebuffering = streamStatus === 'prebuf'

  const fetchStats = useCallback(async () => {
    if (!stream || !isExpanded) return
    
    // Only show loading if we don't have data yet
    if (!hasStatsDataRef.current) {
      setLoading(true)
    }
    
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
        hasStatsDataRef.current = true
      }
    } catch (err) {
      console.error('Failed to fetch stats:', err)
      // Keep existing stats on error
    } finally {
      setLoading(false)
    }
  }, [stream, orchUrl, apiKey, isExpanded])

  const fetchExtendedStats = useCallback(async () => {
    if (!stream) return
    // Only fetch if expanded OR if active (to show title in collapsed state)
    if (!isExpanded && !isActive) return
    
    // Only show loading if we don't have data yet
    if (!hasExtendedStatsDataRef.current) {
      setExtendedStatsLoading(true)
    }
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
        hasExtendedStatsDataRef.current = true
      } else {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }
    } catch (err) {
      console.error('Failed to fetch extended stats:', err)
      // Only set error if we don't have cached data
      if (!hasExtendedStatsDataRef.current) {
        setExtendedStatsError(err.message || String(err))
      }
    } finally {
      setExtendedStatsLoading(false)
    }
  }, [stream, orchUrl, apiKey, isExpanded, isActive])

  const fetchClients = useCallback(async () => {
    if (!stream || !isExpanded || !stream.key) return
    
    // Only show loading indicator if we don't have any data yet
    if (!hasClientsDataRef.current) {
      setClientsLoading(true)
    }
    
    try {
      const response = await fetch(
        `${orchUrl}/proxy/streams/${encodeURIComponent(stream.key)}/clients`
      )
      
      if (response.ok) {
        const data = await response.json()
        setClients(data.clients || [])
        hasClientsDataRef.current = true
      } else if (!hasClientsDataRef.current) {
        // Only clear clients on error if we had no data
        setClients([])
      }
    } catch (err) {
      console.error('Failed to fetch clients:', err)
      // Keep existing clients on error if we had data
      if (!hasClientsDataRef.current) {
        setClients([])
      }
    } finally {
      setClientsLoading(false)
    }
  }, [stream, orchUrl, isExpanded])

  const fetchPeers = useCallback(async () => {
    if (!stream || !isExpanded || !isActive) return
    
    // Only show loading indicator if we don't have any data yet
    if (!hasPeersDataRef.current) {
      setPeersLoading(true)
    }
    setPeersError(null)
    
    try {
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }
      
      const response = await fetch(
        `${orchUrl}/streams/${encodeURIComponent(stream.id)}/peers`,
        { headers }
      )
      
      if (response.ok) {
        const data = await response.json()
        setPeers(data.peers || [])
        hasPeersDataRef.current = true
      } else {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`)
      }
    } catch (err) {
      console.error('Failed to fetch peers:', err)
      // Only set error if we don't have cached data
      if (!hasPeersDataRef.current) {
        setPeersError(err.message || String(err))
        setPeers([])
      }
    } finally {
      setPeersLoading(false)
    }
  }, [stream, orchUrl, apiKey, isExpanded, isActive])

  const fetchStreamStatus = useCallback(async () => {
    if (!stream || !stream.stat_url || !isActive) return
    
    try {
      const response = await fetch(stream.stat_url)
      
      if (response.ok) {
        const data = await response.json()
        // AceStream stat response can have status in various places
        // Check common paths: status, response.status, etc.
        const status = data.status || data.response?.status || null
        setStreamStatus(status)
      }
    } catch (err) {
      console.error('Failed to fetch stream status from stat URL:', err)
      // Keep existing status on error
    }
  }, [stream, isActive])

  const refreshData = useCallback(() => {
    fetchStats()
    fetchClients()
    fetchPeers()
    fetchStreamStatus()
  }, [fetchStats, fetchClients, fetchPeers, fetchStreamStatus])

  useEffect(() => {
    if (isExpanded && isActive) {
      fetchStats()
      fetchExtendedStats()
      fetchClients()
      fetchPeers()
      fetchStreamStatus()
      const interval = setInterval(refreshData, 10000)
      return () => clearInterval(interval)
    }
  }, [refreshData, fetchStats, fetchExtendedStats, fetchClients, fetchPeers, fetchStreamStatus, isExpanded, isActive])

  // Also fetch stream status periodically even when not expanded, for active streams
  useEffect(() => {
    if (isActive) {
      fetchStreamStatus()
      fetchExtendedStats() // Fetch extended stats for active streams to show title
      const interval = setInterval(() => {
        fetchStreamStatus()
        fetchExtendedStats()
      }, 10000) // Check every 10 seconds
      return () => clearInterval(interval)
    }
  }, [fetchStreamStatus, fetchExtendedStats, isActive])

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
          <TableCell className="w-[40px] text-center align-middle px-2">
            <div className="flex items-center justify-center h-full">
              <Checkbox
                checked={isSelected}
                onCheckedChange={onToggleSelect}
                aria-label="Select stream"
                className="mx-auto"
              />
            </div>
          </TableCell>
        )}
        <TableCell className="w-[40px]">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0 border border-input hover:bg-accent hover:text-accent-foreground mx-auto"
            onClick={() => setIsExpanded(!isExpanded)}
          >
            {isExpanded ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
          </Button>
        </TableCell>
        <TableCell className="text-center">
          {isPrebuffering ? (
            <Badge className="flex items-center gap-1 w-fit mx-auto bg-orange-500 hover:bg-orange-600 border-transparent">
              <Clock className="h-3 w-3" />
              <span>PREBUF</span>
            </Badge>
          ) : (
            <Badge variant={isActive ? "success" : "secondary"} className="flex items-center gap-1 w-fit mx-auto">
              {isActive ? <PlayCircle className="h-3 w-3" /> : <Activity className="h-3 w-3" />}
              <span>{isActive ? 'ACTIVE' : 'ENDED'}</span>
            </Badge>
          )}
        </TableCell>
        <TableCell className="font-medium text-center">
          <div className="flex flex-col gap-1 items-center">
            {extendedStats?.title && (
              <span className="text-xs text-muted-foreground truncate max-w-[12rem]" title={extendedStats.title}>
                {extendedStats.title}
              </span>
            )}
            <span className="text-sm text-foreground truncate max-w-[12rem]" title={stream.id}>
              {stream.id.slice(0, TRUNCATED_STREAM_ID_LENGTH)}...
            </span>
          </div>
        </TableCell>
        <TableCell className="text-center">
          {isActive && bufferDuration !== null ? (
            <span className="text-sm text-foreground">
              {bufferDuration}s
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell className="text-center">
          <span className="text-sm text-foreground truncate max-w-[150px] block mx-auto" title={stream.container_name || stream.container_id}>
            {stream.container_name || stream.container_id?.slice(0, TRUNCATED_CONTAINER_ID_LENGTH) || 'N/A'}
          </span>
        </TableCell>
        <TableCell className="text-center">
          <span className="text-sm text-foreground">{formatTime(stream.started_at)}</span>
        </TableCell>
        {showSpeedColumns && (
          <>
            <TableCell className="text-center">
              {isActive ? (
                <span className="text-sm font-semibold text-success">
                  {formatBytesPerSecond((stream.speed_down || 0) * 1024)}
                </span>
              ) : (
                <span className="text-sm text-muted-foreground">—</span>
              )}
            </TableCell>
            <TableCell className="text-center">
              {isActive ? (
                <span className="text-sm font-semibold text-destructive">
                  {formatBytesPerSecond((stream.speed_up || 0) * 1024)}
                </span>
              ) : (
                <span className="text-sm text-muted-foreground">—</span>
              )}
            </TableCell>
            <TableCell className="text-center">
              {isActive ? (
                <div className="flex items-center justify-center gap-1">
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
          <TableCell className="text-center">
            {isActive && stream.livepos && stream.livepos.live_last ? (
              <span className="text-sm text-foreground">
                {formatLiveposTimestamp(stream.livepos.live_last)}
              </span>
            ) : (
              <span className="text-sm text-muted-foreground">—</span>
            )}
          </TableCell>
        )}
        <TableCell className="text-center">
          <span className="text-sm text-foreground">{formatBytes(stream.downloaded)}</span>
        </TableCell>
        <TableCell className="text-center">
          <span className="text-sm text-foreground">{formatBytes(stream.uploaded)}</span>
        </TableCell>
      </TableRow>
      {isExpanded && (
        <TableRow>
          {/* colspan: active streams have 13 cols (checkbox + expand + 11 data), ended streams have 7 cols (expand + 6 data) */}
          <TableCell colSpan={showSpeedColumns ? 13 : 7} className="p-6 bg-muted/50">
            {isActive ? (
              <Tabs defaultValue="statistics" className="w-full">
                <TabsList className="grid w-full grid-cols-3">
                  <TabsTrigger value="statistics">Statistics</TabsTrigger>
                  <TabsTrigger value="peers">
                    <Globe className="h-4 w-4 mr-2" />
                    Peers ({peers.length})
                  </TabsTrigger>
                  <TabsTrigger value="clients">
                    <Users className="h-4 w-4 mr-2" />
                    Clients ({clients.length})
                  </TabsTrigger>
                </TabsList>

                {/* Statistics Tab */}
                <TabsContent value="statistics" className="space-y-6 mt-4">
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

                  {/* Actions */}
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
                </TabsContent>

                {/* Peers Tab */}
                <TabsContent value="peers" className="space-y-4 mt-4">
                  <div>
                    <p className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
                      <Globe className="h-4 w-4" />
                      Torrent Peers ({peers.length})
                    </p>
                    {peersLoading ? (
                      <p className="text-sm text-muted-foreground">Loading peer information...</p>
                    ) : peersError ? (
                      <p className="text-sm text-destructive">Error loading peers: {peersError}</p>
                    ) : peers.length > 0 ? (
                      <div className="rounded-md border">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>IP Address</TableHead>
                              <TableHead>Country</TableHead>
                              <TableHead>City</TableHead>
                              <TableHead>ISP</TableHead>
                              <TableHead>Client</TableHead>
                              <TableHead className="text-right">Progress</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {peers.map((peer, idx) => (
                              <TableRow key={`${peer.ip}-${idx}`}>
                                <TableCell className="font-mono text-xs text-foreground">
                                  {peer.ip}:{peer.port || 'N/A'}
                                </TableCell>
                                <TableCell className="text-sm text-foreground">
                                  <span title={peer.country}>
                                    {peer.country_code && peer.country_code !== '??' && (
                                      <span className="mr-2">{countryCodeToFlag(peer.country_code)}</span>
                                    )}
                                    {peer.country || 'Unknown'}
                                  </span>
                                </TableCell>
                                <TableCell className="text-sm text-foreground">
                                  {peer.city || 'Unknown'}
                                </TableCell>
                                <TableCell className="text-sm text-foreground">
                                  <span className="truncate max-w-[200px] block" title={peer.isp}>
                                    {peer.isp || 'Unknown'}
                                  </span>
                                </TableCell>
                                <TableCell className="font-mono text-xs text-foreground">
                                  <span className="truncate max-w-[150px] block" title={peer.client}>
                                    {peer.client || 'Unknown'}
                                  </span>
                                </TableCell>
                                <TableCell className="text-right text-sm text-foreground">
                                  {peer.progress !== undefined ? `${peer.progress.toFixed(1)}%` : 'N/A'}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground">No peers connected to the torrent swarm</p>
                    )}
                  </div>
                </TabsContent>

                {/* Clients Tab */}
                <TabsContent value="clients" className="space-y-4 mt-4">
                  <div>
                    <p className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
                      <Users className="h-4 w-4" />
                      Connected Clients ({clients.length})
                    </p>
                    {clientsLoading ? (
                      <p className="text-sm text-muted-foreground">Loading clients...</p>
                    ) : clients.length > 0 ? (
                      <div className="rounded-md border">
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Client ID</TableHead>
                              <TableHead>IP Address</TableHead>
                              <TableHead>Connected At</TableHead>
                              <TableHead className="text-right">Bytes Sent</TableHead>
                              <TableHead>User Agent</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {clients.map((client, idx) => (
                              <TableRow key={client.client_id || idx}>
                                <TableCell className="font-mono text-xs text-foreground">
                                  <span className="truncate max-w-[200px] block" title={client.client_id}>
                                    {client.client_id && client.client_id.length > TRUNCATED_CLIENT_ID_LENGTH
                                      ? `${client.client_id.slice(0, TRUNCATED_CLIENT_ID_LENGTH)}...`
                                      : client.client_id || 'N/A'
                                    }
                                  </span>
                                </TableCell>
                                <TableCell className="text-sm text-foreground">
                                  {client.ip_address || 'N/A'}
                                </TableCell>
                                <TableCell className="text-sm text-foreground">
                                  {client.connected_at 
                                    ? new Date(client.connected_at * 1000).toLocaleString()
                                    : 'N/A'
                                  }
                                </TableCell>
                                <TableCell className="text-right text-sm text-foreground">
                                  {client.bytes_sent !== undefined ? formatBytes(client.bytes_sent) : 'N/A'}
                                </TableCell>
                                <TableCell className="font-mono text-xs text-foreground">
                                  <span className="truncate max-w-[300px] block" title={client.user_agent}>
                                    {client.user_agent || 'N/A'}
                                  </span>
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </div>
                    ) : (
                      <p className="text-sm text-muted-foreground">No clients connected</p>
                    )}
                  </div>
                </TabsContent>
              </Tabs>
            ) : (
              // For ended streams, show simple details without tabs
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
                  {stream.ended_at && (
                    <div>
                      <p className="text-xs text-muted-foreground">Ended At</p>
                      <p className="text-sm font-medium text-foreground">{formatTime(stream.ended_at)}</p>
                    </div>
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
                      {extendedStats.infohash && (
                        <div className="col-span-full">
                          <p className="text-xs text-muted-foreground">Infohash</p>
                          <p className="text-sm font-medium text-foreground break-all">{extendedStats.infohash}</p>
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
              </div>
            )}
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
          <div className="rounded-md border overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-[40px] text-center align-middle px-2">
                    <div className="flex items-center justify-center h-full">
                      <Checkbox
                        checked={someSelected ? "indeterminate" : allSelected}
                        onCheckedChange={handleSelectAll}
                        aria-label="Select all"
                        className="mx-auto"
                      />
                    </div>
                  </TableHead>
                  <TableHead className="w-[40px] text-center"></TableHead>
                  <TableHead 
                    className="cursor-pointer select-none text-center"
                    onClick={() => handleSort('status')}
                  >
                    Status <SortIcon column="status" />
                  </TableHead>
                  <TableHead 
                    className="cursor-pointer select-none text-center"
                    onClick={() => handleSort('id')}
                  >
                    Stream <SortIcon column="id" />
                  </TableHead>
                  <TableHead className="text-center">
                    Buffer
                  </TableHead>
                  <TableHead 
                    className="cursor-pointer select-none text-center"
                    onClick={() => handleSort('container_name')}
                  >
                    Engine <SortIcon column="container_name" />
                  </TableHead>
                  <TableHead 
                    className="cursor-pointer select-none text-center"
                    onClick={() => handleSort('started_at')}
                  >
                    Started <SortIcon column="started_at" />
                  </TableHead>
                  <TableHead 
                    className="text-center cursor-pointer select-none"
                    onClick={() => handleSort('speed_down')}
                  >
                    Download <SortIcon column="speed_down" />
                  </TableHead>
                  <TableHead 
                    className="text-center cursor-pointer select-none"
                    onClick={() => handleSort('speed_up')}
                  >
                    Upload <SortIcon column="speed_up" />
                  </TableHead>
                  <TableHead 
                    className="text-center cursor-pointer select-none"
                    onClick={() => handleSort('peers')}
                  >
                    Peers <SortIcon column="peers" />
                  </TableHead>
                  <TableHead 
                    className="text-center"
                  >
                    Broadcast Position
                  </TableHead>
                  <TableHead 
                    className="text-center cursor-pointer select-none"
                    onClick={() => handleSort('downloaded')}
                  >
                    Downloaded <SortIcon column="downloaded" />
                  </TableHead>
                  <TableHead 
                    className="text-center cursor-pointer select-none"
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
            <div className="rounded-md border overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-[40px] text-center"></TableHead>
                    <TableHead 
                      className="cursor-pointer select-none text-center"
                      onClick={() => handleSort('status')}
                    >
                      Status <SortIcon column="status" />
                    </TableHead>
                    <TableHead 
                      className="cursor-pointer select-none text-center"
                      onClick={() => handleSort('id')}
                    >
                      Stream <SortIcon column="id" />
                    </TableHead>
                    <TableHead 
                      className="cursor-pointer select-none text-center"
                      onClick={() => handleSort('container_name')}
                    >
                      Engine <SortIcon column="container_name" />
                    </TableHead>
                    <TableHead 
                      className="cursor-pointer select-none text-center"
                      onClick={() => handleSort('started_at')}
                    >
                      Started <SortIcon column="started_at" />
                    </TableHead>
                    <TableHead 
                      className="text-center cursor-pointer select-none"
                      onClick={() => handleSort('downloaded')}
                    >
                      Downloaded <SortIcon column="downloaded" />
                    </TableHead>
                    <TableHead 
                      className="text-center cursor-pointer select-none"
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
