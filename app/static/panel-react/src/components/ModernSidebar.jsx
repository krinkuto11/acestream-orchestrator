import React, { useState } from 'react'
import { cn } from '@/lib/utils'
import { 
  LayoutDashboard, 
  Server, 
  Activity, 
  Wifi, 
  Settings, 
  BarChart3, 
  ShieldCheck,
  ChevronLeft,
  ChevronRight 
} from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'

const navigation = [
  { name: 'Overview', href: '/', icon: LayoutDashboard },
  { name: 'Engines', href: '/engines', icon: Server },
  { name: 'Streams', href: '/streams', icon: Activity },
  { name: 'Health', href: '/health', icon: ShieldCheck },
  { name: 'VPN', href: '/vpn', icon: Wifi },
  { name: 'Metrics', href: '/metrics', icon: BarChart3 },
  { name: 'Settings', href: '/settings', icon: Settings },
]

export function ModernSidebar() {
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)

  return (
    <div className={cn(
      "flex h-screen flex-col border-r bg-card transition-all duration-300",
      collapsed ? "w-16" : "w-64"
    )}>
      {/* Logo */}
      <div className="flex h-16 items-center border-b px-4">
        {!collapsed && (
          <h1 className="text-lg font-bold text-primary truncate">
            Acestream Orchestrator
          </h1>
        )}
        {collapsed && (
          <span className="text-lg font-bold text-primary">AO</span>
        )}
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 p-2 overflow-y-auto">
        {navigation.map((item) => {
          const isActive = location.pathname === item.href
          return (
            <Link
              key={item.name}
              to={item.href}
              className={cn(
                'flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors',
                isActive
                  ? 'bg-primary text-primary-foreground'
                  : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground',
                collapsed && 'justify-center'
              )}
              title={collapsed ? item.name : undefined}
            >
              <item.icon className="h-5 w-5 shrink-0" />
              {!collapsed && <span>{item.name}</span>}
            </Link>
          )
        })}
      </nav>

      <Separator />

      {/* Footer with toggle */}
      <div className="p-2">
        <Button
          variant="ghost"
          size="sm"
          className="w-full justify-center"
          onClick={() => setCollapsed(!collapsed)}
        >
          {collapsed ? (
            <ChevronRight className="h-4 w-4" />
          ) : (
            <>
              <ChevronLeft className="h-4 w-4 mr-2" />
              <span className="text-xs">Collapse</span>
            </>
          )}
        </Button>
        
        {!collapsed && (
          <p className="text-xs text-muted-foreground text-center mt-2">
            Version 1.0.0
          </p>
        )}
      </div>
    </div>
  )
}
