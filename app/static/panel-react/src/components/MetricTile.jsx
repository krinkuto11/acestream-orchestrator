import React from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { cn } from '@/lib/utils'

const STATUS_BG = {
  default: '',
  success: 'bg-emerald-500/10 border-emerald-500/20',
  warning: 'bg-amber-500/10 border-amber-500/20',
  error:   'bg-rose-500/10 border-rose-500/20',
  info:    'bg-sky-500/10 border-sky-500/20',
}

const ICON_COLOR = {
  default: 'text-muted-foreground',
  success: 'text-emerald-500',
  warning: 'text-amber-500',
  error:   'text-rose-500',
  info:    'text-sky-500',
}

/**
 * Unified KPI metric tile.
 *
 * Props:
 *   title       – label rendered as `text-xs uppercase tracking-wider`
 *   value       – primary figure rendered large and bold
 *   suffix      – optional unit rendered at reduced size
 *   icon        – Lucide icon component
 *   status      – 'default' | 'success' | 'warning' | 'error' | 'info'
 *   children    – optional content rendered below the value (trend, progress, etc.)
 *   className   – extra Tailwind classes forwarded to the Card
 */
export function MetricTile({ title, value, suffix, icon: Icon, status = 'default', children, className }) {
  return (
    <Card className={cn('h-full shadow-sm', STATUS_BG[status], className)}>
      <CardHeader className="flex flex-row items-center justify-between p-4 pb-2">
        <CardTitle className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
          {title}
        </CardTitle>
        {Icon && <Icon className={cn('h-4 w-4', ICON_COLOR[status])} />}
      </CardHeader>
      <CardContent className="p-4 pt-0">
        <div className="text-3xl font-bold tracking-tight text-foreground">
          {value}
          {suffix && (
            <span className="ml-1 text-lg font-semibold text-muted-foreground">{suffix}</span>
          )}
        </div>
        {children}
      </CardContent>
    </Card>
  )
}
