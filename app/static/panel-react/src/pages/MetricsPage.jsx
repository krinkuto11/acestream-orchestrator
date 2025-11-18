import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertCircle, ArrowUpDown, ArrowUp, ArrowDown, HardDrive, Activity } from 'lucide-react'
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

export function MetricsPage({ apiKey, orchUrl }) {
  const [metrics, setMetrics] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [historicalData, setHistoricalData] = useState({
    timestamps: [],
    uploadSpeedMbps: [],
    downloadSpeedMbps: [],
    activeStreams: [],
    usedEngines: []
  })

  const fetchMetrics = useCallback(async () => {
    try {
      setLoading(true)
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }
      
      const response = await fetch(`${orchUrl}/metrics`, { headers })
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`)
      }
      
      const data = await response.text()
      setMetrics(data)
      setError(null)
      
      // Parse and store historical data for speeds and counts only
      const parsed = parseMetrics(data)
      const now = new Date()
      
      setHistoricalData(prev => {
        const maxPoints = 60 // Keep last 60 data points (10 minutes at 10s intervals)
        
        const newTimestamps = [...prev.timestamps, now].slice(-maxPoints)
        const newUploadSpeedMbps = [...prev.uploadSpeedMbps, parsed.orch_total_upload_speed_mbps || 0].slice(-maxPoints)
        const newDownloadSpeedMbps = [...prev.downloadSpeedMbps, parsed.orch_total_download_speed_mbps || 0].slice(-maxPoints)
        const newActiveStreams = [...prev.activeStreams, parsed.orch_total_streams || 0].slice(-maxPoints)
        const newUsedEngines = [...prev.usedEngines, parsed.orch_used_engines || 0].slice(-maxPoints)
        
        return {
          timestamps: newTimestamps,
          uploadSpeedMbps: newUploadSpeedMbps,
          downloadSpeedMbps: newDownloadSpeedMbps,
          activeStreams: newActiveStreams,
          usedEngines: newUsedEngines
        }
      })
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [orchUrl, apiKey])

  useEffect(() => {
    fetchMetrics()
    const interval = setInterval(fetchMetrics, 10000) // Refresh every 10 seconds
    return () => clearInterval(interval)
  }, [fetchMetrics])

  // Parse metrics to extract key values
  const parseMetrics = (metricsText) => {
    const lines = metricsText.split('\n')
    const parsed = {}
    
    lines.forEach(line => {
      if (line.startsWith('#') || !line.trim()) return
      
      const match = line.match(/^(\w+)(?:{.*?})?\s+([\d.]+)/)
      if (match) {
        const [, name, value] = match
        if (!parsed[name]) {
          parsed[name] = parseFloat(value)
        } else {
          parsed[name] += parseFloat(value)
        }
      }
    })
    
    return parsed
  }

  const parsedMetrics = metrics ? parseMetrics(metrics) : {}

  // Format bytes to MB
  const formatMB = (bytes) => {
    return (bytes / (1024 * 1024)).toFixed(2)
  }

  // Create chart data
  const createChartData = (label, data, borderColor, backgroundColor) => ({
    labels: historicalData.timestamps.map(ts => ts.toLocaleTimeString()),
    datasets: [{
      label,
      data,
      borderColor,
      backgroundColor,
      tension: 0.3,
      fill: true
    }]
  })

  const createDualChartData = (label1, data1, label2, data2, color1, color2) => ({
    labels: historicalData.timestamps.map(ts => ts.toLocaleTimeString()),
    datasets: [
      {
        label: label1,
        data: data1,
        borderColor: color1.border,
        backgroundColor: color1.bg,
        tension: 0.3,
        fill: true,
        yAxisID: 'y'
      },
      {
        label: label2,
        data: data2,
        borderColor: color2.border,
        backgroundColor: color2.bg,
        tension: 0.3,
        fill: true,
        yAxisID: 'y'
      }
    ]
  })

  const chartOptions = (title, yAxisLabel) => ({
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
        text: title,
      },
    },
    scales: {
      y: {
        type: 'linear',
        display: true,
        position: 'left',
        title: {
          display: true,
          text: yAxisLabel,
        },
        beginAtZero: true
      }
    },
  })

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Metrics</h1>
          <p className="text-muted-foreground mt-1">Real-time metrics and statistics</p>
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Key Metrics Summary */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-5">
        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Downloaded</CardTitle>
            <ArrowDown className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatMB(parsedMetrics.orch_total_downloaded_bytes || 0)} MB</div>
            <p className="text-xs text-muted-foreground mt-1">All-time total</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Total Uploaded</CardTitle>
            <ArrowUp className="h-4 w-4 text-green-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{formatMB(parsedMetrics.orch_total_uploaded_bytes || 0)} MB</div>
            <p className="text-xs text-muted-foreground mt-1">All-time total</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Download Speed</CardTitle>
            <ArrowDown className="h-4 w-4 text-blue-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{(parsedMetrics.orch_total_download_speed_mbps || 0).toFixed(2)} MB/s</div>
            <p className="text-xs text-muted-foreground mt-1">Current speed</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Upload Speed</CardTitle>
            <ArrowUp className="h-4 w-4 text-green-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{(parsedMetrics.orch_total_upload_speed_mbps || 0).toFixed(2)} MB/s</div>
            <p className="text-xs text-muted-foreground mt-1">Current speed</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
            <CardTitle className="text-sm font-medium">Active Streams</CardTitle>
            <Activity className="h-4 w-4 text-purple-500" />
          </CardHeader>
          <CardContent>
            <div className="text-2xl font-bold">{parsedMetrics.orch_total_streams || 0}</div>
            <p className="text-xs text-muted-foreground mt-1">{parsedMetrics.orch_used_engines || 0} engines in use</p>
          </CardContent>
        </Card>
      </div>

      {/* Charts */}
      <div className="grid gap-6 md:grid-cols-2">
        {/* Transfer Speeds Chart */}
        <Card>
          <CardHeader>
            <CardTitle>Transfer Speeds (MB/s)</CardTitle>
          </CardHeader>
          <CardContent>
            <div style={{ height: '300px' }}>
              {loading && historicalData.timestamps.length === 0 ? (
                <div className="flex items-center justify-center h-full">
                  <p className="text-muted-foreground">Loading chart data...</p>
                </div>
              ) : (
                <Line
                  data={createDualChartData(
                    'Download Speed',
                    historicalData.downloadSpeedMbps,
                    'Upload Speed',
                    historicalData.uploadSpeedMbps,
                    { border: 'rgb(59, 130, 246)', bg: 'rgba(59, 130, 246, 0.1)' },
                    { border: 'rgb(34, 197, 94)', bg: 'rgba(34, 197, 94, 0.1)' }
                  )}
                  options={chartOptions('Transfer Speeds Over Time', 'MB/s')}
                />
              )}
            </div>
          </CardContent>
        </Card>

        {/* Active Streams Chart */}
        <Card>
          <CardHeader>
            <CardTitle>Active Streams</CardTitle>
          </CardHeader>
          <CardContent>
            <div style={{ height: '300px' }}>
              {loading && historicalData.timestamps.length === 0 ? (
                <div className="flex items-center justify-center h-full">
                  <p className="text-muted-foreground">Loading chart data...</p>
                </div>
              ) : (
                <Line
                  data={createChartData(
                    'Active Streams',
                    historicalData.activeStreams,
                    'rgb(168, 85, 247)',
                    'rgba(168, 85, 247, 0.1)'
                  )}
                  options={chartOptions('Active Streams Over Time', 'Count')}
                />
              )}
            </div>
          </CardContent>
        </Card>

        {/* Used Engines Chart */}
        <Card>
          <CardHeader>
            <CardTitle>Engines in Use</CardTitle>
          </CardHeader>
          <CardContent>
            <div style={{ height: '300px' }}>
              {loading && historicalData.timestamps.length === 0 ? (
                <div className="flex items-center justify-center h-full">
                  <p className="text-muted-foreground">Loading chart data...</p>
                </div>
              ) : (
                <Line
                  data={createChartData(
                    'Used Engines',
                    historicalData.usedEngines,
                    'rgb(234, 179, 8)',
                    'rgba(234, 179, 8, 0.1)'
                  )}
                  options={chartOptions('Engines in Use Over Time', 'Count')}
                />
              )}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Raw Metrics */}
      <Card>
        <CardHeader>
          <CardTitle>Raw Prometheus Metrics</CardTitle>
        </CardHeader>
        <CardContent>
          {loading && !metrics ? (
            <p className="text-muted-foreground">Loading metrics...</p>
          ) : (
            <pre className="overflow-x-auto rounded-lg bg-muted p-4 text-xs max-h-96">
              {metrics || 'No metrics available'}
            </pre>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
