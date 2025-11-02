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
