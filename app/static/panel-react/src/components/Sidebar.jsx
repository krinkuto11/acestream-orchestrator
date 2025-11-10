import React from 'react'
import { cn } from '@/lib/utils'
import { LayoutDashboard, Server, Activity, Wifi, Settings, BarChart3, ShieldCheck } from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'

const navigation = [
  { name: 'Overview', href: '/', icon: LayoutDashboard },
  { name: 'Engines', href: '/engines', icon: Server },
  { name: 'Streams', href: '/streams', icon: Activity },
  { name: 'Health', href: '/health', icon: ShieldCheck },
  { name: 'VPN', href: '/vpn', icon: Wifi },
  { name: 'Metrics', href: '/metrics', icon: BarChart3 },
  { name: 'Settings', href: '/settings', icon: Settings },
]

export function Sidebar() {
  const location = useLocation()

  return (
    <div className="flex h-screen w-64 flex-col border-r bg-card">
      {/* Logo */}
      <div className="flex h-16 items-center border-b px-6">
        <h1 className="text-xl font-bold text-primary">Acestream Orchestrator</h1>
      </div>

      {/* Navigation */}
      <nav className="flex-1 space-y-1 px-3 py-4">
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
                  : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
              )}
            >
              <item.icon className="h-5 w-5" />
              {item.name}
            </Link>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="border-t p-4">
        <p className="text-xs text-muted-foreground">
          Version 1.0.0
        </p>
      </div>
    </div>
  )
}
