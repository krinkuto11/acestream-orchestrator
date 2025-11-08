import React from 'react'
import {
  Card,
  CardContent,
  Typography,
  Box,
  Chip,
  Grid,
  Divider,
  Alert,
  AlertTitle
} from '@mui/material'
import VpnLockIcon from '@mui/icons-material/VpnLock'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import WarningIcon from '@mui/icons-material/Warning'
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
  const HealthIcon = isHealthy ? CheckCircleIcon : ErrorIcon
  const healthColor = isHealthy ? 'success' : 'error'
  
  // Check if this VPN is in emergency (failed)
  const isEmergencyFailed = emergencyMode?.active && emergencyMode?.failed_vpn === vpnData.container_name

  return (
    <Box>
      {label && (
        <Typography variant="h6" component="h3" sx={{ mb: 2, fontWeight: 600 }}>
          {label}
        </Typography>
      )}
      
      {/* Emergency mode alert for this specific VPN */}
      {isEmergencyFailed && (
        <Alert severity="error" icon={<WarningIcon />} sx={{ mb: 2 }}>
          <AlertTitle>Emergency Mode - VPN Failed</AlertTitle>
          <Typography variant="body2">
            This VPN has failed and is currently unavailable. All engines assigned to this VPN have been stopped.
          </Typography>
          <Typography variant="body2" sx={{ mt: 1 }}>
            Duration: {formatDuration(emergencyMode.duration_seconds)}
          </Typography>
          {emergencyMode.entered_at && (
            <Typography variant="caption" color="text.secondary">
              Started at: {formatTime(emergencyMode.entered_at)}
            </Typography>
          )}
        </Alert>
      )}
      
      <Box sx={{ display: 'flex', gap: 1, mb: 2 }}>
        <Chip
          icon={<HealthIcon />}
          label={isHealthy ? 'Healthy' : 'Unhealthy'}
          color={healthColor}
          size="small"
        />
        <Chip
          label={vpnData.connected ? 'Connected' : 'Disconnected'}
          color={vpnData.connected ? 'success' : 'error'}
          size="small"
        />
        {isEmergencyFailed && (
          <Chip
            icon={<WarningIcon />}
            label="EMERGENCY"
            color="error"
            size="small"
            sx={{ fontWeight: 'bold' }}
          />
        )}
      </Box>
      <Grid container spacing={2}>
        <Grid item xs={12} sm={6} md={3}>
          <Typography variant="caption" color="text.secondary">
            Container
          </Typography>
          <Typography variant="body2">
            {vpnData.container_name || 'N/A'}
          </Typography>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Typography variant="caption" color="text.secondary">
            Health
          </Typography>
          <Typography variant="body2">
            {vpnData.health || 'unknown'}
          </Typography>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Typography variant="caption" color="text.secondary">
            Forwarded Port
          </Typography>
          <Typography variant="body2">
            {vpnData.forwarded_port || 'N/A'}
          </Typography>
        </Grid>
        <Grid item xs={12} sm={6} md={3}>
          <Typography variant="caption" color="text.secondary">
            Last Check
          </Typography>
          <Typography variant="body2">
            {formatTime(vpnData.last_check_at)}
          </Typography>
        </Grid>
      </Grid>
    </Box>
  )
}

function VPNStatus({ vpnStatus }) {
  const isRedundantMode = vpnStatus.mode === 'redundant'
  const overallHealthy = vpnStatus.connected
  const OverallHealthIcon = overallHealthy ? CheckCircleIcon : ErrorIcon
  const overallHealthColor = overallHealthy ? 'success' : 'error'
  const emergencyMode = vpnStatus.emergency_mode

  return (
    <Card>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 3 }}>
          <VpnLockIcon sx={{ fontSize: 40, color: 'primary.main' }} />
          <Box>
            <Typography variant="h5" component="h2" sx={{ fontWeight: 600 }}>
              VPN Status {isRedundantMode && '(Redundant Mode)'}
            </Typography>
            <Box sx={{ display: 'flex', gap: 1, mt: 0.5 }}>
              <Chip
                icon={<OverallHealthIcon />}
                label={overallHealthy ? 'Healthy' : 'Unhealthy'}
                color={overallHealthColor}
                size="small"
              />
              <Chip
                label={vpnStatus.connected ? 'Connected' : 'Disconnected'}
                color={vpnStatus.connected ? 'success' : 'error'}
                size="small"
              />
              {emergencyMode?.active && (
                <Chip
                  icon={<WarningIcon />}
                  label="EMERGENCY MODE"
                  color="error"
                  size="small"
                  sx={{ fontWeight: 'bold' }}
                />
              )}
            </Box>
          </Box>
        </Box>

        {/* Overall emergency mode alert for redundant mode */}
        {isRedundantMode && emergencyMode?.active && (
          <Alert severity="warning" icon={<WarningIcon />} sx={{ mb: 3 }}>
            <AlertTitle>System in Emergency Mode</AlertTitle>
            <Typography variant="body2">
              Operating with reduced capacity on a single VPN due to VPN failure.
              System will automatically restore full capacity once the failed VPN recovers.
            </Typography>
            <Box sx={{ mt: 1, display: 'flex', gap: 2, flexWrap: 'wrap' }}>
              <Typography variant="body2">
                <strong>Failed VPN:</strong> {emergencyMode.failed_vpn || 'N/A'}
              </Typography>
              <Typography variant="body2">
                <strong>Healthy VPN:</strong> {emergencyMode.healthy_vpn || 'N/A'}
              </Typography>
              <Typography variant="body2">
                <strong>Duration:</strong> {formatDuration(emergencyMode.duration_seconds)}
              </Typography>
            </Box>
          </Alert>
        )}

        {isRedundantMode ? (
          <>
            {/* VPN 1 */}
            {vpnStatus.vpn1 && (
              <>
                <SingleVPNDisplay vpnData={vpnStatus.vpn1} label="VPN 1" emergencyMode={emergencyMode} />
                {vpnStatus.vpn2 && <Divider sx={{ my: 3 }} />}
              </>
            )}
            
            {/* VPN 2 */}
            {vpnStatus.vpn2 && (
              <SingleVPNDisplay vpnData={vpnStatus.vpn2} label="VPN 2" emergencyMode={emergencyMode} />
            )}
          </>
        ) : (
          /* Single VPN mode - show simple view */
          <Grid container spacing={2}>
            <Grid item xs={12} sm={6} md={3}>
              <Typography variant="caption" color="text.secondary">
                Container
              </Typography>
              <Typography variant="body2">
                {vpnStatus.container || 'N/A'}
              </Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <Typography variant="caption" color="text.secondary">
                Public IP
              </Typography>
              <Typography variant="body2" sx={{ fontFamily: 'monospace' }}>
                {vpnStatus.public_ip || 'N/A'}
              </Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <Typography variant="caption" color="text.secondary">
                Forwarded Port
              </Typography>
              <Typography variant="body2">
                {vpnStatus.forwarded_port || 'N/A'}
              </Typography>
            </Grid>
            <Grid item xs={12} sm={6} md={3}>
              <Typography variant="caption" color="text.secondary">
                Last Check
              </Typography>
              <Typography variant="body2">
                {formatTime(vpnStatus.last_check_at)}
              </Typography>
            </Grid>
          </Grid>
        )}
      </CardContent>
    </Card>
  )
}

export default VPNStatus
