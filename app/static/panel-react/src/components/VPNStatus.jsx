import React from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { ShieldCheck, CheckCircle, XCircle, AlertTriangle } from 'lucide-react'
import { formatTime } from '../utils/formatters'

function formatDuration(seconds) {
  if (!seconds) return '0s'
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.floor(seconds % 60)
  if (minutes === 0) return `${remainingSeconds}s`
  return `${minutes}m ${remainingSeconds}s`
}

function SingleVPNDisplay({ vpnData, label, emergencyMode }) {
  const isHealthy = vpnData.connected
  const HealthIcon = isHealthy ? CheckCircle : XCircle
  
  // Check if this VPN is in emergency (failed)
  const isEmergencyFailed = emergencyMode?.active && emergencyMode?.failed_vpn === vpnData.container_name

  return (
    <div>
      {label && (
        <h3 className="text-xl font-semibold mb-3">{label}</h3>
      )}
      
      {/* Emergency mode alert for this specific VPN */}
      {isEmergencyFailed && (
        <Alert variant="destructive" className="mb-4">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Emergency Mode - VPN Failed</AlertTitle>
          <AlertDescription>
            <p className="text-sm">
              This VPN has failed and is currently unavailable. All engines assigned to this VPN have been stopped.
            </p>
            <p className="text-sm mt-2">
              Duration: {formatDuration(emergencyMode.duration_seconds)}
            </p>
            {emergencyMode.entered_at && (
              <p className="text-xs text-muted-foreground mt-1">
                Started at: {formatTime(emergencyMode.entered_at)}
              </p>
            )}
          </AlertDescription>
        </Alert>
      )}
      
      <div className="flex gap-2 mb-4">
        <Badge variant={isHealthy ? "success" : "destructive"} className="flex items-center gap-1">
          <HealthIcon className="h-3 w-3" />
          {isHealthy ? 'Healthy' : 'Unhealthy'}
        </Badge>
        <Badge variant={vpnData.connected ? "success" : "destructive"}>
          {vpnData.connected ? 'Connected' : 'Disconnected'}
        </Badge>
        {isEmergencyFailed && (
          <Badge variant="destructive" className="flex items-center gap-1 font-bold">
            <AlertTriangle className="h-3 w-3" />
            EMERGENCY
          </Badge>
        )}
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <p className="text-xs text-muted-foreground">Container</p>
          <p className="text-sm font-medium">{vpnData.container_name || 'N/A'}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Public IP</p>
          <p className="text-sm font-medium font-mono">{vpnData.public_ip || 'N/A'}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Forwarded Port</p>
          <p className="text-sm font-medium">{vpnData.forwarded_port || 'N/A'}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Last Check</p>
          <p className="text-sm font-medium">{formatTime(vpnData.last_check_at)}</p>
        </div>
        {vpnData.provider && (
          <div>
            <p className="text-xs text-muted-foreground">Provider</p>
            <p className="text-sm font-medium capitalize">{vpnData.provider}</p>
          </div>
        )}
        {vpnData.country && (
          <div>
            <p className="text-xs text-muted-foreground">Country</p>
            <p className="text-sm font-medium">{vpnData.country}</p>
          </div>
        )}
        {vpnData.city && (
          <div>
            <p className="text-xs text-muted-foreground">City</p>
            <p className="text-sm font-medium">{vpnData.city}</p>
          </div>
        )}
      </div>
    </div>
  )
}

function VPNStatus({ vpnStatus }) {
  const isRedundantMode = vpnStatus.mode === 'redundant'
  const overallHealthy = vpnStatus.connected
  const OverallHealthIcon = overallHealthy ? CheckCircle : XCircle
  const emergencyMode = vpnStatus.emergency_mode

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <ShieldCheck className="h-10 w-10 text-primary" />
          <div>
            <CardTitle className="text-2xl">
              VPN Status {isRedundantMode && '(Redundant Mode)'}
            </CardTitle>
            <div className="flex gap-2 mt-2">
              <Badge variant={overallHealthy ? "success" : "destructive"} className="flex items-center gap-1">
                <OverallHealthIcon className="h-3 w-3" />
                {overallHealthy ? 'Healthy' : 'Unhealthy'}
              </Badge>
              <Badge variant={vpnStatus.connected ? "success" : "destructive"}>
                {vpnStatus.connected ? 'Connected' : 'Disconnected'}
              </Badge>
              {emergencyMode?.active && (
                <Badge variant="destructive" className="flex items-center gap-1 font-bold">
                  <AlertTriangle className="h-3 w-3" />
                  EMERGENCY MODE
                </Badge>
              )}
            </div>
          </div>
        </div>
      </CardHeader>

      <CardContent>
        {/* Overall emergency mode alert for redundant mode */}
        {isRedundantMode && emergencyMode?.active && (
          <Alert variant="warning" className="mb-6">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>System in Emergency Mode</AlertTitle>
            <AlertDescription>
              <p className="text-sm">
                Operating with reduced capacity on a single VPN due to VPN failure.
                System will automatically restore full capacity once the failed VPN recovers.
              </p>
              <div className="mt-2 flex gap-4 flex-wrap text-sm">
                <div>
                  <strong>Failed VPN:</strong> {emergencyMode.failed_vpn || 'N/A'}
                </div>
                <div>
                  <strong>Healthy VPN:</strong> {emergencyMode.healthy_vpn || 'N/A'}
                </div>
                <div>
                  <strong>Duration:</strong> {formatDuration(emergencyMode.duration_seconds)}
                </div>
              </div>
            </AlertDescription>
          </Alert>
        )}

        {isRedundantMode ? (
          <div className="space-y-6">
            {/* VPN 1 */}
            {vpnStatus.vpn1 && (
              <>
                <SingleVPNDisplay vpnData={vpnStatus.vpn1} label="VPN 1" emergencyMode={emergencyMode} />
                {vpnStatus.vpn2 && <div className="border-t my-6" />}
              </>
            )}
            
            {/* VPN 2 */}
            {vpnStatus.vpn2 && (
              <SingleVPNDisplay vpnData={vpnStatus.vpn2} label="VPN 2" emergencyMode={emergencyMode} />
            )}
          </div>
        ) : (
          /* Single VPN mode - show simple view */
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <p className="text-xs text-muted-foreground">Container</p>
              <p className="text-sm font-medium">{vpnStatus.container || 'N/A'}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Public IP</p>
              <p className="text-sm font-medium font-mono">{vpnStatus.public_ip || 'N/A'}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Forwarded Port</p>
              <p className="text-sm font-medium">{vpnStatus.forwarded_port || 'N/A'}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Last Check</p>
              <p className="text-sm font-medium">{formatTime(vpnStatus.last_check_at)}</p>
            </div>
            {vpnStatus.provider && (
              <div>
                <p className="text-xs text-muted-foreground">Provider</p>
                <p className="text-sm font-medium capitalize">{vpnStatus.provider}</p>
              </div>
            )}
            {vpnStatus.country && (
              <div>
                <p className="text-xs text-muted-foreground">Country</p>
                <p className="text-sm font-medium">{vpnStatus.country}</p>
              </div>
            )}
            {vpnStatus.city && (
              <div>
                <p className="text-xs text-muted-foreground">City</p>
                <p className="text-sm font-medium">{vpnStatus.city}</p>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export default VPNStatus
