import React, { useState, useEffect, useCallback } from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { RefreshCw, Filter } from 'lucide-react'
import { formatDistanceToNow } from 'date-fns'

const EVENT_TYPE_COLORS = {
  engine: 'bg-blue-500',
  stream: 'bg-green-500',
  vpn: 'bg-purple-500',
  health: 'bg-yellow-500',
  system: 'bg-gray-500'
}

const EVENT_TYPE_LABELS = {
  engine: 'Engine',
  stream: 'Stream',
  vpn: 'VPN',
  health: 'Health',
  system: 'System'
}

export function EventsPage({ orchUrl, apiKey, maxEventsDisplay = 100 }) {
  const [events, setEvents] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [filterType, setFilterType] = useState('all')
  const [displayLimit, setDisplayLimit] = useState(maxEventsDisplay)

  // Sync displayLimit with maxEventsDisplay prop changes
  useEffect(() => {
    setDisplayLimit(maxEventsDisplay)
  }, [maxEventsDisplay])

  const fetchEvents = useCallback(async () => {
    try {
      setLoading(true)
      const headers = apiKey ? { Authorization: `Bearer ${apiKey}` } : {}
      
      // Fetch events with filter
      const eventsUrl = filterType === 'all' 
        ? `${orchUrl}/events?limit=${displayLimit}`
        : `${orchUrl}/events?event_type=${filterType}&limit=${displayLimit}`
      
      const [eventsRes, statsRes] = await Promise.all([
        fetch(eventsUrl, { headers }),
        fetch(`${orchUrl}/events/stats`, { headers })
      ])

      if (!eventsRes.ok || !statsRes.ok) {
        throw new Error('Failed to fetch events')
      }

      const eventsData = await eventsRes.json()
      const statsData = await statsRes.json()

      setEvents(eventsData)
      setStats(statsData)
      setError(null)
    } catch (err) {
      console.error('Error fetching events:', err)
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }, [orchUrl, apiKey, filterType, displayLimit])

  useEffect(() => {
    fetchEvents()
    // Refresh events every 5 seconds
    const interval = setInterval(fetchEvents, 5000)
    return () => clearInterval(interval)
  }, [fetchEvents])

  const formatTimestamp = (timestamp) => {
    try {
      const date = new Date(timestamp)
      return formatDistanceToNow(date, { addSuffix: true })
    } catch {
      return timestamp
    }
  }

  const getEventIcon = (eventType) => {
    const icons = {
      engine: '⚙️',
      stream: '📺',
      vpn: '🔒',
      health: '💊',
      system: '⚡'
    }
    return icons[eventType] || '📝'
  }

  const getCategoryBadgeVariant = (category) => {
    const variants = {
      created: 'default',
      deleted: 'destructive',
      started: 'default',
      ended: 'secondary',
      failed: 'destructive',
      recovered: 'default',
      connected: 'default',
      disconnected: 'destructive',
      warning: 'destructive',
      scaling: 'secondary'
    }
    return variants[category] || 'outline'
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Event Log</h1>
          <p className="text-muted-foreground mt-1">
            Track significant application events and operations
          </p>
        </div>
        <Button onClick={fetchEvents} variant="outline" size="sm">
          <RefreshCw className="h-4 w-4 mr-2" />
          Refresh
        </Button>
      </div>

      {/* Statistics Cards */}
      {stats && (
        <div className="grid gap-4 md:grid-cols-6">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
              <CardTitle className="text-sm font-medium">Total Events</CardTitle>
            </CardHeader>
            <CardContent>
              <div className="text-2xl font-bold">{stats.total}</div>
            </CardContent>
          </Card>
          {Object.entries(stats.by_type || {}).map(([type, count]) => (
            <Card key={type}>
              <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-2">
                <CardTitle className="text-sm font-medium">
                  {getEventIcon(type)} {EVENT_TYPE_LABELS[type]}
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold">{count}</div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      {/* Filters */}
      <Card>
        <CardContent className="pt-6">
          <div className="flex flex-wrap gap-4 items-end">
            <div className="flex-1 min-w-[200px]">
              <Label htmlFor="event-type">Event Type</Label>
              <Select value={filterType} onValueChange={setFilterType}>
                <SelectTrigger id="event-type">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="all">All Events</SelectItem>
                  <SelectItem value="engine">🔧 Engine</SelectItem>
                  <SelectItem value="stream">📺 Stream</SelectItem>
                  <SelectItem value="vpn">🔒 VPN</SelectItem>
                  <SelectItem value="health">💊 Health</SelectItem>
                  <SelectItem value="system">⚡ System</SelectItem>
                </SelectContent>
              </Select>
            </div>
            
            <div className="flex-1 min-w-[200px]">
              <Label htmlFor="display-limit">Display Limit</Label>
              <Select value={displayLimit.toString()} onValueChange={(val) => setDisplayLimit(Number(val))}>
                <SelectTrigger id="display-limit">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="50">50 events</SelectItem>
                  <SelectItem value="100">100 events</SelectItem>
                  <SelectItem value="200">200 events</SelectItem>
                  <SelectItem value="500">500 events</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Events List */}
      <Card>
        <CardHeader>
          <CardTitle>Recent Events</CardTitle>
        </CardHeader>
        <CardContent>
          {loading && !events.length && (
            <div className="text-center py-8 text-muted-foreground">
              Loading events...
            </div>
          )}
          
          {error && (
            <div className="text-center py-8 text-destructive">
              Error: {error}
            </div>
          )}
          
          {!loading && !error && events.length === 0 && (
            <div className="text-center py-8 text-muted-foreground">
              No events found
            </div>
          )}

          {events.length > 0 && (
            <div className="space-y-3">
              {events.map((event) => (
                <div
                  key={event.id}
                  className="flex items-start gap-4 p-4 rounded-lg border bg-card hover:bg-accent/50 transition-colors"
                >
                  {/* Event Type Indicator */}
                  <div className="flex-shrink-0">
                    <div className={`w-10 h-10 rounded-full ${EVENT_TYPE_COLORS[event.event_type]} flex items-center justify-center text-white text-xl`}>
                      {getEventIcon(event.event_type)}
                    </div>
                  </div>

                  {/* Event Details */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <Badge variant={getCategoryBadgeVariant(event.category)}>
                        {event.category}
                      </Badge>
                      <Badge variant="outline">
                        {EVENT_TYPE_LABELS[event.event_type]}
                      </Badge>
                      <span className="text-xs text-muted-foreground ml-auto">
                        {formatTimestamp(event.timestamp)}
                      </span>
                    </div>
                    
                    <p className="text-sm font-medium mb-1">{event.message}</p>
                    
                    {/* Additional Details */}
                    {event.details && Object.keys(event.details).length > 0 && (
                      <details className="text-xs text-muted-foreground mt-2">
                        <summary className="cursor-pointer hover:text-foreground">
                          Show details
                        </summary>
                        <pre className="mt-2 p-2 bg-muted rounded text-xs overflow-x-auto">
                          {JSON.stringify(event.details, null, 2)}
                        </pre>
                      </details>
                    )}
                    
                    {/* Container/Stream IDs */}
                    <div className="flex gap-3 mt-2 text-xs text-muted-foreground">
                      {event.container_id && (
                        <span>Container: {event.container_id.substring(0, 12)}</span>
                      )}
                      {event.stream_id && (
                        <span>Stream: {event.stream_id.substring(0, 16)}...</span>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  )
}
