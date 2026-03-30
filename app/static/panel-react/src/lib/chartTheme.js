/**
 * Unified chart color palette for ECharts / Chart.js.
 * Colors are chosen to be clearly legible on both light and dark card
 * backgrounds while remaining visually cohesive with the ShadCN theme.
 */

export const CHART_SERIES = {
  blue:    '#6b7cf5',  // indigo-ish — primary series
  emerald: '#10b981',  // emerald-500 — success / egress
  amber:   '#f59e0b',  // amber-500  — warning / latency
  rose:    '#f43f5e',  // rose-500   — error / critical
  violet:  '#8b5cf6',  // violet-500 — secondary series
  sky:     '#0ea5e9',  // sky-500    — info / streams
}

/**
 * Returns axis / grid / tooltip colors that adapt to light vs dark mode.
 * @param {boolean} isDark
 */
export function getChartTheme(isDark) {
  return {
    axisLabel:     isDark ? '#94a3b8' : '#64748b',   // slate-400 / slate-500
    splitLine:     isDark ? 'rgba(148,163,184,0.12)' : 'rgba(0,0,0,0.07)',
    tooltipBg:     isDark ? '#1e293b' : '#ffffff',
    tooltipBorder: isDark ? '#334155' : '#e2e8f0',
    tooltipText:   isDark ? '#f1f5f9' : '#0f172a',
    legendText:    isDark ? '#94a3b8' : '#64748b',
  }
}
