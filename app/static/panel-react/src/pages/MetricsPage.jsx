import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { AlertCircle } from 'lucide-react'

export function MetricsPage({ apiKey, orchUrl }) {
  const [metrics, setMetrics] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

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
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [orchUrl, apiKey])

  useEffect(() => {
    fetchMetrics()
    const interval = setInterval(fetchMetrics, 30000)
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Metrics</h1>
          <p className="text-muted-foreground mt-1">Prometheus metrics and system statistics</p>
        </div>
      </div>

      {error && (
        <Alert variant="destructive">
          <AlertCircle className="h-4 w-4" />
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      )}

      {/* Key Metrics Summary */}
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">Active Streams</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-bold">{parsedMetrics.orch_streams_active || 0}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">Streams Started</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-bold">{parsedMetrics.orch_events_started_total || 0}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">Streams Ended</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-bold">{parsedMetrics.orch_events_ended_total || 0}</p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="text-sm font-medium text-muted-foreground">Collect Errors</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-3xl font-bold text-red-600 dark:text-red-400">{parsedMetrics.orch_collect_errors_total || 0}</p>
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
            <pre className="overflow-x-auto rounded-lg bg-muted p-4 text-xs">
              {metrics || 'No metrics available'}
            </pre>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
