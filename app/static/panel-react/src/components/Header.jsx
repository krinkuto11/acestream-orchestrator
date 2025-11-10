import React from 'react'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Activity } from 'lucide-react'

function Header({
  orchUrl,
  setOrchUrl,
  apiKey,
  setApiKey,
  refreshInterval,
  setRefreshInterval,
  isConnected
}) {
  return (
    <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="container flex h-16 items-center gap-4 px-4">
        <div className="flex items-center gap-2">
          <h1 className="text-xl font-bold text-primary">Acestream Orchestrator</h1>
          <Badge variant={isConnected ? "success" : "destructive"} className="flex items-center gap-1">
            <Activity className="h-3 w-3" />
            {isConnected ? 'Connected' : 'Error'}
          </Badge>
        </div>

        <div className="ml-auto flex items-center gap-4">
          <div className="flex flex-col gap-1">
            <Label htmlFor="server-url" className="text-xs">Server URL</Label>
            <Input
              id="server-url"
              value={orchUrl}
              onChange={(e) => setOrchUrl(e.target.value)}
              className="h-8 w-48"
              placeholder="http://localhost:8000"
            />
          </div>
          
          <div className="flex flex-col gap-1">
            <Label htmlFor="api-key" className="text-xs">API Key</Label>
            <Input
              id="api-key"
              type="password"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              className="h-8 w-40"
              placeholder="Enter API key"
            />
          </div>

          <div className="flex flex-col gap-1">
            <Label htmlFor="refresh" className="text-xs">Refresh</Label>
            <Select value={refreshInterval.toString()} onValueChange={(val) => setRefreshInterval(Number(val))}>
              <SelectTrigger className="h-8 w-24">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="2000">2s</SelectItem>
                <SelectItem value="5000">5s</SelectItem>
                <SelectItem value="10000">10s</SelectItem>
                <SelectItem value="30000">30s</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>
    </header>
  )
}

export default Header
