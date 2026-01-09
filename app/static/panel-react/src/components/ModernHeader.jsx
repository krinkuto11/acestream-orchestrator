import React, { useState } from 'react'
import { Separator } from '@/components/ui/separator'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Wifi, WifiOff, Bell, CheckCheck, X } from 'lucide-react'
import { cn } from '@/lib/utils'
import { useNotifications } from '@/context/NotificationContext'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import { ScrollArea } from '@/components/ui/scroll-area'

export function ModernHeader({ isConnected, lastUpdate, onOpenSettings }) {
  const { notifications, removeNotification, clearNotifications } = useNotifications()
  const [isOpen, setIsOpen] = useState(false)
  
  const unreadCount = notifications.length
  
  const getNotificationIcon = (type) => {
    switch (type) {
      case 'error':
        return 'text-red-600 dark:text-red-400'
      case 'warning':
        return 'text-yellow-600 dark:text-yellow-400'
      case 'success':
        return 'text-green-600 dark:text-green-400'
      default:
        return 'text-blue-600 dark:text-blue-400'
    }
  }
  
  const getNotificationBg = (type) => {
    switch (type) {
      case 'error':
        return 'bg-red-50 dark:bg-red-950 border-red-200 dark:border-red-800'
      case 'warning':
        return 'bg-yellow-50 dark:bg-yellow-950 border-yellow-200 dark:border-yellow-800'
      case 'success':
        return 'bg-green-50 dark:bg-green-950 border-green-200 dark:border-green-800'
      default:
        return 'bg-blue-50 dark:bg-blue-950 border-blue-200 dark:border-blue-800'
    }
  }
  
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
        
        {/* Notifications */}
        <Popover open={isOpen} onOpenChange={setIsOpen}>
          <PopoverTrigger asChild>
            <Button variant="ghost" size="icon" className="relative">
              <Bell className="h-5 w-5" />
              {unreadCount > 0 && (
                <span className="absolute -top-1 -right-1 h-5 w-5 rounded-full bg-red-600 text-white text-xs flex items-center justify-center">
                  {unreadCount > 9 ? '9+' : unreadCount}
                </span>
              )}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-96 p-0" align="end">
            <div className="flex items-center justify-between p-4 border-b">
              <h3 className="font-semibold">Notifications</h3>
              {unreadCount > 0 && (
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => clearNotifications()}
                  className="h-8 gap-2"
                >
                  <CheckCheck className="h-4 w-4" />
                  Clear All
                </Button>
              )}
            </div>
            <ScrollArea className="h-[400px]">
              {notifications.length === 0 ? (
                <div className="p-8 text-center text-muted-foreground">
                  No notifications
                </div>
              ) : (
                <div className="p-2 space-y-2">
                  {notifications.map((notification) => (
                    <div
                      key={notification.id}
                      className={cn(
                        'p-3 rounded-lg border relative',
                        getNotificationBg(notification.type)
                      )}
                    >
                      <div className="flex items-start gap-2">
                        <div className={cn('mt-0.5', getNotificationIcon(notification.type))}>
                          <div className="h-2 w-2 rounded-full bg-current"></div>
                        </div>
                        <div className="flex-1 pr-6">
                          <p className="text-sm">{notification.message}</p>
                          <p className="text-xs text-muted-foreground mt-1">
                            {new Date(notification.timestamp).toLocaleTimeString()}
                          </p>
                        </div>
                        <Button
                          variant="ghost"
                          size="icon"
                          className="h-6 w-6 absolute top-2 right-2"
                          onClick={() => removeNotification(notification.id)}
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </ScrollArea>
          </PopoverContent>
        </Popover>
      </div>
    </header>
  )
}
