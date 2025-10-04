import React from 'react'
import {
  Card,
  CardContent,
  CardActionArea,
  Typography,
  Box,
  Chip,
  Divider,
  Grid
} from '@mui/material'
import PlayCircleFilledIcon from '@mui/icons-material/PlayCircleFilled'
import { formatTime, formatBytes } from '../utils/formatters'

function StreamCard({ stream, isSelected, onSelect }) {
  return (
    <Card 
      sx={{ 
        mb: 2, 
        border: isSelected ? 2 : 0,
        borderColor: 'primary.main',
        '&:hover': { bgcolor: 'action.hover' }
      }}
    >
      <CardActionArea onClick={() => onSelect(stream)}>
        <CardContent>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 2 }}>
            <Box sx={{ flex: 1, overflow: 'hidden' }}>
              <Typography variant="h6" component="div" sx={{ fontWeight: 600, mb: 0.5 }}>
                {stream.id.slice(0, 16)}...
              </Typography>
              <Typography variant="body2" color="text.secondary" noWrap>
                {stream.content_key || 'N/A'}
              </Typography>
            </Box>
            <Chip
              icon={<PlayCircleFilledIcon />}
              label="ACTIVE"
              color="success"
              size="small"
              sx={{ ml: 1 }}
            />
          </Box>

          <Divider sx={{ my: 1.5 }} />

          <Grid container spacing={1}>
            <Grid item xs={6}>
              <Typography variant="caption" color="text.secondary">
                Engine
              </Typography>
              <Typography variant="body2" noWrap>
                {stream.container_name || stream.container_id?.slice(0, 12) || 'N/A'}
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="caption" color="text.secondary">
                Started
              </Typography>
              <Typography variant="body2">
                {formatTime(stream.started_at)}
              </Typography>
            </Grid>
            {stream.peers != null && (
              <Grid item xs={6}>
                <Typography variant="caption" color="text.secondary">
                  Peers
                </Typography>
                <Typography variant="body2">
                  {stream.peers}
                </Typography>
              </Grid>
            )}
            {stream.speed_down != null && (
              <Grid item xs={6}>
                <Typography variant="caption" color="text.secondary">
                  Download
                </Typography>
                <Typography variant="body2">
                  {formatBytes(stream.speed_down)}/s
                </Typography>
              </Grid>
            )}
          </Grid>
        </CardContent>
      </CardActionArea>
    </Card>
  )
}

function StreamList({ streams, selectedStream, onSelectStream }) {
  return (
    <Box>
      <Typography variant="h5" component="h2" gutterBottom sx={{ fontWeight: 600 }}>
        Active Streams ({streams.length})
      </Typography>
      {streams.length === 0 ? (
        <Card>
          <CardContent>
            <Typography color="text.secondary">No active streams</Typography>
          </CardContent>
        </Card>
      ) : (
        streams.map((stream) => (
          <StreamCard
            key={stream.id}
            stream={stream}
            isSelected={selectedStream?.id === stream.id}
            onSelect={onSelectStream}
          />
        ))
      )}
    </Box>
  )
}

export default StreamList
