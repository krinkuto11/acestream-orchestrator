import React from 'react'
import { Separator } from '@/components/ui/separator'
import { ThemeToggle } from './ThemeToggle'
import { Badge } from '@/components/ui/badge'
import { Wifi, WifiOff } from 'lucide-react'
import { cn } from '@/lib/utils'

export function ModernHeader({ isConnected, lastUpdate, onOpenSettings }) {
  return (
    <header className="sticky top-0 z-50 w-full border-b bg-background/95 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      <div className="flex h-16 items-center gap-4 px-6">
        {/* Connection Status */}
        <div className="flex items-center gap-2">
          {isConnected ? (
            <Wifi className="h-4 w-4 text-green-600 dark:text-green-400" />
          ) : (
            <WifiOff className="h-4 w-4 text-red-600 dark:text-red-400" />
          )}
          <Badge variant={isConnected ? "success" : "destructive"} className="text-xs">
            {isConnected ? 'Connected' : 'Disconnected'}
          </Badge>
        </div>
        
        <Separator orientation="vertical" className="h-6" />
        
        {/* Last Update */}
        {lastUpdate && (
          <span className="text-xs text-muted-foreground">
            Last update: {lastUpdate.toLocaleTimeString()}
          </span>
        )}
        
        {/* Spacer */}
        <div className="flex-1" />
        
        {/* Right side controls */}
        <div className="flex items-center gap-2">
          <ThemeToggle />
        </div>
      </div>
    </header>
  )
}
