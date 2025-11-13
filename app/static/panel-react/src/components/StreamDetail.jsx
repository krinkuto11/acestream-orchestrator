import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { X, StopCircle, Trash2, ExternalLink, ChevronDown, ChevronUp } from 'lucide-react'
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
import { formatTime, formatBytes } from '../utils/formatters'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { Badge } from '@/components/ui/badge'

ChartJS.register(
  CategoryScale,
  LinearScale,
  PointElement,
  LineElement,
  Title,
  Tooltip,
  Legend
)

function StreamDetail({ stream, orchUrl, apiKey, onStopStream, onDeleteEngine, onClose }) {
  const [stats, setStats] = useState([])
  const [loading, setLoading] = useState(false)
  const [extendedStats, setExtendedStats] = useState(null)
  const [extendedStatsLoading, setExtendedStatsLoading] = useState(false)
  const [isExtendedStatsOpen, setIsExtendedStatsOpen] = useState(false)

  useEffect(() => {
    if (!stream) return
    
    const abortController = new AbortController()
    
    // Fetch regular stats
    const fetchStats = async () => {
      if (abortController.signal.aborted) return
      
      setLoading(true)
      try {
        const since = new Date(Date.now() - 60 * 60 * 1000).toISOString()
        const headers = {}
        if (apiKey) {
          headers['Authorization'] = `Bearer ${apiKey}`
        }
        
        const response = await fetch(
          `${orchUrl}/streams/${encodeURIComponent(stream.id)}/stats?since=${encodeURIComponent(since)}`,
          { headers, signal: abortController.signal }
        )
        
        if (response.ok) {
          const data = await response.json()
          setStats(data)
        }
      } catch (err) {
        if (err.name !== 'AbortError') {
          console.error('Failed to fetch stats:', err)
        }
      } finally {
        setLoading(false)
      }
    }
    
    // Fetch extended stats (once)
    const fetchExtendedStats = async () => {
      if (abortController.signal.aborted) return
      
      setExtendedStatsLoading(true)
      try {
        const headers = {}
        if (apiKey) {
          headers['Authorization'] = `Bearer ${apiKey}`
        }
        
        const response = await fetch(
          `${orchUrl}/streams/${encodeURIComponent(stream.id)}/extended-stats`,
          { headers, signal: abortController.signal }
        )
        
        if (response.ok) {
          const data = await response.json()
          setExtendedStats(data)
        }
      } catch (err) {
        if (err.name !== 'AbortError') {
          console.error('Failed to fetch extended stats:', err)
        }
      } finally {
        setExtendedStatsLoading(false)
      }
    }
    
    // Initial fetch
    fetchStats()
    fetchExtendedStats()
    
    // Set up interval for stats (but not extended stats)
    const interval = setInterval(fetchStats, 10000)
    
    return () => {
      clearInterval(interval)
      abortController.abort()
    }
  }, [stream, orchUrl, apiKey])

  // AceStream API returns speed in KB/s, so we divide by 1024 to convert to MB/s
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
    <Card>
      <CardHeader>
        <div className="flex justify-between items-start">
          <CardTitle className="text-2xl">Stream Details</CardTitle>
          <Button variant="ghost" size="icon" onClick={onClose}>
            <X className="h-4 w-4" />
          </Button>
        </div>
      </CardHeader>

      <CardContent className="space-y-6">
        <Collapsible open={isExtendedStatsOpen} onOpenChange={setIsExtendedStatsOpen}>
          <CollapsibleTrigger asChild>
            <Button variant="outline" className="w-full flex justify-between items-center">
              <span className="font-semibold">Stream Information</span>
              {isExtendedStatsOpen ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
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
              
              {/* Extended Stats from analyze_content API */}
              {extendedStatsLoading && (
                <div className="col-span-2">
                  <p className="text-sm text-muted-foreground">Loading extended stats...</p>
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
          </CollapsibleContent>
        </Collapsible>

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
            onClick={() => onStopStream(stream.id, stream.container_id)}
            className="flex items-center gap-2"
          >
            <StopCircle className="h-4 w-4" />
            Stop Stream
          </Button>
          <Button
            variant="outline"
            onClick={() => onDeleteEngine(stream.container_id)}
            className="flex items-center gap-2 text-destructive hover:text-destructive"
          >
            <Trash2 className="h-4 w-4" />
            Delete Engine
          </Button>
        </div>
      </CardContent>
    </Card>
  )
}

export default StreamDetail
