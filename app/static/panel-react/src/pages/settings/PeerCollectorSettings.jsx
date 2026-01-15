import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Label } from '@/components/ui/label'
import { Input } from '@/components/ui/input'
import { Switch } from '@/components/ui/switch'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Loader2, CheckCircle2, XCircle, AlertCircle } from 'lucide-react'

export function PeerCollectorSettings({ apiKey, orchUrl }) {
  const [enabled, setEnabled] = useState(false)
  const [collectorUrl, setCollectorUrl] = useState('http://gluetun:8080')
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState(null)
  const [healthStatus, setHealthStatus] = useState(null)
  const [checkingHealth, setCheckingHealth] = useState(false)

  // Fetch current config
  useEffect(() => {
    fetchConfig()
  }, [orchUrl, apiKey])

  const fetchConfig = async () => {
    setLoading(true)
    try {
      const headers = {}
      if (apiKey) {
        headers['X-API-KEY'] = apiKey
      }

      const response = await fetch(`${orchUrl}/config/runtime`, { headers })
      if (response.ok) {
        const data = await response.json()
        setEnabled(data.PEER_COLLECTOR_ENABLED || false)
        setCollectorUrl(data.PEER_COLLECTOR_URL || 'http://gluetun:8080')
      }
    } catch (err) {
      console.error('Failed to fetch peer collector config:', err)
    } finally {
      setLoading(false)
    }
  }

  const checkHealth = async () => {
    if (!collectorUrl) {
      setHealthStatus({ status: 'error', message: 'Please enter a collector URL first' })
      return
    }

    setCheckingHealth(true)
    setHealthStatus(null)

    try {
      const headers = {}
      if (apiKey) {
        headers['X-API-KEY'] = apiKey
      }

      const response = await fetch(`${orchUrl}/peer-collector/health`, { 
        headers,
        method: 'POST',
        body: JSON.stringify({ collector_url: collectorUrl }),
        headers: {
          ...headers,
          'Content-Type': 'application/json'
        }
      })
      
      if (response.ok) {
        const data = await response.json()
        setHealthStatus({
          status: 'success',
          message: `Peer collector is healthy! (libtorrent: ${data.libtorrent_available ? 'available' : 'unavailable'}, redis: ${data.redis_available ? 'available' : 'unavailable'})`
        })
      } else {
        setHealthStatus({
          status: 'error',
          message: 'Peer collector is not responding or unhealthy'
        })
      }
    } catch (err) {
      setHealthStatus({
        status: 'error',
        message: `Failed to connect to peer collector: ${err.message}`
      })
    } finally {
      setCheckingHealth(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    setMessage(null)

    try {
      const headers = {
        'Content-Type': 'application/json'
      }
      if (apiKey) {
        headers['X-API-KEY'] = apiKey
      }

      const response = await fetch(`${orchUrl}/config/runtime`, {
        method: 'PATCH',
        headers,
        body: JSON.stringify({
          PEER_COLLECTOR_ENABLED: enabled,
          PEER_COLLECTOR_URL: collectorUrl
        })
      })

      if (response.ok) {
        setMessage({ type: 'success', text: 'Peer collector settings saved successfully!' })
        // Refresh config to confirm
        await fetchConfig()
      } else {
        const errorData = await response.json().catch(() => ({}))
        setMessage({ 
          type: 'error', 
          text: `Failed to save settings: ${errorData.detail || response.statusText}` 
        })
      }
    } catch (err) {
      setMessage({ type: 'error', text: `Error saving settings: ${err.message}` })
    } finally {
      setSaving(false)
    }
  }

  if (loading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Peer Collector</CardTitle>
          <CardDescription>Configure peer statistics collection via microservice</CardDescription>
        </CardHeader>
        <CardContent>
          <div className="flex items-center justify-center py-8">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </div>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle>Peer Collector Microservice</CardTitle>
        <CardDescription>
          Configure peer statistics collection via an external microservice running inside the Gluetun VPN container.
          This allows the orchestrator to remain outside the VPN while still collecting torrent peer data.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        {message && (
          <Alert variant={message.type === 'error' ? 'destructive' : 'default'}>
            {message.type === 'success' ? (
              <CheckCircle2 className="h-4 w-4" />
            ) : (
              <XCircle className="h-4 w-4" />
            )}
            <AlertDescription>{message.text}</AlertDescription>
          </Alert>
        )}

        <div className="space-y-4">
          <div className="flex items-center justify-between space-x-4">
            <div className="space-y-0.5">
              <Label htmlFor="peer-collector-enabled">Enable Peer Collection</Label>
              <p className="text-sm text-muted-foreground">
                Collect torrent peer statistics via the microservice
              </p>
            </div>
            <Switch
              id="peer-collector-enabled"
              checked={enabled}
              onCheckedChange={setEnabled}
            />
          </div>

          <div className="space-y-2">
            <Label htmlFor="collector-url">Peer Collector URL</Label>
            <Input
              id="collector-url"
              type="text"
              value={collectorUrl}
              onChange={(e) => setCollectorUrl(e.target.value)}
              placeholder="http://gluetun:8080"
              disabled={!enabled}
            />
            <p className="text-sm text-muted-foreground">
              URL of the peer collector microservice. Use <code className="text-xs bg-muted px-1 py-0.5 rounded">http://gluetun:8080</code> when 
              using <code className="text-xs bg-muted px-1 py-0.5 rounded">network_mode: service:gluetun</code> in docker-compose.
            </p>
          </div>

          {healthStatus && (
            <Alert variant={healthStatus.status === 'error' ? 'destructive' : 'default'}>
              {healthStatus.status === 'success' ? (
                <CheckCircle2 className="h-4 w-4" />
              ) : (
                <AlertCircle className="h-4 w-4" />
              )}
              <AlertDescription>{healthStatus.message}</AlertDescription>
            </Alert>
          )}

          <div className="flex gap-2">
            <Button
              onClick={checkHealth}
              disabled={!enabled || checkingHealth}
              variant="outline"
            >
              {checkingHealth ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Checking...
                </>
              ) : (
                'Test Connection'
              )}
            </Button>

            <Button onClick={handleSave} disabled={saving}>
              {saving ? (
                <>
                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  Saving...
                </>
              ) : (
                'Save Settings'
              )}
            </Button>
          </div>
        </div>

        <div className="pt-4 border-t">
          <h4 className="text-sm font-medium mb-2">About Peer Collection</h4>
          <ul className="text-sm text-muted-foreground space-y-1 list-disc list-inside">
            <li>When enabled, detailed peer lists will be available in the Streams page</li>
            <li>When disabled, only peer counts will be shown (from stream stats)</li>
            <li>The microservice uses libtorrent to connect to BitTorrent swarms</li>
            <li>Peer data is enriched with geolocation information (country, city, ISP)</li>
            <li>Results are cached for 30 seconds to reduce API calls</li>
          </ul>
        </div>
      </CardContent>
    </Card>
  )
}
