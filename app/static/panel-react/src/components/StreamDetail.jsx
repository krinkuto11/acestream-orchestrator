import React, { useState, useEffect, useCallback } from 'react'
import {
  Card,
  CardContent,
  Typography,
  Box,
  Button,
  Grid,
  Divider,
  Link,
  IconButton
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import StopCircleIcon from '@mui/icons-material/StopCircle'
import DeleteIcon from '@mui/icons-material/Delete'
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

  const fetchStats = useCallback(async () => {
    if (!stream) return
    
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
  }, [stream, orchUrl, apiKey])

  useEffect(() => {
    fetchStats()
    const interval = setInterval(fetchStats, 10000) // Refresh every 10 seconds
    return () => clearInterval(interval)
  }, [fetchStats])

  const chartData = {
    labels: stats.map(s => new Date(s.ts).toLocaleTimeString()),
    datasets: [
      {
        label: 'Download (MB/s)',
        data: stats.map(s => (s.speed_down || 0) / (1024 * 1024)),
        borderColor: 'rgb(75, 192, 192)',
        backgroundColor: 'rgba(75, 192, 192, 0.2)',
        yAxisID: 'y',
      },
      {
        label: 'Upload (MB/s)',
        data: stats.map(s => (s.speed_up || 0) / (1024 * 1024)),
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
      <CardContent>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 3 }}>
          <Typography variant="h5" component="h2" sx={{ fontWeight: 600 }}>
            Stream Details
          </Typography>
          <IconButton onClick={onClose} size="small">
            <CloseIcon />
          </IconButton>
        </Box>

        <Grid container spacing={2} sx={{ mb: 3 }}>
          <Grid item xs={12} md={6}>
            <Typography variant="caption" color="text.secondary">
              Stream ID
            </Typography>
            <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
              {stream.id}
            </Typography>
          </Grid>
          <Grid item xs={12} md={6}>
            <Typography variant="caption" color="text.secondary">
              Session ID
            </Typography>
            <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
              {stream.session_id || 'N/A'}
            </Typography>
          </Grid>
          <Grid item xs={12} md={6}>
            <Typography variant="caption" color="text.secondary">
              Content Key
            </Typography>
            <Typography variant="body2" sx={{ wordBreak: 'break-all' }}>
              {stream.content_key || 'N/A'}
            </Typography>
          </Grid>
          <Grid item xs={12} md={6}>
            <Typography variant="caption" color="text.secondary">
              Content Type
            </Typography>
            <Typography variant="body2">
              {stream.content_type || 'N/A'}
            </Typography>
          </Grid>
          <Grid item xs={12} md={6}>
            <Typography variant="caption" color="text.secondary">
              Engine
            </Typography>
            <Typography variant="body2">
              {stream.container_name || stream.container_id?.slice(0, 12) || 'N/A'}
            </Typography>
          </Grid>
          <Grid item xs={12} md={6}>
            <Typography variant="caption" color="text.secondary">
              Started At
            </Typography>
            <Typography variant="body2">
              {formatTime(stream.started_at)}
            </Typography>
          </Grid>
        </Grid>

        <Box sx={{ mb: 2 }}>
          <Link href={stream.stat_url} target="_blank" rel="noopener" sx={{ mr: 2 }}>
            Statistics URL
          </Link>
          <Link href={stream.command_url} target="_blank" rel="noopener">
            Command URL
          </Link>
        </Box>

        <Divider sx={{ my: 2 }} />

        <Box sx={{ height: 300, mb: 3 }}>
          {stats.length > 0 ? (
            <Line data={chartData} options={chartOptions} />
          ) : (
            <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
              <Typography color="text.secondary">
                {loading ? 'Loading statistics...' : 'No statistics available'}
              </Typography>
            </Box>
          )}
        </Box>

        <Box sx={{ display: 'flex', gap: 2 }}>
          <Button
            variant="contained"
            color="error"
            startIcon={<StopCircleIcon />}
            onClick={() => onStopStream(stream.id, stream.container_id)}
          >
            Stop Stream
          </Button>
          <Button
            variant="outlined"
            color="error"
            startIcon={<DeleteIcon />}
            onClick={() => onDeleteEngine(stream.container_id)}
          >
            Delete Engine
          </Button>
        </Box>
      </CardContent>
    </Card>
  )
}

export default StreamDetail
