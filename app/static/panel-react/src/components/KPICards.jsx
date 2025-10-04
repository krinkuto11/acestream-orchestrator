import React from 'react'
import { Grid, Card, CardContent, Typography, Box } from '@mui/material'
import DnsIcon from '@mui/icons-material/Dns'
import PlayCircleIcon from '@mui/icons-material/PlayCircle'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import VpnLockIcon from '@mui/icons-material/VpnLock'
import UpdateIcon from '@mui/icons-material/Update'

function KPICard({ icon: Icon, value, label, color = 'primary' }) {
  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
          <Icon sx={{ fontSize: 40, color: `${color}.main` }} />
          <Box>
            <Typography variant="h4" component="div" sx={{ fontWeight: 600 }}>
              {value}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {label}
            </Typography>
          </Box>
        </Box>
      </CardContent>
    </Card>
  )
}

function KPICards({ totalEngines, activeStreams, healthyEngines, vpnStatus, lastUpdate }) {
  const vpnStatusText = vpnStatus.enabled 
    ? (vpnStatus.connected ? 'Connected' : 'Disconnected')
    : 'Disabled'
  
  const vpnColor = vpnStatus.enabled 
    ? (vpnStatus.connected ? 'success' : 'error')
    : 'default'

  return (
    <Grid container spacing={2}>
      <Grid item xs={12} sm={6} md={2.4}>
        <KPICard icon={DnsIcon} value={totalEngines} label="Engines" color="primary" />
      </Grid>
      <Grid item xs={12} sm={6} md={2.4}>
        <KPICard icon={PlayCircleIcon} value={activeStreams} label="Active Streams" color="secondary" />
      </Grid>
      <Grid item xs={12} sm={6} md={2.4}>
        <KPICard icon={CheckCircleIcon} value={healthyEngines} label="Healthy Engines" color="success" />
      </Grid>
      <Grid item xs={12} sm={6} md={2.4}>
        <KPICard icon={VpnLockIcon} value={vpnStatusText} label="VPN Status" color={vpnColor} />
      </Grid>
      <Grid item xs={12} sm={12} md={2.4}>
        <KPICard 
          icon={UpdateIcon} 
          value={lastUpdate ? lastUpdate.toLocaleTimeString() : 'Never'} 
          label="Last Update" 
          color="info" 
        />
      </Grid>
    </Grid>
  )
}

export default KPICards
