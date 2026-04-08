import React from 'react'
import { Tooltip } from 'recharts'
import { cn } from '@/lib/utils'

const ChartContext = React.createContext({ config: {} })

function useChart() {
  return React.useContext(ChartContext)
}

function toCssVars(config = {}) {
  return Object.entries(config).reduce((style, [key, item]) => {
    if (!item) return style
    const color = item.color || item.theme?.light || item.theme?.dark
    if (!color) return style
    style[`--color-${key}`] = color
    return style
  }, {})
}

const ChartContainer = React.forwardRef(({ id, className, children, config = {}, style, ...props }, ref) => {
  const inlineVars = React.useMemo(() => toCssVars(config), [config])

  return (
    <ChartContext.Provider value={{ config }}>
      <div
        ref={ref}
        data-chart={id || 'chart'}
        className={cn(
          'flex aspect-video justify-center text-xs',
          className,
        )}
        style={{ ...inlineVars, ...style }}
        {...props}
      >
        {children}
      </div>
    </ChartContext.Provider>
  )
})
ChartContainer.displayName = 'ChartContainer'

const ChartTooltip = Tooltip

function ChartTooltipContent({
  active,
  payload,
  label,
  className,
  hideLabel = false,
  labelFormatter,
  formatter,
}) {
  const { config } = useChart()

  if (!active || !payload?.length) {
    return null
  }

  const rawLabel = labelFormatter ? labelFormatter(label, payload) : label

  return (
    <div className={cn('min-w-[11rem] rounded-lg border bg-background/95 p-2 shadow-md', className)}>
      {!hideLabel && rawLabel != null && (
        <div className="mb-1 text-[11px] font-medium text-muted-foreground">{rawLabel}</div>
      )}
      <div className="space-y-1">
        {payload.map((item, index) => {
          const key = item.dataKey || item.name || `value-${index}`
          const cfg = config[key] || config[item.name] || {}
          const entryLabel = cfg.label || item.name || item.dataKey

          let renderedValue = item.value
          if (formatter) {
            const formatted = formatter(item.value, entryLabel, item, index, item.payload)
            if (formatted !== undefined) {
              renderedValue = formatted
            }
          }

          return (
            <div key={`${key}-${index}`} className="flex items-center justify-between gap-2 text-xs">
              <div className="flex items-center gap-1.5 text-muted-foreground">
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: item.color || 'currentColor' }}
                />
                <span>{entryLabel}</span>
              </div>
              <div className="font-medium text-foreground">{renderedValue}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export { ChartContainer, ChartTooltip, ChartTooltipContent }
