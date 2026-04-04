import React, { useState, useEffect, useMemo } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { 
  Activity,
  ChevronDown, 
  ChevronUp, 
  Trash2,
  Cpu,
  MemoryStick,
  Network,
  HardDrive,
  Users,
  Download,
  Upload,
  ArrowUpDown,
  ArrowUp,
  ArrowDown,
  Check,
  Timer,
  Zap,
} from 'lucide-react'
import { timeAgo, formatTime, formatBytes, formatBytesPerSecond } from '../utils/formatters'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'

const normalizeLifecycle = (value) => {
  return String(value || '').trim().toLowerCase() === 'draining' ? 'draining' : 'active'
}

const collectDrainingVpnContainers = (vpnStatus) => {
  const draining = new Set()
  if (!vpnStatus) return draining

  const candidates = [
    ...(Array.isArray(vpnStatus?.vpn_nodes) ? vpnStatus.vpn_nodes : []),
    ...(Array.isArray(vpnStatus?.nodes) ? vpnStatus.nodes : []),
    vpnStatus?.vpn1,
    vpnStatus?.vpn2,
  ].filter(Boolean)

  for (const node of candidates) {
    const lifecycle = normalizeLifecycle(node?.lifecycle)
    if (lifecycle !== 'draining') continue
    const name = String(node?.container_name || node?.container || node?.name || '').trim()
    if (name) {
      draining.add(name)
    }
  }

  return draining
}

const resolveEngineLifecycle = (engine, drainingVpnContainers) => {
  const directLifecycle = normalizeLifecycle(
    engine?.lifecycle
    ?? engine?.vpn_lifecycle
    ?? engine?.labels?.['acestream.lifecycle']
    ?? null,
  )

  if (directLifecycle === 'draining') return 'draining'

  const vpnContainer = String(engine?.vpn_container || '').trim()
  if (vpnContainer && drainingVpnContainers.has(vpnContainer)) {
    return 'draining'
  }

  return 'active'
}

