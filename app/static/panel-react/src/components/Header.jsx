import React from 'react'
import {
  AppBar,
  Toolbar,
  Typography,
  Box,
  TextField,
  Select,
  MenuItem,
  IconButton,
  Chip,
  FormControl,
  InputLabel
} from '@mui/material'
import RefreshIcon from '@mui/icons-material/Refresh'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'

function Header({
  orchUrl,
  setOrchUrl,
  apiKey,
  setApiKey,
  refreshInterval,
  setRefreshInterval,
  onRefresh,
  isConnected
}) {
  return (
    <AppBar position="sticky" color="default" elevation={1}>
      <Toolbar sx={{ gap: 2, flexWrap: 'wrap', py: 1 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <Typography variant="h6" component="div" color="primary" sx={{ fontWeight: 600 }}>
            Acestream Orchestrator
          </Typography>
          <Chip
            icon={<FiberManualRecordIcon />}
            label={isConnected ? 'Connected (Polling)' : 'Error'}
            color={isConnected ? 'success' : 'error'}
            size="small"
            sx={{ ml: 1 }}
          />
        </Box>

        <Box sx={{ display: 'flex', gap: 2, flexWrap: 'wrap', flex: 1, justifyContent: 'flex-end' }}>
          <TextField
            label="Server URL"
            value={orchUrl}
            onChange={(e) => setOrchUrl(e.target.value)}
            size="small"
            sx={{ minWidth: 200 }}
          />
          <TextField
            label="API Key"
            type="password"
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            size="small"
            sx={{ minWidth: 150 }}
          />
          <FormControl size="small" sx={{ minWidth: 100 }}>
            <InputLabel>Refresh</InputLabel>
            <Select
              value={refreshInterval}
              onChange={(e) => setRefreshInterval(e.target.value)}
              label="Refresh"
            >
              <MenuItem value={2000}>2s</MenuItem>
              <MenuItem value={5000}>5s</MenuItem>
              <MenuItem value={10000}>10s</MenuItem>
              <MenuItem value={30000}>30s</MenuItem>
            </Select>
          </FormControl>
          <IconButton onClick={onRefresh} color="primary">
            <RefreshIcon />
          </IconButton>
        </Box>
      </Toolbar>
    </AppBar>
  )
}

export default Header
