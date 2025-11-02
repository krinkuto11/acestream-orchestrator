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
import { formatTime, formatBytes, formatBytesPerSecond } from '../utils/formatters'

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
            <Grid item xs={12}>
              <Typography variant="caption" color="text.secondary">
                Engine
              </Typography>
              <Typography variant="body2" noWrap>
                {stream.container_name || stream.container_id?.slice(0, 12) || 'N/A'}
              </Typography>
            </Grid>
            <Grid item xs={12}>
              <Typography variant="caption" color="text.secondary">
                Started
              </Typography>
              <Typography variant="body2">
                {formatTime(stream.started_at)}
              </Typography>
            </Grid>
            {/* AceStream API returns speeds in KB/s, convert to B/s for formatter */}
            <Grid item xs={4}>
              <Typography variant="caption" color="text.secondary">
                Download
              </Typography>
              <Typography variant="body2" sx={{ color: 'secondary.main', fontWeight: 600 }}>
                {formatBytesPerSecond((stream.speed_down || 0) * 1024)}
              </Typography>
            </Grid>
            <Grid item xs={4}>
              <Typography variant="caption" color="text.secondary">
                Upload
              </Typography>
              <Typography variant="body2" sx={{ color: 'error.main', fontWeight: 600 }}>
                {formatBytesPerSecond((stream.speed_up || 0) * 1024)}
              </Typography>
            </Grid>
            <Grid item xs={4}>
              <Typography variant="caption" color="text.secondary">
                Peers
              </Typography>
              <Typography variant="body2" sx={{ color: 'info.main', fontWeight: 600 }}>
                {stream.peers != null ? stream.peers : 'N/A'}
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="caption" color="text.secondary">
                Total Downloaded
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {formatBytes(stream.downloaded)}
              </Typography>
            </Grid>
            <Grid item xs={6}>
              <Typography variant="caption" color="text.secondary">
                Total Uploaded
              </Typography>
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                {formatBytes(stream.uploaded)}
              </Typography>
            </Grid>
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
