import React from 'react'
import {
  Card,
  CardContent,
  Typography,
  Box,
  Chip,
  Grid,
  Link
} from '@mui/material'
import VpnLockIcon from '@mui/icons-material/VpnLock'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import ErrorIcon from '@mui/icons-material/Error'
import { formatTime } from '../utils/formatters'

function VPNStatus({ vpnStatus }) {
  const isHealthy = vpnStatus.connected
  const HealthIcon = isHealthy ? CheckCircleIcon : ErrorIcon
  const healthColor = isHealthy ? 'success' : 'error'

  return (
    <Card>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 3 }}>
          <VpnLockIcon sx={{ fontSize: 40, color: 'primary.main' }} />
          <Box>
            <Typography variant="h5" component="h2" sx={{ fontWeight: 600 }}>
              VPN Status
            </Typography>
            <Box sx={{ display: 'flex', gap: 1, mt: 0.5 }}>
              <Chip
                icon={<HealthIcon />}
                label={isHealthy ? 'Healthy' : 'Unhealthy'}
                color={healthColor}
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
      </CardContent>
    </Card>
  )
}

export default VPNStatus
