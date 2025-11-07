import React from 'react'
import {
  Card,
  CardContent,
  Typography,
  Box,
  Chip,
  Grid,
  Divider
} from '@mui/material'
import VpnLockIcon from '@mui/icons-material/VpnLock'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import { formatTime } from '../utils/formatters'

function SingleVPNDisplay({ vpnData, label }) {
  const isHealthy = vpnData.connected
  const HealthIcon = isHealthy ? CheckCircleIcon : ErrorIcon
  const healthColor = isHealthy ? 'success' : 'error'

  return (
    <Box>
      {label && (
        <Typography variant="h6" component="h3" sx={{ mb: 2, fontWeight: 600 }}>
          {label}
        </Typography>
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
            </Box>
          </Box>
        </Box>

        {isRedundantMode ? (
          <>
            {/* VPN 1 */}
            {vpnStatus.vpn1 && (
              <>
                <SingleVPNDisplay vpnData={vpnStatus.vpn1} label="VPN 1" />
                {vpnStatus.vpn2 && <Divider sx={{ my: 3 }} />}
              </>
            )}
            
            {/* VPN 2 */}
            {vpnStatus.vpn2 && (
              <SingleVPNDisplay vpnData={vpnStatus.vpn2} label="VPN 2" />
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
