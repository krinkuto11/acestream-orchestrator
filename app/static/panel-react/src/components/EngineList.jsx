import React from 'react'
import {
  Card,
  CardContent,
  Typography,
  Box,
  Chip,
  IconButton,
  Divider,
  Grid
} from '@mui/material'
import DeleteIcon from '@mui/icons-material/Delete'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import { timeAgo, formatTime } from '../utils/formatters'

function EngineCard({ engine, onDelete }) {
  const healthColors = {
    healthy: 'success',
    unhealthy: 'error',
    unknown: 'default'
  }
  
  const healthStatus = engine.health_status || 'unknown'
  const healthColor = healthColors[healthStatus] || 'default'

  return (
    <Card sx={{ mb: 2, '&:hover': { bgcolor: 'action.hover' } }}>
      <CardContent>
        <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
          <Box>
            <Typography variant="h6" component="div" sx={{ fontWeight: 600 }}>
              {engine.container_name || engine.container_id.slice(0, 12)}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {engine.host}:{engine.port}
            </Typography>
          </Box>
          <Box sx={{ display: 'flex', gap: 1, alignItems: 'center' }}>
            <Chip
              icon={<FiberManualRecordIcon />}
              label={healthStatus.toUpperCase()}
              color={healthColor}
              size="small"
            />
            <IconButton
              onClick={() => onDelete(engine.container_id)}
              color="error"
              size="small"
            >
              <DeleteIcon />
            </IconButton>
          </Box>
        </Box>

        <Grid container spacing={2}>
          <Grid item xs={6}>
            <Typography variant="caption" color="text.secondary">
              Active Streams
            </Typography>
            <Typography variant="body2">
              {engine.streams.length}
            </Typography>
          </Grid>
          <Grid item xs={6}>
            <Typography variant="caption" color="text.secondary">
              Last Used
            </Typography>
            <Typography variant="body2">
              {timeAgo(engine.last_used_at)}
            </Typography>
          </Grid>
          {engine.health_check_at && (
            <>
              <Grid item xs={12}>
                <Divider sx={{ my: 0.5 }} />
              </Grid>
              <Grid item xs={12}>
                <Typography variant="caption" color="text.secondary">
                  Last Health Check
                </Typography>
                <Typography variant="body2">
                  {formatTime(engine.health_check_at)}
                </Typography>
              </Grid>
            </>
          )}
        </Grid>
      </CardContent>
    </Card>
  )
}

function EngineList({ engines, onDeleteEngine }) {
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
          />
        ))
      )}
    </Box>
  )
}

export default EngineList
