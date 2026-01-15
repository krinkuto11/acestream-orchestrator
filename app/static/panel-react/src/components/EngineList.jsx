import React, { useState, useEffect } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Trash2, AlertTriangle, Activity, ChevronDown, ChevronUp, Cpu, MemoryStick, Network, HardDrive, LayoutGrid, Table2 } from 'lucide-react'
import { timeAgo, formatTime, formatBytes } from '../utils/formatters'
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from '@/components/ui/collapsible'
import { Progress } from '@/components/ui/progress'
import { useLocalStorage } from '../hooks/useLocalStorage'
import EngineTableView from './EngineTableView'

// Simple in-memory cache for engine stats to prevent flickering when switching tabs
const statsCache = new Map()
const STATS_CACHE_TTL = 3000 // 3 seconds

function EngineCard({ engine, onDelete, showVpnLabel = false, orchUrl }) {
  const [isOpen, setIsOpen] = useState(false)
  const [stats, setStats] = useState(() => {
    // Initialize with cached stats if available
    const cached = statsCache.get(engine.container_id)
    return cached?.data || null
  })
  
  const healthColors = {
    healthy: 'success',
    unhealthy: 'destructive',
    unknown: 'outline'
  }
  
  const healthStatus = engine.health_status || 'unknown'
  const healthVariant = healthColors[healthStatus] || 'outline'

  // Fetch Docker stats continuously (not just when expanded)
  useEffect(() => {
    const fetchStats = async () => {
      // Check cache first
      const cached = statsCache.get(engine.container_id)
      const now = Date.now()
      
      if (cached && (now - cached.timestamp) < STATS_CACHE_TTL) {
        // Use cached data
        setStats(cached.data)
        return
      }
      
      try {
        const response = await fetch(`${orchUrl}/engines/${engine.container_id}/stats`)
        if (response.ok) {
          const data = await response.json()
          setStats(data)
          // Update cache
          statsCache.set(engine.container_id, {
            data,
            timestamp: now
          })
        }
      } catch (err) {
        console.error('Failed to fetch engine stats:', err)
      }
    }
    
    // Fetch immediately
    fetchStats()
    
    // Refresh stats every 3 seconds
    const interval = setInterval(fetchStats, 3000)
    
    return () => clearInterval(interval)
  }, [engine.container_id, orchUrl])

  return (
    <Card className="mb-3 hover:bg-accent/5 transition-colors">
      <CardContent className="pt-4 pb-4">
        <div className="flex justify-between items-start mb-3">
          <div className="flex-1">
            <div className="font-semibold text-base flex items-center gap-2 mb-1">
              {engine.container_name || engine.container_id.slice(0, 12)}
              {engine.forwarded && (
                <Badge variant="default" className="font-bold">FORWARDED</Badge>
              )}
              {showVpnLabel && engine.vpn_container && (
                <Badge variant="outline" className="text-xs">{engine.vpn_container}</Badge>
              )}
              {engine.is_custom_variant && (
                <Badge variant="secondary" className="text-xs">
                  {engine.template_name ? `Custom Variant: ${engine.template_name}` : 'Custom Variant'}
                </Badge>
              )}
              {engine.engine_variant && !engine.is_custom_variant && (
                <Badge variant="secondary" className="text-xs">{engine.engine_variant}</Badge>
              )}
            </div>
            <p className="text-sm text-muted-foreground">
              {engine.host}:{engine.port}
            </p>
          </div>
          <div className="flex gap-2 items-center">
            <Badge variant={healthVariant} className="flex items-center gap-1">
              <Activity className="h-3 w-3" />
              {healthStatus.toUpperCase()}
            </Badge>
            <Button
              variant="ghost"
              size="icon"
              onClick={() => onDelete(engine.container_id)}
              className="h-8 w-8 text-destructive hover:text-destructive hover:bg-destructive/10"
            >
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
        </div>

        <div className="grid grid-cols-2 gap-4 mb-3">
          <div>
            <p className="text-xs text-muted-foreground">Active Streams</p>
            <p className="text-sm font-medium">{engine.streams.length}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Last Used</p>
            <p className="text-sm font-medium">{timeAgo(engine.last_stream_usage)}</p>
          </div>
          {engine.last_health_check && (
            <div>
              <p className="text-xs text-muted-foreground">Last Health Check</p>
              <p className="text-sm font-medium">{formatTime(engine.last_health_check)}</p>
            </div>
          )}
        </div>

        {/* Docker Stats Section - Always visible in main block */}
        {stats && (
          <div className="border-t pt-3 mb-3">
            <div className="space-y-2">
              {/* CPU */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-muted-foreground flex items-center gap-1">
                    <Cpu className="h-3 w-3" />
                    CPU
                  </span>
                  <span className="text-xs font-medium">{stats.cpu_percent.toFixed(1)}%</span>
                </div>
                <Progress value={Math.min(stats.cpu_percent, 100)} className="h-1.5" />
              </div>
              
              {/* Memory */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <span className="text-xs text-muted-foreground flex items-center gap-1">
                    <MemoryStick className="h-3 w-3" />
                    Memory
                  </span>
                  <span className="text-xs font-medium">
                    {formatBytes(stats.memory_usage)} ({stats.memory_percent.toFixed(1)}%)
                  </span>
                </div>
                <Progress value={stats.memory_percent} className="h-1.5" />
              </div>
            </div>
          </div>
        )}

        {/* Collapsible details section */}
        <Collapsible open={isOpen} onOpenChange={setIsOpen}>
          <CollapsibleTrigger asChild>
            <Button
              variant="ghost"
              size="sm"
              className="w-full flex items-center justify-center gap-2 text-xs"
            >
              {isOpen ? (
                <>
                  <ChevronUp className="h-3 w-3" />
                  Hide Details
                </>
              ) : (
                <>
                  <ChevronDown className="h-3 w-3" />
                  Show Details
                </>
              )}
            </Button>
          </CollapsibleTrigger>
          <CollapsibleContent className="pt-3">
            <div className="border-t pt-3">
              <div className="grid grid-cols-2 gap-4 mb-4">
                {engine.platform && (
                  <div>
                    <p className="text-xs text-muted-foreground">Platform</p>
                    <p className="text-sm font-medium">{engine.platform}</p>
                  </div>
                )}
                {engine.version && (
                  <div>
                    <p className="text-xs text-muted-foreground">AceStream Version</p>
                    <p className="text-sm font-medium">{engine.version}</p>
                  </div>
                )}
                {engine.forwarded && engine.forwarded_port && (
                  <div>
                    <p className="text-xs text-muted-foreground">Forwarded Port</p>
                    <p className="text-sm font-medium font-mono">{engine.forwarded_port}</p>
                  </div>
                )}
              </div>
              
              {/* Extended Docker Stats Section - Network and Block I/O */}
              {stats && (
                <div className="border-t pt-3">
                  <h4 className="text-sm font-semibold mb-3 flex items-center gap-2">
                    <Activity className="h-4 w-4" />
                    Extended Stats
                  </h4>
                  
                  <div className="space-y-3">
                    {/* Network I/O */}
                    <div>
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-muted-foreground flex items-center gap-1">
                          <Network className="h-3 w-3" />
                          Network I/O
                        </span>
                        <span className="text-xs font-medium">
                          ↓ {formatBytes(stats.network_rx_bytes)} / ↑ {formatBytes(stats.network_tx_bytes)}
                        </span>
                      </div>
                    </div>
                    
                    {/* Block I/O */}
                    <div>
                      <div className="flex items-center justify-between">
                        <span className="text-xs text-muted-foreground flex items-center gap-1">
                          <HardDrive className="h-3 w-3" />
                          Block I/O
                        </span>
                        <span className="text-xs font-medium">
                          Read: {formatBytes(stats.block_read_bytes)} / Write: {formatBytes(stats.block_write_bytes)}
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </CollapsibleContent>
        </Collapsible>
      </CardContent>
    </Card>
  )
}

function VPNEngineGroup({ vpnName, engines, onDeleteEngine, emergencyMode, orchUrl }) {
  const isEmergencyFailed = emergencyMode?.active && emergencyMode?.failed_vpn === vpnName
  
  return (
    <div className="mb-6">
      <div className="flex items-center gap-2 mb-3">
        <h3 className="text-xl font-semibold tracking-tight">{vpnName}</h3>
        <Badge variant="outline">
          {engines.length} {engines.length === 1 ? 'engine' : 'engines'}
        </Badge>
        {isEmergencyFailed && (
          <Badge variant="destructive" className="flex items-center gap-1 font-bold">
            <AlertTriangle className="h-3 w-3" />
            FAILED
          </Badge>
        )}
      </div>
      
      {isEmergencyFailed && (
        <Alert variant="destructive" className="mb-4">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>VPN Failed</AlertTitle>
          <AlertDescription>
            This VPN has failed. All engines assigned to this VPN have been stopped during emergency mode.
          </AlertDescription>
        </Alert>
      )}
      
      {engines.length === 0 ? (
        <Card>
          <CardContent className="pt-6 pb-6">
            <p className="text-muted-foreground">
              {isEmergencyFailed ? 'No engines (stopped due to VPN failure)' : 'No engines assigned'}
            </p>
          </CardContent>
        </Card>
      ) : (
        engines.map((engine) => (
          <EngineCard
            key={engine.container_id}
            engine={engine}
            onDelete={onDeleteEngine}
            showVpnLabel={false}
            orchUrl={orchUrl}
          />
        ))
      )}
    </div>
  )
}

function EngineList({ engines, onDeleteEngine, vpnStatus, orchUrl }) {
  const isRedundantMode = vpnStatus?.mode === 'redundant'
  const emergencyMode = vpnStatus?.emergency_mode
  
  // Persist view mode in localStorage
  const [viewMode, setViewMode] = useLocalStorage('engine_view_mode', 'cards')
  
  // State for engines with metrics (for table view)
  const [enginesWithMetrics, setEnginesWithMetrics] = useState([])
  
  // Fetch engines with metrics when in table view
  useEffect(() => {
    if (viewMode !== 'table') return
    
    const fetchEnginesWithMetrics = async () => {
      try {
        const response = await fetch(`${orchUrl}/engines/with-metrics`)
        if (response.ok) {
          const data = await response.json()
          setEnginesWithMetrics(data)
        }
      } catch (err) {
        console.error('Failed to fetch engines with metrics:', err)
        // Fallback to regular engines data
        setEnginesWithMetrics(engines)
      }
    }
    
    // Fetch immediately
    fetchEnginesWithMetrics()
    
    // Refresh every 5 seconds when in table view
    const interval = setInterval(fetchEnginesWithMetrics, 5000)
    
    return () => clearInterval(interval)
  }, [viewMode, orchUrl, engines])
  
  // Use enginesWithMetrics for table view, regular engines for card view
  const displayEngines = viewMode === 'table' ? enginesWithMetrics : engines
  
  // Group engines by VPN in redundant mode
  if (isRedundantMode) {
    const vpn1Name = vpnStatus?.vpn1?.container_name
    const vpn2Name = vpnStatus?.vpn2?.container_name
    
    // Group engines by their VPN assignment
    const enginesByVpn = {
      [vpn1Name]: [],
      [vpn2Name]: []
    }
    
    displayEngines.forEach(engine => {
      if (engine.vpn_container === vpn1Name) {
        enginesByVpn[vpn1Name].push(engine)
      } else if (engine.vpn_container === vpn2Name) {
        enginesByVpn[vpn2Name].push(engine)
      }
      // Engines without VPN assignment are ignored in redundant mode
    })
    
    return (
      <div>
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-2xl font-semibold tracking-tight">Engines ({displayEngines.length})</h2>
          
          {/* View Toggle Button */}
          <div className="flex gap-2">
            <Button
              variant={viewMode === 'cards' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setViewMode('cards')}
              className="flex items-center gap-2"
            >
              <LayoutGrid className="h-4 w-4" />
              Cards
            </Button>
            <Button
              variant={viewMode === 'table' ? 'default' : 'outline'}
              size="sm"
              onClick={() => setViewMode('table')}
              className="flex items-center gap-2"
            >
              <Table2 className="h-4 w-4" />
              Table
            </Button>
          </div>
        </div>
        
        {viewMode === 'table' ? (
          // Table view - show all engines in a single table (no VPN grouping in table view)
          <EngineTableView
            engines={displayEngines}
            onDeleteEngine={onDeleteEngine}
            showVpnLabel={true}
            orchUrl={orchUrl}
          />
        ) : (
          // Card view - Grid layout for side-by-side VPN groups in redundant mode
          <div className="grid gap-6 md:grid-cols-2">
            {/* VPN 1 Engines */}
            {vpn1Name && (
              <VPNEngineGroup
                vpnName={vpn1Name}
                engines={enginesByVpn[vpn1Name]}
                onDeleteEngine={onDeleteEngine}
                emergencyMode={emergencyMode}
                orchUrl={orchUrl}
              />
            )}
            
            {/* VPN 2 Engines */}
            {vpn2Name && (
              <VPNEngineGroup
                vpnName={vpn2Name}
                engines={enginesByVpn[vpn2Name]}
                onDeleteEngine={onDeleteEngine}
                emergencyMode={emergencyMode}
                orchUrl={orchUrl}
              />
            )}
          </div>
        )}
      </div>
    )
  }
  
  // Single VPN mode or no VPN - show all engines in a simple list or table
  return (
    <div>
      <div className="flex items-center justify-between mb-6">
        <h2 className="text-2xl font-semibold tracking-tight">Engines ({displayEngines.length})</h2>
        
        {/* View Toggle Button */}
        <div className="flex gap-2">
          <Button
            variant={viewMode === 'cards' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setViewMode('cards')}
            className="flex items-center gap-2"
          >
            <LayoutGrid className="h-4 w-4" />
            Cards
          </Button>
          <Button
            variant={viewMode === 'table' ? 'default' : 'outline'}
            size="sm"
            onClick={() => setViewMode('table')}
            className="flex items-center gap-2"
          >
            <Table2 className="h-4 w-4" />
            Table
          </Button>
        </div>
      </div>
      
      {viewMode === 'table' ? (
        <EngineTableView
          engines={displayEngines}
          onDeleteEngine={onDeleteEngine}
          showVpnLabel={false}
          orchUrl={orchUrl}
        />
      ) : (
        <>
          {displayEngines.length === 0 ? (
            <Card>
              <CardContent className="pt-6 pb-6">
                <p className="text-muted-foreground">No engines available</p>
              </CardContent>
            </Card>
          ) : (
            displayEngines.map((engine) => (
              <EngineCard
                key={engine.container_id}
                engine={engine}
                onDelete={onDeleteEngine}
                showVpnLabel={false}
                orchUrl={orchUrl}
              />
            ))
          )}
        </>
      )}
    </div>
  )
}

export default EngineList
