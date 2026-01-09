import React, { useState, useEffect } from 'react'
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
  ChevronRight,
  FileText
} from 'lucide-react'
import { Link, useLocation } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'

const navigation = [
  { name: 'Overview', href: '/', icon: LayoutDashboard },
  { name: 'Engines', href: '/engines', icon: Server },
  { name: 'Streams', href: '/streams', icon: Activity },
  { name: 'Events', href: '/events', icon: FileText },
  { name: 'Health', href: '/health', icon: ShieldCheck },
  { name: 'VPN', href: '/vpn', icon: Wifi },
  { name: 'Metrics', href: '/metrics', icon: BarChart3 },
  { name: 'Settings', href: '/settings', icon: Settings },
]

// Sidebar width constants
const SIDEBAR_WIDTH_EXPANDED = '16rem' // w-64 in Tailwind
const SIDEBAR_WIDTH_COLLAPSED = '4rem' // w-16 in Tailwind

export function ModernSidebar() {
  const location = useLocation()
  const [collapsed, setCollapsed] = useState(false)
  const [version, setVersion] = useState('1.0.0')

  useEffect(() => {
    // Fetch version information
    fetch('/panel/version.json')
      .then(res => res.json())
      .then(data => {
        setVersion(data.version)
      })
      .catch(err => {
        console.warn('Failed to load version info:', err)
      })
  }, [])

  useEffect(() => {
    // Update CSS variable for sidebar width
    document.documentElement.style.setProperty(
      '--sidebar-width', 
      collapsed ? SIDEBAR_WIDTH_COLLAPSED : SIDEBAR_WIDTH_EXPANDED
    )
  }, [collapsed])

  return (
    <div className={cn(
      "fixed left-0 top-0 flex h-screen flex-col border-r bg-card transition-all duration-300",
      collapsed ? "w-16" : "w-64"
    )}>
      {/* Logo */}
      <div className="flex h-16 items-center border-b px-6 gap-2">
        {!collapsed && (
          <>
            <img src="/favicon-96x96-dark.png" alt="AceStream Logo" className="h-8 w-8" />
            <h1 className="text-lg font-bold text-primary leading-tight">
              <span className="block">AceStream</span>
              <span className="block">Orchestrator</span>
            </h1>
          </>
        )}
        {collapsed && (
          <img src="/favicon-96x96-dark.png" alt="AceStream Logo" className="h-8 w-8" />
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
          className="w-full justify-center text-foreground"
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
            Version {version}
          </p>
        )}
      </div>
    </div>
  )
}
