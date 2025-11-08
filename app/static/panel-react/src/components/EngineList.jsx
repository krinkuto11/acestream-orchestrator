import React from 'react'
import {
  Card,
  CardContent,
  Typography,
  Box,
  Chip,
  IconButton,
  Divider,
  Grid,
  Alert
} from '@mui/material'
import DeleteIcon from '@mui/icons-material/Delete'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import WarningIcon from '@mui/icons-material/Warning'
import { timeAgo, formatTime } from '../utils/formatters'

function EngineCard({ engine, onDelete, showVpnLabel = false }) {
  const healthColors = {
    healthy: 'success',
    unhealthy: 'error',
    unknown: 'default'
  }
  
  const healthStatus = engine.health_status || 'unknown'
  const healthColor = healthColors[healthStatus] || 'default'

  return (
    <Card sx={{ mb: 1.5, '&:hover': { bgcolor: 'action.hover' } }}>
      <CardContent sx={{ py: 1.5, '&:last-child': { pb: 1.5 } }}>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1 }}>
          <Box>
            <Typography variant="subtitle1" component="div" sx={{ fontWeight: 600, lineHeight: 1.3 }}>
              {engine.container_name || engine.container_id.slice(0, 12)}
              {engine.forwarded && (
                <Chip
                  label="FORWARDED"
                  color="primary"
                  size="small"
                  sx={{ ml: 1, fontWeight: 'bold', height: 20 }}
                />
              )}
              {showVpnLabel && engine.vpn_container && (
                <Chip
                  label={engine.vpn_container}
                  color="info"
                  size="small"
                  variant="outlined"
                  sx={{ ml: 1, height: 20, fontSize: '0.7rem' }}
                />
              )}
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ fontSize: '0.8rem' }}>
              {engine.host}:{engine.port}
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', gap: 0.5, alignItems: 'center' }}>
            <Chip
              icon={<FiberManualRecordIcon sx={{ fontSize: 12 }} />}
              label={healthStatus.toUpperCase()}
              color={healthColor}
              size="small"
              sx={{ height: 24, fontSize: '0.7rem' }}
            />
            <IconButton
              onClick={() => onDelete(engine.container_id)}
              color="error"
              size="small"
              sx={{ p: 0.5 }}
            >
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Box>
        </Box>

        <Grid container spacing={1.5}>
          <Grid item xs={6}>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.7rem' }}>
              Active Streams
            </Typography>
            <Typography variant="body2" sx={{ fontSize: '0.85rem' }}>
              {engine.streams.length}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.7rem' }}>
              Last Used
            </Typography>
            <Typography variant="body2" sx={{ fontSize: '0.85rem' }}>
              {timeAgo(engine.last_stream_usage)}
            </Typography>
          </Grid>
          {engine.last_health_check && (
            <>
              <Grid item xs={12}>
                <Divider sx={{ my: 0 }} />
              </Grid>
              <Grid item xs={12}>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.7rem' }}>
                  Last Health Check
                </Typography>
                <Typography variant="body2" sx={{ fontSize: '0.85rem' }}>
                  {formatTime(engine.last_health_check)}
                </Typography>
              </Grid>
            </>
          )}
        </Grid>
      </CardContent>
    </Card>
  )
}

function VPNEngineGroup({ vpnName, engines, onDeleteEngine, emergencyMode }) {
  const isEmergencyFailed = emergencyMode?.active && emergencyMode?.failed_vpn === vpnName
  
  return (
    <Box sx={{ mb: 3 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
        <Typography variant="h6" component="h3" sx={{ fontWeight: 600 }}>
          {vpnName}
        </Typography>
        <Chip
          label={`${engines.length} ${engines.length === 1 ? 'engine' : 'engines'}`}
          size="small"
          color="primary"
          variant="outlined"
        />
        {isEmergencyFailed && (
          <Chip
            icon={<WarningIcon />}
            label="FAILED"
            size="small"
            color="error"
            sx={{ fontWeight: 'bold' }}
          />
        )}
      </Box>
      
      {isEmergencyFailed && (
        <Alert severity="error" icon={<WarningIcon />} sx={{ mb: 2 }}>
          This VPN has failed. All engines assigned to this VPN have been stopped during emergency mode.
        </Alert>
      )}
      
      {engines.length === 0 ? (
        <Card>
          <CardContent>
            <Typography color="text.secondary">
              {isEmergencyFailed ? 'No engines (stopped due to VPN failure)' : 'No engines assigned'}
            </Typography>
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
    </Box>
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
      <Box>
        <Typography variant="h5" component="h2" gutterBottom sx={{ fontWeight: 600, mb: 3 }}>
          Engines ({engines.length})
        </Typography>
        
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
      </Box>
    )
  }
  
  // Single VPN mode or no VPN - show all engines in a simple list
  return (
    <Box>
      <Typography variant="h5" component="h2" gutterBottom sx={{ fontWeight: 600 }}>
        Engines ({engines.length})
      </Typography>
      {engines.length === 0 ? (
        <Card>
          <CardContent>
            <Typography color="text.secondary">No engines available</Typography>
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
    </Box>
  )
}

export default EngineList
