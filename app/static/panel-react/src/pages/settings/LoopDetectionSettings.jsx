import React, { useState, useEffect } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Button } from '@/components/ui/button'
import { AlertCircle, CheckCircle2 } from 'lucide-react'

export function LoopDetectionSettings({ apiKey, orchUrl }) {
  const [loopDetectionEnabled, setLoopDetectionEnabled] = useState(false)
  const [loopDetectionThresholdMinutes, setLoopDetectionThresholdMinutes] = useState(60)
  const [loopDetectionCheckIntervalSeconds, setLoopDetectionCheckIntervalSeconds] = useState(10)
  const [loopDetectionRetentionMinutes, setLoopDetectionRetentionMinutes] = useState(0)
  const [loopDetectionLoading, setLoopDetectionLoading] = useState(false)
  const [loopDetectionMessage, setLoopDetectionMessage] = useState(null)
  const [loopDetectionError, setLoopDetectionError] = useState(null)
  const [loopingStreams, setLoopingStreams] = useState([])
  const [loopingStreamsLoading, setLoopingStreamsLoading] = useState(false)

  useEffect(() => {
    fetchLoopDetectionConfig()
    fetchLoopingStreams()
  }, [orchUrl])

  const fetchLoopDetectionConfig = async () => {
    try {
      const response = await fetch(`${orchUrl}/stream-loop-detection/config`)
      if (response.ok) {
        const data = await response.json()
        setLoopDetectionEnabled(data.enabled)
        setLoopDetectionThresholdMinutes(Math.round(data.threshold_minutes))
        setLoopDetectionCheckIntervalSeconds(data.check_interval_seconds || 10)
        setLoopDetectionRetentionMinutes(data.retention_minutes || 0)
      }
    } catch (err) {
      console.error('Failed to fetch loop detection config:', err)
    }
  }

  const fetchLoopingStreams = async () => {
    setLoopingStreamsLoading(true)
    try {
      const response = await fetch(`${orchUrl}/looping-streams`)
      if (response.ok) {
        const data = await response.json()
        setLoopingStreams(Object.entries(data.streams || {}).map(([id, time]) => ({ id, time })))
      }
    } catch (err) {
      console.error('Failed to fetch looping streams:', err)
    } finally {
      setLoopingStreamsLoading(false)
    }
  }

  const removeLoopingStream = async (streamId) => {
    if (!apiKey) {
      setLoopDetectionError('API Key is required to remove streams')
      return
    }

    try {
      const response = await fetch(`${orchUrl}/looping-streams/${streamId}`, {
        method: 'DELETE',
        headers: {
          'Authorization': `Bearer ${apiKey}`
        }
      })

      if (response.ok) {
        setLoopDetectionMessage(`Stream ${streamId.substring(0, 16)}... removed from looping list`)
        await fetchLoopingStreams()
      } else {
        const errorData = await response.json()
        setLoopDetectionError(errorData.detail || 'Failed to remove stream')
      }
    } catch (err) {
      setLoopDetectionError('Failed to remove stream: ' + err.message)
    }
  }

  const clearAllLoopingStreams = async () => {
    if (!apiKey) {
      setLoopDetectionError('API Key is required to clear streams')
      return
    }

    try {
      const response = await fetch(`${orchUrl}/looping-streams/clear`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${apiKey}`
        }
      })

      if (response.ok) {
        setLoopDetectionMessage('All looping streams cleared')
        await fetchLoopingStreams()
      } else {
        const errorData = await response.json()
        setLoopDetectionError(errorData.detail || 'Failed to clear streams')
      }
    } catch (err) {
      setLoopDetectionError('Failed to clear streams: ' + err.message)
    }
  }

  const saveLoopDetectionConfig = async () => {
    if (!apiKey) {
      setLoopDetectionError('API Key is required to update settings')
      return
    }

    setLoopDetectionLoading(true)
    setLoopDetectionMessage(null)
    setLoopDetectionError(null)

    try {
      const thresholdSeconds = loopDetectionThresholdMinutes * 60
      const response = await fetch(
        `${orchUrl}/stream-loop-detection/config?enabled=${loopDetectionEnabled}&threshold_seconds=${thresholdSeconds}&check_interval_seconds=${loopDetectionCheckIntervalSeconds}&retention_minutes=${loopDetectionRetentionMinutes}`,
        {
          method: 'POST',
          headers: {
            'Authorization': `Bearer ${apiKey}`
          }
        }
      )

      if (response.ok) {
        const data = await response.json()
        setLoopDetectionMessage(data.message)
        await fetchLoopDetectionConfig()
      } else {
        const errorData = await response.json()
        setLoopDetectionError(errorData.detail || 'Failed to update configuration')
      }
    } catch (err) {
      setLoopDetectionError('Failed to save configuration: ' + err.message)
    } finally {
      setLoopDetectionLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      <Card>
        <CardHeader>
          <CardTitle>Stream Loop Detection</CardTitle>
          <CardDescription>
            Automatically stop streams that are looping (no new data being fed into the network)
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-2">
            <Label htmlFor="loop-detection-enabled">Enable Loop Detection</Label>
            <Select 
              value={loopDetectionEnabled ? "true" : "false"} 
              onValueChange={(val) => setLoopDetectionEnabled(val === "true")}
            >
              <SelectTrigger id="loop-detection-enabled">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="true">Enabled</SelectItem>
                <SelectItem value="false">Disabled</SelectItem>
              </SelectContent>
            </Select>
            <p className="text-xs text-muted-foreground">
              When enabled, streams will be automatically stopped if they fall behind live by the configured threshold
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="loop-detection-threshold">Threshold (Minutes)</Label>
            <Input
              id="loop-detection-threshold"
              type="number"
              min="1"
              value={loopDetectionThresholdMinutes}
              onChange={(e) => setLoopDetectionThresholdMinutes(parseInt(e.target.value) || 60)}
            />
            <p className="text-xs text-muted-foreground">
              Stop stream if broadcast position (live_last) is behind current time by this many minutes
              ({loopDetectionThresholdMinutes} minutes = {(loopDetectionThresholdMinutes / 60).toFixed(2)} hours)
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="loop-detection-check-interval">Check Interval (Seconds)</Label>
            <Input
              id="loop-detection-check-interval"
              type="number"
              min="5"
              value={loopDetectionCheckIntervalSeconds}
              onChange={(e) => setLoopDetectionCheckIntervalSeconds(parseInt(e.target.value) || 10)}
            />
            <p className="text-xs text-muted-foreground">
              How often to check streams for loop detection (minimum: 5 seconds)
            </p>
          </div>

          <div className="space-y-2">
            <Label htmlFor="loop-detection-retention">Retention Time (Minutes)</Label>
            <Input
              id="loop-detection-retention"
              type="number"
              min="0"
              value={loopDetectionRetentionMinutes}
              onChange={(e) => setLoopDetectionRetentionMinutes(parseInt(e.target.value) || 0)}
            />
            <p className="text-xs text-muted-foreground">
              How long to keep looping stream IDs in the list. Set to 0 for indefinite retention.
              {loopDetectionRetentionMinutes === 0 
                ? ' (Currently: Indefinite - streams remain until manually removed)' 
                : ` (Currently: ${loopDetectionRetentionMinutes} minutes = ${(loopDetectionRetentionMinutes / 60).toFixed(2)} hours)`}
            </p>
          </div>

          <div className="pt-4">
            <Button 
              onClick={saveLoopDetectionConfig}
              disabled={loopDetectionLoading || !apiKey}
            >
              {loopDetectionLoading ? 'Saving...' : 'Save Loop Detection Settings'}
            </Button>
            {!apiKey && (
              <p className="text-xs text-destructive mt-2">
                API Key is required to update settings
              </p>
            )}
          </div>

          {loopDetectionMessage && (
            <div className="flex items-center gap-2 p-3 bg-success/10 border border-success rounded-md">
              <CheckCircle2 className="h-4 w-4 text-success" />
              <span className="text-sm text-success">{loopDetectionMessage}</span>
            </div>
          )}

          {loopDetectionError && (
            <div className="flex items-center gap-2 p-3 bg-destructive/10 border border-destructive rounded-md">
              <AlertCircle className="h-4 w-4 text-destructive" />
              <span className="text-sm text-destructive">{loopDetectionError}</span>
            </div>
          )}

          {/* Looping Streams List */}
          <div className="pt-4 border-t">
            <div className="flex items-center justify-between mb-3">
              <div>
                <h3 className="text-sm font-semibold">Looping Streams</h3>
                <p className="text-xs text-muted-foreground">
                  Streams currently marked as looping. Acexy will reject playback attempts for these streams.
                </p>
              </div>
              {loopingStreams.length > 0 && (
                <Button 
                  variant="outline" 
                  size="sm"
                  onClick={clearAllLoopingStreams}
                  disabled={!apiKey}
                >
                  Clear All
                </Button>
              )}
            </div>
            
            {loopingStreamsLoading ? (
              <div className="text-center py-4 text-muted-foreground">
                Loading looping streams...
              </div>
            ) : loopingStreams.length === 0 ? (
              <div className="text-center py-4 text-muted-foreground">
                No looping streams detected
              </div>
            ) : (
              <div className="space-y-2">
                {loopingStreams.map(({ id, time }) => (
                  <div key={id} className="flex items-center justify-between p-3 bg-muted/50 rounded-md">
                    <div className="flex-1">
                      <div className="font-mono text-sm">{id}</div>
                      <div className="text-xs text-muted-foreground">
                        Detected: {new Date(time).toLocaleString()}
                      </div>
                    </div>
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => removeLoopingStream(id)}
                      disabled={!apiKey}
                      className="ml-2"
                    >
                      Remove
                    </Button>
                  </div>
                ))}
              </div>
            )}
          </div>
        </CardContent>
      </Card>
    </div>
  )
}