function EngineTableRow({ engine, onDelete, showVpnLabel = false, vpnMode = null, drainingVpnContainers }) {
  const [isExpanded, setIsExpanded] = useState(false)
  const [stats, setStats] = useState(engine?.docker_stats || null)
  
  const healthColors = {
    healthy: 'success',
    unhealthy: 'destructive',
    unknown: 'outline'
  }
  
  const healthStatus = engine.health_status || 'unknown'
  const healthVariant = healthColors[healthStatus] || 'outline'
  const lifecycle = resolveEngineLifecycle(engine, drainingVpnContainers)
  const isDraining = lifecycle === 'draining'

  // Stats are delivered by the global SSE snapshot payload.
  useEffect(() => {
    setStats(engine?.docker_stats || null)
  }, [engine?.docker_stats])

  // Format CPU and RAM as text
  const cpuText = stats ? `${stats.cpu_percent.toFixed(1)}%` : 'N/A'
  const ramText = stats ? `${formatBytes(stats.memory_usage)} (${stats.memory_percent.toFixed(1)}%)` : 'N/A'
  
  // Get engine variant name
  const variantName = engine.is_custom_variant 
    ? (engine.template_name || 'Custom')
    : (engine.engine_variant || 'Default')

  return (
    <>
      <TableRow className="hover:bg-accent/5">
        <TableCell className="w-[40px]">
          <Button
            variant="ghost"
            size="sm"
            className="h-8 w-8 p-0 border border-white/20 hover:bg-white/10 mx-auto"
            onClick={() => setIsExpanded(!isExpanded)}
          >
            {isExpanded ? <ChevronUp className="h-4 w-4 text-white" /> : <ChevronDown className="h-4 w-4 text-white" />}
          </Button>
        </TableCell>
        <TableCell className="font-medium text-center">
          <span className="text-sm text-white truncate max-w-[12rem] block" title={engine.container_name || engine.container_id}>
            {engine.container_name || engine.container_id.slice(0, 12)}
          </span>
        </TableCell>
        <TableCell className="text-center">
          <span className="text-sm text-white">{engine.host}:{engine.port}</span>
        </TableCell>
        <TableCell className="text-center">
          <span className="text-sm text-white">{engine.api_port || '—'}</span>
        </TableCell>
        <TableCell className="text-center">
          <Badge variant={healthVariant} className="flex items-center gap-1 w-fit mx-auto">
            <Activity className="h-3 w-3" />
            <span className="text-white">{healthStatus.toUpperCase()}</span>
          </Badge>
        </TableCell>
        <TableCell className="text-center">
          <Badge
            variant={isDraining ? 'warning' : 'success'}
            className="mx-auto flex w-fit items-center gap-1"
          >
            {isDraining ? <Timer className="h-3 w-3" /> : <Check className="h-3 w-3" />}
            {isDraining ? 'Draining' : 'Active'}
          </Badge>
        </TableCell>
        <TableCell className="text-center">
          {engine.forwarded ? (
            <Badge variant="outline" className="mx-auto flex w-fit items-center gap-1 border-amber-400/70 bg-amber-500/10 text-amber-300">
              <Zap className="h-3 w-3" />
              Forwarded Leader
            </Badge>
          ) : (
            <span className="text-sm text-muted-foreground">Worker</span>
          )}
        </TableCell>
        {vpnMode && (
          <TableCell className="text-center">
            <span className="text-sm text-white">{engine.vpn_container || '—'}</span>
          </TableCell>
        )}
        <TableCell className="text-center">
          <span className="text-sm text-white">{variantName}</span>
        </TableCell>
        <TableCell className="text-center">
          <span className="text-sm text-white">{cpuText}</span>
        </TableCell>
        <TableCell className="text-center">
          <span className="text-sm text-white">{ramText}</span>
        </TableCell>
        <TableCell className="text-center">
          <span className="text-sm text-white">{engine.stream_count || engine.streams?.length || 0}</span>
        </TableCell>
        <TableCell className="text-center">
          {engine.total_peers !== undefined ? (
            <div className="flex items-center justify-center gap-1">
              <Users className="h-3 w-3 text-primary" />
              <span className="text-sm font-semibold text-primary">{engine.total_peers}</span>
            </div>
          ) : (
            <span className="text-sm text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell className="text-center">
          {engine.total_speed_down !== undefined && engine.total_speed_down > 0 ? (
            <span className="text-sm font-semibold text-success">
              {formatBytesPerSecond((engine.total_speed_down || 0) * 1024)}
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell className="text-center">
          {engine.total_speed_up !== undefined && engine.total_speed_up > 0 ? (
            <span className="text-sm font-semibold text-destructive">
              {formatBytesPerSecond((engine.total_speed_up || 0) * 1024)}
            </span>
          ) : (
            <span className="text-sm text-muted-foreground">—</span>
          )}
        </TableCell>
        <TableCell className="text-center">
          <span className="text-sm text-white">{timeAgo(engine.last_stream_usage)}</span>
        </TableCell>
        <TableCell className="text-center">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => onDelete(engine.container_id)}
            className="h-8 w-8 text-destructive hover:text-destructive hover:bg-destructive/10"
          >
            <Trash2 className="h-4 w-4" />
          </Button>
        </TableCell>
      </TableRow>
      {isExpanded && (
        <TableRow>
          <TableCell colSpan={vpnMode ? 17 : 16} className="p-6 bg-muted/50">
            <div className="space-y-6">
              {/* Engine Details */}
              <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                <div>
                  <p className="text-xs text-muted-foreground">Container ID</p>
                  <p className="text-sm font-medium text-foreground break-all">{engine.container_id}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Container Name</p>
                  <p className="text-sm font-medium text-foreground">{engine.container_name || 'N/A'}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Address</p>
                  <p className="text-sm font-medium text-foreground">{engine.host}:{engine.port}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">API Port</p>
                  <p className="text-sm font-medium text-foreground">{engine.api_port || 'N/A'}</p>
                </div>
                {engine.platform && (
                  <div>
                    <p className="text-xs text-muted-foreground">Platform</p>
                    <p className="text-sm font-medium text-foreground">{engine.platform}</p>
                  </div>
                )}
                {engine.version && (
                  <div>
                    <p className="text-xs text-muted-foreground">AceStream Version</p>
                    <p className="text-sm font-medium text-foreground">{engine.version}</p>
                  </div>
                )}
                {engine.forwarded && engine.forwarded_port && (
                  <div>
                    <p className="text-xs text-muted-foreground">Forwarded Port</p>
                    <p className="text-sm font-medium font-mono text-foreground">{engine.forwarded_port}</p>
                  </div>
                )}
                <div>
                  <p className="text-xs text-muted-foreground">First Seen</p>
                  <p className="text-sm font-medium text-foreground">{formatTime(engine.first_seen)}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Last Seen</p>
                  <p className="text-sm font-medium text-foreground">{formatTime(engine.last_seen)}</p>
                </div>
                {engine.last_health_check && (
                  <div>
                    <p className="text-xs text-muted-foreground">Last Health Check</p>
                    <p className="text-sm font-medium text-foreground">{formatTime(engine.last_health_check)}</p>
                  </div>
                )}
              </div>
              
              {/* Extended Docker Stats Section */}
              {stats && (
                <div className="border-t pt-3">
                  <h4 className="text-sm font-semibold mb-3 flex items-center gap-2">
                    <Activity className="h-4 w-4" />
                    Extended Stats
                  </h4>
                  
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
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
          </TableCell>
        </TableRow>
      )}
    </>
  )
}

function EngineTableView({ engines, onDeleteEngine, showVpnLabel = false, vpnMode = null, vpnStatus = null }) {
  // State for sorting
  const [sortColumn, setSortColumn] = useState(null)
  const [sortDirection, setSortDirection] = useState('asc')
  const drainingVpnContainers = useMemo(() => collectDrainingVpnContainers(vpnStatus), [vpnStatus])
  
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
  
  // Sort engines based on current sort settings
  const sortEngines = (enginesList) => {
    if (!sortColumn) return enginesList
    
    return [...enginesList].sort((a, b) => {
      let aVal = a[sortColumn]
      let bVal = b[sortColumn]
      
      // Handle special cases
      if (sortColumn === 'last_stream_usage' || sortColumn === 'last_seen' || sortColumn === 'first_seen') {
        aVal = aVal ? new Date(aVal).getTime() : 0
        bVal = bVal ? new Date(bVal).getTime() : 0
      } else if (sortColumn === 'stream_count' || sortColumn === 'total_peers' || 
                 sortColumn === 'total_speed_down' || sortColumn === 'total_speed_up') {
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
  
  const sortedEngines = sortEngines(engines)

  return (
    <div className="space-y-6">
      <div className="rounded-md border overflow-x-auto">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-[40px] text-center"></TableHead>
              <TableHead 
                className="cursor-pointer select-none text-center"
                onClick={() => handleSort('container_name')}
              >
                Engine <SortIcon column="container_name" />
              </TableHead>
              <TableHead 
                className="cursor-pointer select-none text-center"
                onClick={() => handleSort('host')}
              >
                Address <SortIcon column="host" />
              </TableHead>
              <TableHead className="text-center">
                API Port
              </TableHead>
              <TableHead 
                className="cursor-pointer select-none text-center"
                onClick={() => handleSort('health_status')}
              >
                Health <SortIcon column="health_status" />
              </TableHead>
              <TableHead 
                className="text-center"
              >
                Lifecycle
              </TableHead>
              <TableHead 
                className="cursor-pointer select-none text-center"
                onClick={() => handleSort('forwarded')}
              >
                Role <SortIcon column="forwarded" />
              </TableHead>
              {vpnMode && (
                <TableHead className="text-center">
                  VPN
                </TableHead>
              )}
              <TableHead className="text-center">
                Variant
              </TableHead>
              <TableHead className="text-center">
                CPU
              </TableHead>
              <TableHead className="text-center">
                RAM
              </TableHead>
              <TableHead 
                className="cursor-pointer select-none text-center"
                onClick={() => handleSort('stream_count')}
              >
                Streams <SortIcon column="stream_count" />
              </TableHead>
              <TableHead 
                className="text-center cursor-pointer select-none"
                onClick={() => handleSort('total_peers')}
              >
                Peers <SortIcon column="total_peers" />
              </TableHead>
              <TableHead 
                className="text-center cursor-pointer select-none"
                onClick={() => handleSort('total_speed_down')}
              >
                Download <SortIcon column="total_speed_down" />
              </TableHead>
              <TableHead 
                className="text-center cursor-pointer select-none"
                onClick={() => handleSort('total_speed_up')}
              >
                Upload <SortIcon column="total_speed_up" />
              </TableHead>
              <TableHead 
                className="cursor-pointer select-none text-center"
                onClick={() => handleSort('last_stream_usage')}
              >
                Last Used <SortIcon column="last_stream_usage" />
              </TableHead>
              <TableHead className="text-center">
                Actions
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {sortedEngines.length === 0 ? (
              <TableRow>
                <TableCell colSpan={vpnMode ? 17 : 16} className="text-center py-8 text-muted-foreground">
                  No engines available
                </TableCell>
              </TableRow>
            ) : (
              sortedEngines.map((engine) => (
                <EngineTableRow
                  key={engine.container_id}
                  engine={engine}
                  onDelete={onDeleteEngine}
                  showVpnLabel={showVpnLabel}
                  vpnMode={vpnMode}
                  drainingVpnContainers={drainingVpnContainers}
                />
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  )
}

export default EngineTableView
