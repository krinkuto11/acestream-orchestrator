import React from 'react'
import { Badge } from '@/components/ui/badge'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Activity, Bell } from 'lucide-react'
import { Button } from '@/components/ui/button'

export function Topbar({
  orchUrl,
  setOrchUrl,
  apiKey,
  setApiKey,
  refreshInterval,
  setRefreshInterval,
  isConnected
}) {
  return (
    <header className="sticky top-0 z-40 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex h-16 items-center justify-between px-6">
        <div className="flex items-center gap-4">
          <h2 className="text-lg font-semibold">Dashboard</h2>
          <Badge variant={isConnected ? "success" : "destructive"} className="flex items-center gap-1">
            <Activity className="h-3 w-3" />
            {isConnected ? 'Connected' : 'Disconnected'}
          </Badge>
        </div>

        <div className="flex items-center gap-4">
          <Button variant="ghost" size="icon" className="relative">
            <Bell className="h-5 w-5" />
            <span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-red-500" />
          </Button>
          
          <div className="h-8 w-px bg-border" />
          
          <div className="flex items-center gap-2 text-sm">
            <span className="text-muted-foreground">Refresh:</span>
            <Select value={refreshInterval.toString()} onValueChange={(val) => setRefreshInterval(Number(val))}>
              <SelectTrigger className="h-8 w-20">
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
