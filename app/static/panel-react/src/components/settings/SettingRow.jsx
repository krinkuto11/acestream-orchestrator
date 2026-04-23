import React from 'react'
import { Label } from '@/components/ui/label'
import { AlertTriangle, Info } from 'lucide-react'

export function SettingRow({
  label,
  description,
  children,
  tooltip,
  warning,
  htmlFor,
  className = '',
}) {
  return (
    <div className={`grid gap-3 rounded-lg border border-slate-200/70 bg-white/60 p-4 dark:border-slate-800 dark:bg-slate-900/40 md:grid-cols-[minmax(220px,300px)_1fr] ${className}`.trim()}>
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Label htmlFor={htmlFor} className="text-sm font-semibold text-slate-800 dark:text-slate-100">
            {label}
          </Label>
          {tooltip && (
            <span className="inline-flex items-center text-slate-400" title={tooltip} aria-label={tooltip}>
              <Info className="h-3.5 w-3.5" />
            </span>
          )}
        </div>
        {description && (
          <p className="text-xs text-slate-500 dark:text-slate-400">{description}</p>
        )}
        {warning && (
          <div className="inline-flex items-center gap-1 rounded-md border border-amber-300/60 bg-amber-50/80 px-2 py-1 text-[11px] text-amber-800 dark:border-amber-700/70 dark:bg-amber-950/40 dark:text-amber-300">
            <AlertTriangle className="h-3 w-3" />
            <span>{warning}</span>
          </div>
        )}
      </div>
      <div className="flex items-center">{children}</div>
    </div>
  )
}
