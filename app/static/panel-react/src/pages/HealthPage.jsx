import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { CheckCircle, XCircle, AlertTriangle, Activity } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function HealthPage({ apiKey, orchUrl }) {
  const [healthStatus, setHealthStatus] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)

  const fetchHealthStatus = useCallback(async () => {
    try {
      setLoading(true)
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }
      
      const response = await fetch(`${orchUrl}/health/status`, { headers })
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`)
      }
      
      const data = await response.json()
      setHealthStatus(data)
      setError(null)
    } catch (err) {
      setError(err.message || String(err))
    } finally {
      setLoading(false)
    }
  }, [orchUrl, apiKey])

  useEffect(() => {
    fetchHealthStatus()
    const interval = setInterval(fetchHealthStatus, 10000)
    return () => clearInterval(interval)
  }, [fetchHealthStatus])

  const resetCircuitBreaker = async (type = null) => {
    try {
      const headers = {}
      if (apiKey) {
        headers['Authorization'] = `Bearer ${apiKey}`
      }

      const url = type 
        ? `${orchUrl}/health/circuit-breaker/reset?operation_type=${type}`
        : `${orchUrl}/health/circuit-breaker/reset`

      const response = await fetch(url, { method: 'POST', headers })
      if (!response.ok) {
        throw new Error(`${response.status} ${response.statusText}`)
      }

      await fetchHealthStatus()
    } catch (err) {
      setError(`Failed to reset circuit breaker: ${err.message}`)
    }
  }

  if (loading && !healthStatus) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Health & Monitoring</h1>
          <p className="text-muted-foreground">System health and circuit breaker status</p>
        </div>
        <Card>
          <CardContent className="pt-6">
            <p className="text-muted-foreground">Loading health status...</p>
          </CardContent>
        </Card>
      </div>
    )
  }

  if (error && !healthStatus) {
    return (
      <div className="space-y-6">
        <div>
          <h1 className="text-3xl font-bold">Health & Monitoring</h1>
          <p className="text-muted-foreground">System health and circuit breaker status</p>
        </div>
        <Alert variant="destructive">
          <XCircle className="h-4 w-4" />
          <AlertTitle>Error</AlertTitle>
          <AlertDescription>{error}</AlertDescription>
        </Alert>
      </div>
    )
  }

  const circuitBreakerState = healthStatus?.circuit_breakers?.general?.state || 'unknown'
  const isHealthy = healthStatus?.healthy_engines > 0 && circuitBreakerState === 'closed'

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold">Health & Monitoring</h1>
          <p className="text-muted-foreground">System health and circuit breaker status</p>
        </div>
        <Badge variant={isHealthy ? "success" : "warning"} className="flex items-center gap-1">
          {isHealthy ? <CheckCircle className="h-3 w-3" /> : <AlertTriangle className="h-3 w-3" />}
          {isHealthy ? 'Healthy' : 'Degraded'}
        </Badge>
      </div>

      {/* Engine Health */}
      <Card>
        <CardHeader>
          <CardTitle>Engine Health</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4 md:grid-cols-3">
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">Healthy Engines</p>
              <p className="text-3xl font-bold text-green-600">{healthStatus?.healthy_engines || 0}</p>
            </div>
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">Unhealthy Engines</p>
              <p className="text-3xl font-bold text-red-600">{healthStatus?.unhealthy_engines || 0}</p>
            </div>
            <div className="space-y-2">
              <p className="text-sm text-muted-foreground">Total Checks</p>
              <p className="text-3xl font-bold">{healthStatus?.total_checks || 0}</p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Circuit Breaker Status */}
      <Card>
        <CardHeader>
          <div className="flex items-center justify-between">
            <CardTitle>Circuit Breaker</CardTitle>
            {circuitBreakerState !== 'closed' && (
              <Button 
                variant="outline" 
                size="sm"
                onClick={() => resetCircuitBreaker()}
              >
                Reset All
              </Button>
            )}
          </div>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {/* General Circuit Breaker */}
            {healthStatus?.circuit_breakers?.general && (
              <div className="flex items-center justify-between border-b pb-3">
                <div>
                  <p className="font-medium">General Operations</p>
                  <p className="text-sm text-muted-foreground">
                    Failures: {healthStatus.circuit_breakers.general.failure_count} / {healthStatus.circuit_breakers.general.failure_threshold}
                  </p>
                </div>
                <Badge 
                  variant={
                    healthStatus.circuit_breakers.general.state === 'closed' 
                      ? 'success' 
                      : healthStatus.circuit_breakers.general.state === 'half_open'
                        ? 'warning'
                        : 'destructive'
                  }
                >
                  {healthStatus.circuit_breakers.general.state}
                </Badge>
              </div>
            )}

            {/* Per-VPN Circuit Breakers */}
            {healthStatus?.circuit_breakers?.per_vpn && Object.entries(healthStatus.circuit_breakers.per_vpn).map(([vpn, status]) => (
              <div key={vpn} className="flex items-center justify-between border-b pb-3 last:border-0">
                <div>
                  <p className="font-medium">{vpn}</p>
                  <p className="text-sm text-muted-foreground">
                    Failures: {status.failure_count} / {status.failure_threshold}
                  </p>
                </div>
                <Badge 
                  variant={
                    status.state === 'closed' 
                      ? 'success' 
                      : status.state === 'half_open'
                        ? 'warning'
                        : 'destructive'
                  }
                >
                  {status.state}
                </Badge>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      {/* Recent Issues */}
      {healthStatus?.recent_issues && healthStatus.recent_issues.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>Recent Issues</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="space-y-2">
              {healthStatus.recent_issues.slice(0, 10).map((issue, index) => (
                <Alert key={index} variant="warning">
                  <AlertTriangle className="h-4 w-4" />
                  <AlertDescription className="text-sm">
                    {issue.message || issue}
                  </AlertDescription>
                </Alert>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
