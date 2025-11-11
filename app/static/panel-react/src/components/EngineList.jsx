import React from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { Trash2, AlertTriangle, Activity } from 'lucide-react'
import { timeAgo, formatTime } from '../utils/formatters'

function EngineCard({ engine, onDelete, showVpnLabel = false }) {
  const healthColors = {
    healthy: 'success',
    unhealthy: 'destructive',
    unknown: 'outline'
  }
  
  const healthStatus = engine.health_status || 'unknown'
  const healthVariant = healthColors[healthStatus] || 'outline'

  return (
    <Card className="mb-3 hover:bg-accent/5 transition-colors">
      <CardContent className="pt-4 pb-4">
        <div className="flex justify-between items-start mb-3">
          <div>
            <div className="font-semibold text-base flex items-center gap-2 mb-1">
              {engine.container_name || engine.container_id.slice(0, 12)}
              {engine.forwarded && (
                <Badge variant="default" className="font-bold">FORWARDED</Badge>
              )}
              {showVpnLabel && engine.vpn_container && (
                <Badge variant="outline" className="text-xs">{engine.vpn_container}</Badge>
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

        <div className="grid grid-cols-2 gap-4">
          <div>
            <p className="text-xs text-muted-foreground">Active Streams</p>
            <p className="text-sm font-medium">{engine.streams.length}</p>
          </div>
          <div>
            <p className="text-xs text-muted-foreground">Last Used</p>
            <p className="text-sm font-medium">{timeAgo(engine.last_stream_usage)}</p>
          </div>
          {engine.last_health_check && (
            <div className="col-span-2">
              <p className="text-xs text-muted-foreground">Last Health Check</p>
              <p className="text-sm font-medium">{formatTime(engine.last_health_check)}</p>
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  )
}

function VPNEngineGroup({ vpnName, engines, onDeleteEngine, emergencyMode }) {
  const isEmergencyFailed = emergencyMode?.active && emergencyMode?.failed_vpn === vpnName
  
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <h3 className="text-xl font-semibold">{vpnName}</h3>
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
          />
        ))
      )}
    </div>
  )
}

function EngineList({ engines, onDeleteEngine, vpnStatus }) {
  const isRedundantMode = vpnStatus?.mode === 'redundant'
  const emergencyMode = vpnStatus?.emergency_mode
  
  // Group engines by VPN in redundant mode
  if (isRedundantMode) {
    const vpn1Name = vpnStatus?.vpn1?.container_name
    const vpn2Name = vpnStatus?.vpn2?.container_name
    
    // Group engines by their VPN assignment
    const enginesByVpn = {
      [vpn1Name]: [],
      [vpn2Name]: []
    }
    
    engines.forEach(engine => {
      if (engine.vpn_container === vpn1Name) {
        enginesByVpn[vpn1Name].push(engine)
      } else if (engine.vpn_container === vpn2Name) {
        enginesByVpn[vpn2Name].push(engine)
      }
      // Engines without VPN assignment are ignored in redundant mode
    })
    
    return (
      <div>
        <h2 className="text-2xl font-semibold mb-6">Engines ({engines.length})</h2>
        
        {/* Side-by-side layout for redundant VPN mode */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* VPN 1 Engines */}
          {vpn1Name && (
            <VPNEngineGroup
              vpnName={vpn1Name}
              engines={enginesByVpn[vpn1Name]}
              onDeleteEngine={onDeleteEngine}
              emergencyMode={emergencyMode}
            />
          )}
          
          {/* VPN 2 Engines */}
          {vpn2Name && (
            <VPNEngineGroup
              vpnName={vpn2Name}
              engines={enginesByVpn[vpn2Name]}
              onDeleteEngine={onDeleteEngine}
              emergencyMode={emergencyMode}
            />
          )}
        </div>
      </div>
    )
  }
  
  // Single VPN mode or no VPN - show all engines in a simple list
  return (
    <div>
      <h2 className="text-2xl font-semibold mb-6">Engines ({engines.length})</h2>
      {engines.length === 0 ? (
        <Card>
          <CardContent className="pt-6 pb-6">
            <p className="text-muted-foreground">No engines available</p>
          </CardContent>
        </Card>
      ) : (
        engines.map((engine) => (
          <EngineCard
            key={engine.container_id}
            engine={engine}
            onDelete={onDeleteEngine}
            showVpnLabel={false}
          />
        ))
      )}
    </div>
  )
}

export default EngineList
