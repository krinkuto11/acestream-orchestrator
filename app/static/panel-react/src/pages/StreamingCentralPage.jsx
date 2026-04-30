import React, { useEffect, useMemo, useState } from 'react'
import { useStreamingCentralStore } from '@/stores/streamingCentralStore'
import { useTheme } from '@/components/ThemeProvider'
import { CHART_SERIES } from '@/lib/chartTheme'

// ── Helpers ──────────────────────────────────────────────────────────────────
const formatTime = (iso) => {
  if (!iso) return '–'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? '–' : d.toLocaleTimeString([], { hour12: false })
}

const formatEgress = (egressGbps) => {
  const n = Number(egressGbps || 0)
  if (!Number.isFinite(n) || n <= 0) return { value: '0.0', suffix: 'Mbps' }
  if (n >= 1) return { value: n.toFixed(3), suffix: 'Gbps' }
  return { value: (n * 1000).toFixed(1), suffix: 'Mbps' }
}

// ── BigCounter ────────────────────────────────────────────────────────────────
function BigCounter({ label, value, sub, accent = 'green' }) {
  return (
    <div className="bracketed" style={{
      background: 'var(--bg-1)',
      border: '1px solid var(--line)',
      padding: '12px 14px',
      position: 'relative',
      flexShrink: 0,
    }}>
      <div className="label" style={{ color: 'var(--fg-3)' }}>{label}</div>
      <div style={{
        fontFamily: 'var(--font-display)',
        fontSize: 36, fontWeight: 700, lineHeight: 1.05,
        color: `var(--acc-${accent})`,
        fontVariantNumeric: 'tabular-nums',
        letterSpacing: '-0.04em',
        marginTop: 2,
        textShadow: `0 0 24px var(--acc-${accent}-bg)`,
      }}>{value}</div>
      {sub && <div style={{ fontSize: 10, color: 'var(--fg-2)', marginTop: 2 }}>{sub}</div>}
      <div style={{ position: 'absolute', right: 6, top: 6, fontSize: 8, color: 'var(--fg-3)', letterSpacing: 1 }}>◇ LIVE</div>
    </div>
  )
}

// ── PolicyBlock ───────────────────────────────────────────────────────────────
function PolicyBlock({ orchestratorStatus }) {
  const minR = orchestratorStatus?.config?.min_replicas ?? orchestratorStatus?.engines?.min_replicas ?? '–'
  const maxR = orchestratorStatus?.capacity?.max_replicas ?? orchestratorStatus?.engines?.max_replicas ?? '–'
  const breaker = orchestratorStatus?.provisioning?.circuit_breaker_state || 'closed'
  const breakerColor = breaker === 'closed' ? 'var(--acc-green)' : 'var(--acc-amber)'

  return (
    <div style={{
      background: 'var(--bg-1)',
      border: '1px solid var(--line-soft)',
      padding: '10px 12px',
    }}>
      <div className="label" style={{ marginBottom: 6 }}>POLICY</div>
      {[
        ['MIN_REPLICAS', String(minR)],
        ['MAX_REPLICAS', String(maxR)],
        ['BREAKER', breaker],
        ['PROTOCOL', 'sse/1s'],
      ].map(([k, v]) => (
        <div key={k} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 10, padding: '3px 0' }}>
          <span style={{ color: 'var(--fg-3)', width: 90, flexShrink: 0 }}>{k}</span>
          <span style={{ color: k === 'BREAKER' ? breakerColor : 'var(--fg-0)' }}>{v}</span>
        </div>
      ))}
    </div>
  )
}

// ── Marquee ───────────────────────────────────────────────────────────────────
function Marquee({ engines, streams, vpnStatus, isConnected }) {
  const activeEngines = engines.filter(e => e.health_status === 'healthy').length
  const drainingEngines = engines.filter(e => e.health_status === 'unhealthy').length
  const vpnMode = vpnStatus?.mode || 'disabled'
  const vpnConnected = vpnStatus?.connected || (vpnStatus?.vpn1?.connected && vpnStatus?.vpn2?.connected)
  const migrations = streams.filter(s => String(s.status || '').includes('failover')).length

  const items = [
    { text: isConnected ? '● ONLINE' : '● OFFLINE', color: isConnected ? 'var(--acc-green)' : 'var(--acc-red)' },
    { text: `${streams.length}.STREAMS`, color: 'var(--fg-2)' },
    { text: `${engines.length}.ENGINES`, color: 'var(--fg-2)' },
    { text: `${activeEngines}.HEALTHY`, color: 'var(--acc-green)' },
    drainingEngines > 0 && { text: `${drainingEngines}.UNHEALTHY`, color: 'var(--acc-red)' },
    migrations > 0 && { text: `${migrations}.MIGRATION`, color: 'var(--acc-magenta)' },
    { text: `VPN.${vpnMode.toUpperCase()}`, color: vpnConnected ? 'var(--acc-cyan)' : 'var(--fg-3)' },
    { text: `SSE.LIVE`, color: isConnected ? 'var(--acc-green)' : 'var(--acc-red)' },
  ].filter(Boolean)

  const content = items.map((item, i) => (
    <React.Fragment key={i}>
      <span style={{ color: item.color }}>{item.text}</span>
      <span style={{ color: 'var(--fg-3)', padding: '0 10px' }}>│</span>
    </React.Fragment>
  ))

  return (
    <div style={{
      height: 24,
      borderBottom: '1px solid var(--line)',
      background: 'var(--bg-1)',
      overflow: 'hidden',
      fontSize: 10,
      color: 'var(--fg-2)',
      fontFamily: 'var(--font-mono)',
      letterSpacing: '0.1em',
      display: 'flex',
      alignItems: 'center',
      flexShrink: 0,
    }}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        whiteSpace: 'nowrap',
        animation: 'marquee 30s linear infinite',
        paddingLeft: '100%',
      }}>
        {content}{content}
      </div>
    </div>
  )
}

// ── SignalRow ─────────────────────────────────────────────────────────────────
function SignalRow({ e, idx }) {
  const sevColor = {
    info: 'var(--fg-1)',
    warn: 'var(--acc-amber)',
    error: 'var(--acc-red)',
    warning: 'var(--acc-amber)',
  }[e.severity?.toLowerCase() || e.level?.toLowerCase() || 'info'] || 'var(--fg-1)'

  const sevGlyph = {
    info: '·', warn: '!', warning: '!', error: '✗',
  }[e.severity?.toLowerCase() || e.level?.toLowerCase() || 'info'] || '·'

  const eventType = String(e.event_type || e.type || e.src || '').toLowerCase()
  const message = e.message || e.msg || ''
  const ts = formatTime(e.timestamp || e.t)

  return (
    <div style={{
      display: 'flex', alignItems: 'flex-start', gap: 6,
      padding: '6px 14px',
      borderBottom: '1px solid var(--line-soft)',
      fontSize: 10,
      fontFamily: 'var(--font-mono)',
      opacity: Math.max(0.35, 1 - idx * 0.035),
    }}>
      <span style={{ color: 'var(--fg-3)', width: 54, flexShrink: 0 }}>{ts}</span>
      <span style={{ color: sevColor, width: 8, flexShrink: 0 }}>{sevGlyph}</span>
      <span style={{ color: 'var(--acc-cyan)', width: 72, flexShrink: 0, fontSize: 9, overflow: 'hidden', textOverflow: 'ellipsis' }}>{eventType}</span>
      <span style={{ color: 'var(--fg-0)', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{message}</span>
    </div>
  )
}

// ── Session Waveform ──────────────────────────────────────────────────────────
function SessionWaveform({ kpiHistory }) {
  const data = kpiHistory?.activeStreams || []
  const W = 720, H = 52

  if (data.length < 2) {
    return (
      <div style={{
        borderTop: '1px solid var(--line)',
        padding: '8px 14px 12px',
        fontSize: 10, color: 'var(--fg-3)',
        flexShrink: 0,
      }}>
        <div className="label" style={{ marginBottom: 4 }}>SESSION.WAVEFORM · LAST 15M</div>
        <div style={{ color: 'var(--fg-3)', fontStyle: 'italic' }}>awaiting data...</div>
      </div>
    )
  }

  const max = Math.max(...data, 1)
  const linePts = data.map((v, i) =>
    `${i === 0 ? 'M' : 'L'} ${(i / (data.length - 1)) * W} ${H - (v / max) * H}`
  ).join(' ')
  const fillPts = `M 0 ${H} ` + data.map((v, i) =>
    `L ${(i / (data.length - 1)) * W} ${H - (v / max) * H}`
  ).join(' ') + ` L ${W} ${H} Z`

  return (
    <div style={{
      borderTop: '1px solid var(--line)',
      padding: '8px 14px 12px',
      flexShrink: 0,
    }}>
      <div style={{ display: 'flex', alignItems: 'center', marginBottom: 4 }}>
        <span className="label">SESSION.WAVEFORM · LAST 15M</span>
        <div style={{ flex: 1 }}/>
        <span style={{ fontSize: 9, color: 'var(--fg-3)' }}>0 ─────── 15m</span>
      </div>
      <svg width="100%" viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" style={{ display: 'block', height: H }}>
        {[0, 1, 2, 3, 4, 5].map(i => (
          <line key={i} x1={i * (W / 5)} y1="0" x2={i * (W / 5)} y2={H} stroke="var(--line-soft)"/>
        ))}
        <path d={fillPts} fill="var(--acc-green-bg)" opacity="0.5"/>
        <path d={linePts} fill="none" stroke="var(--acc-green)" strokeWidth="1.5"/>
      </svg>
    </div>
  )
}

// ── Constellation Graph ───────────────────────────────────────────────────────
function buildVpnNodes(vpnStatus) {
  if (!vpnStatus || !vpnStatus.enabled || vpnStatus.mode === 'disabled') return []

  if (vpnStatus.mode === 'redundant') {
    return [vpnStatus.vpn1, vpnStatus.vpn2]
      .filter(Boolean)
      .map((t, i) => ({
        id: t.container_name || t.container || `vpn-${i + 1}`,
        label: t.container_name || `VPN ${i + 1}`,
        state: t.connected ? 'healthy' : 'failed',
        ip: t.public_ip || '–',
        provider: t.provider || '–',
      }))
  }

  const tunnel = vpnStatus.vpn1 || vpnStatus
  return [{
    id: tunnel.container_name || tunnel.container || 'vpn-1',
    label: tunnel.container_name || 'VPN',
    state: tunnel.connected ? 'healthy' : 'failed',
    ip: tunnel.public_ip || vpnStatus.public_ip || '–',
    provider: tunnel.provider || '–',
  }]
}

function colorFor(state) {
  const map = {
    healthy: 'var(--acc-green)',
    active: 'var(--acc-green)',
    healthy_unhealthy: 'var(--acc-green)',
    draining: 'var(--acc-amber)',
    warming: 'var(--acc-magenta)',
    pending: 'var(--acc-cyan)',
    failed: 'var(--acc-red)',
    unhealthy: 'var(--acc-red)',
  }
  return map[state] || 'var(--fg-2)'
}

function ConstellationGraph({ engines, vpnStatus }) {
  const W = 760, H = 360
  const cx = W / 2, cy = H / 2

  const vpnNodes = useMemo(() => buildVpnNodes(vpnStatus), [vpnStatus])

  // Group engines by VPN container
  const engsByVpn = useMemo(() => {
    const map = new Map()
    engines.forEach(e => {
      const key = e.vpn_container || '__none__'
      if (!map.has(key)) map.set(key, [])
      map.get(key).push(e)
    })
    return map
  }, [engines])

  // If no VPN nodes, show engines around center
  const noVpn = vpnNodes.length === 0

  // Sun positions
  const vpnCount = noVpn ? 1 : vpnNodes.length
  const sunRadius = Math.min(W, H) * (vpnCount > 4 ? 0.30 : 0.38)
  const sunR = 14

  const vpnPos = {}
  if (noVpn) {
    vpnPos['__center__'] = { x: cx, y: cy, angle: 0 }
  } else {
    vpnNodes.forEach((v, i) => {
      const a = (i / vpnCount) * Math.PI * 2 - Math.PI / 2
      vpnPos[v.id] = { x: cx + Math.cos(a) * sunRadius, y: cy + Math.sin(a) * sunRadius, angle: a }
    })
  }

  // Engine positions
  const engPos = {}
  const allEngineGroups = noVpn
    ? [{ vpnId: '__center__', engs: engines }]
    : vpnNodes.map(v => ({
        vpnId: v.id,
        engs: engsByVpn.get(v.label) || engsByVpn.get(v.id) || [],
      }))

  // Also handle engines with no VPN match in redundant mode
  if (!noVpn) {
    const unassigned = engines.filter(e => !vpnNodes.some(v => v.label === e.vpn_container || v.id === e.vpn_container))
    if (unassigned.length > 0) allEngineGroups.push({ vpnId: '__none__', engs: unassigned })
  }

  allEngineGroups.forEach(({ vpnId, engs: myEngs }) => {
    const sun = vpnPos[vpnId] || { x: cx, y: cy, angle: 0 }
    const n = myEngs.length
    if (n === 0) return

    const ringCap = [6, 10, 14]
    const rings = []
    let remaining = n
    let r = 0
    while (remaining > 0 && r < ringCap.length) {
      const c = Math.min(remaining, ringCap[r])
      rings.push(c)
      remaining -= c
      r++
    }

    const baseR = 38
    const ringStep = 22
    let placed = 0
    rings.forEach((cnt, ri) => {
      const radius = baseR + ri * ringStep
      const arcCenter = sun.angle
      const span = Math.PI * 1.4
      for (let i = 0; i < cnt; i++) {
        const a = cnt === 1
          ? arcCenter
          : arcCenter + (i / (cnt - 1) - 0.5) * span
        engPos[myEngs[placed + i].container_id] = {
          x: sun.x + Math.cos(a) * radius,
          y: sun.y + Math.sin(a) * radius,
        }
      }
      placed += cnt
    })
  })

  const showLabel = engines.length <= 14
  const engSize = engines.length > 30 ? 6 : engines.length > 14 ? 8 : 10

  return (
    <svg width="100%" height="100%" viewBox={`0 0 ${W} ${H}`} style={{ display: 'block' }} preserveAspectRatio="xMidYMid meet">
      <defs>
        <pattern id="cgrid" width="20" height="20" patternUnits="userSpaceOnUse">
          <path d="M 20 0 L 0 0 0 20" fill="none" stroke="var(--line-soft)" strokeWidth="0.5"/>
        </pattern>
        <radialGradient id="glowG"><stop offset="0%" stopColor="var(--acc-green)" stopOpacity="0.35"/><stop offset="100%" stopColor="var(--acc-green)" stopOpacity="0"/></radialGradient>
        <radialGradient id="glowA"><stop offset="0%" stopColor="var(--acc-amber)" stopOpacity="0.35"/><stop offset="100%" stopColor="var(--acc-amber)" stopOpacity="0"/></radialGradient>
        <radialGradient id="glowR"><stop offset="0%" stopColor="var(--acc-red)" stopOpacity="0.45"/><stop offset="100%" stopColor="var(--acc-red)" stopOpacity="0"/></radialGradient>
        <radialGradient id="glowM"><stop offset="0%" stopColor="var(--acc-magenta)" stopOpacity="0.35"/><stop offset="100%" stopColor="var(--acc-magenta)" stopOpacity="0"/></radialGradient>
      </defs>

      <rect x="0" y="0" width={W} height={H} fill="url(#cgrid)"/>

      {/* Spokes from center to suns */}
      {!noVpn && vpnNodes.map(v => {
        const p = vpnPos[v.id]
        if (!p) return null
        return <line key={'spoke-' + v.id} x1={cx} y1={cy} x2={p.x} y2={p.y} stroke="var(--line-soft)" strokeWidth="0.5" strokeDasharray="1 4"/>
      })}

      {/* Center ctrl node */}
      <circle cx={cx} cy={cy} r="4" fill="var(--acc-green)" opacity="0.8"/>
      <text x={cx} y={cy + 14} textAnchor="middle" fontSize="7" fill="var(--fg-3)" fontFamily="var(--font-mono)" letterSpacing="1">CTRL</text>

      {/* Engine → VPN edges */}
      {engines.map(e => {
        const vpnKey = noVpn ? '__center__'
          : vpnNodes.find(v => v.label === e.vpn_container || v.id === e.vpn_container)?.id || '__none__'
        const sun = vpnPos[vpnKey] || vpnPos[Object.keys(vpnPos)[0]]
        const p = engPos[e.container_id]
        if (!sun || !p) return null
        const c = colorFor(e.health_status === 'healthy' ? 'healthy' : e.health_status === 'unhealthy' ? 'unhealthy' : 'pending')
        return <line key={'ve-' + e.container_id} x1={sun.x} y1={sun.y} x2={p.x} y2={p.y} stroke={c} strokeOpacity="0.4" strokeWidth="0.75"/>
      })}

      {/* Migration arc (pending_failover) */}
      {(() => {
        const src = engines.find(e => e.health_status === 'unhealthy')
        const dst = engines.find(e => e.health_status === 'healthy' && e.container_id !== src?.container_id)
        if (!src || !dst) return null
        const a = engPos[src.container_id], b = engPos[dst.container_id]
        if (!a || !b) return null
        const mx = (a.x + b.x) / 2 + (b.y - a.y) * 0.2
        const my = (a.y + b.y) / 2 - (b.x - a.x) * 0.2
        const path = `M ${a.x} ${a.y} Q ${mx} ${my} ${b.x} ${b.y}`
        return (
          <g>
            <path d={path} fill="none" stroke="var(--acc-magenta)" strokeWidth="1.4" strokeDasharray="4 3">
              <animate attributeName="stroke-dashoffset" from="0" to="-14" dur="0.8s" repeatCount="indefinite"/>
            </path>
            <circle r="3" fill="var(--acc-magenta)">
              <animateMotion dur="2.5s" repeatCount="indefinite" path={path}/>
            </circle>
          </g>
        )
      })()}

      {/* VPN suns */}
      {!noVpn && vpnNodes.map(v => {
        const p = vpnPos[v.id]
        if (!p) return null
        const glow = v.state === 'failed' ? 'glowR' : v.state === 'draining' ? 'glowA' : v.state === 'warming' ? 'glowM' : 'glowG'
        const ringSpan = 38 + 2 * 22 + 14
        const lx = p.x + Math.cos(p.angle) * ringSpan
        const ly = p.y + Math.sin(p.angle) * ringSpan
        const anchor = Math.cos(p.angle) > 0.3 ? 'start' : Math.cos(p.angle) < -0.3 ? 'end' : 'middle'
        return (
          <g key={v.id}>
            <circle cx={p.x} cy={p.y} r={sunR * 2.2} fill={`url(#${glow})`}/>
            <circle cx={p.x} cy={p.y} r={sunR} fill="var(--bg-0)" stroke={colorFor(v.state)} strokeWidth="1.5"/>
            <text x={p.x} y={p.y + 4} textAnchor="middle" fontSize="10" fill={colorFor(v.state)} fontFamily="var(--font-mono)" fontWeight="600">⌬</text>
            <rect
              x={anchor === 'start' ? lx - 2 : anchor === 'end' ? lx - 68 : lx - 35}
              y={ly - 14} width="70" height="22"
              fill="var(--bg-1)" stroke="var(--line)" opacity="0.92"
            />
            <text x={lx} y={ly - 3} textAnchor={anchor} fontSize="9" fill="var(--fg-0)" fontFamily="var(--font-mono)" fontWeight="600">
              {v.label.length > 10 ? v.label.slice(0, 10) + '…' : v.label}
            </text>
            <text x={lx} y={ly + 6} textAnchor={anchor} fontSize="7" fill={colorFor(v.state)} fontFamily="var(--font-mono)" letterSpacing="1">
              {v.state.toUpperCase()}
            </text>
          </g>
        )
      })}

      {/* Engine nodes */}
      {engines.map(e => {
        const p = engPos[e.container_id]
        if (!p) return null
        const c = colorFor(e.health_status === 'healthy' ? 'active' : e.health_status === 'unhealthy' ? 'failed' : 'pending')
        if (showLabel) {
          const name = (e.container_name || e.container_id).slice(-8)
          return (
            <g key={e.container_id}>
              <rect x={p.x - 24} y={p.y - 8} width="48" height="16" fill="var(--bg-0)" stroke={c} strokeWidth="1"/>
              <text x={p.x} y={p.y + 4} textAnchor="middle" fontSize="8" fill="var(--fg-0)" fontFamily="var(--font-mono)" fontWeight="600">{name}</text>
            </g>
          )
        }
        return (
          <rect
            key={e.container_id}
            x={p.x - engSize / 2} y={p.y - engSize / 2}
            width={engSize} height={engSize}
            fill={c} stroke="var(--bg-0)" strokeWidth="0.5"
            opacity={e.health_status === 'healthy' ? 1 : 0.6}
          />
        )
      })}

      {/* Legend */}
      <g transform={`translate(${W - 130}, ${H - 52})`}>
        <rect width="120" height="42" fill="var(--bg-0)" stroke="var(--line)" opacity="0.88"/>
        <text x="6" y="11" fontSize="7" fill="var(--fg-3)" fontFamily="var(--font-mono)" letterSpacing="1">LEGEND</text>
        <rect x="6" y="17" width="6" height="6" fill="var(--acc-green)"/>
        <text x="16" y="23" fontSize="7" fill="var(--fg-1)" fontFamily="var(--font-mono)">healthy</text>
        <rect x="52" y="17" width="6" height="6" fill="var(--acc-red)"/>
        <text x="62" y="23" fontSize="7" fill="var(--fg-1)" fontFamily="var(--font-mono)">failed</text>
        <rect x="96" y="17" width="6" height="6" fill="var(--acc-cyan)" opacity="0.5"/>
        <text x="6" y="36" fontSize="7" fill="var(--fg-3)" fontFamily="var(--font-mono)">⌬ vpn sun · □ engine</text>
      </g>
    </svg>
  )
}

// ── Alert Banner ──────────────────────────────────────────────────────────────
function AlertBanner({ title, message, accent = 'red' }) {
  return (
    <div style={{
      background: `var(--acc-${accent}-bg)`,
      border: `1px solid var(--acc-${accent}-dim)`,
      padding: '10px 14px',
      display: 'flex', alignItems: 'flex-start', gap: 10,
      flexShrink: 0,
    }}>
      <span className="dot pulse" style={{ color: `var(--acc-${accent})`, marginTop: 3, flexShrink: 0 }}/>
      <div>
        <div className="label" style={{ color: `var(--acc-${accent})`, marginBottom: 2 }}>{title}</div>
        <div style={{ fontSize: 11, color: 'var(--fg-1)' }}>{message}</div>
      </div>
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export function StreamingCentralPage({ engines, streams, vpnStatus, orchestratorStatus, orchUrl, apiKey }) {
  const {
    kpiHistory,
    vpnLeaseSummary,
    dashboardSnapshot,
    engineStatsById,
    selectedEngineId,
    logsByContainerId,
    logsLoadingByContainerId,
    logsErrorByContainerId,
    ingestLiveSnapshot,
    setDashboardSnapshot,
    setEngineStartEvents,
    setVpnLeaseSummary,
    refreshBackendTelemetry,
    openEngineLogs,
    closeEngineLogs,
    setContainerLogsSnapshot,
    setContainerLogsLoading,
    setContainerLogsError,
    fetchContainerLogs,
  } = useStreamingCentralStore(s => s)

  const [events, setEvents] = useState([])

  useEffect(() => {
    ingestLiveSnapshot({ engines, streams, vpnStatus, orchestratorStatus })
  }, [engines, streams, vpnStatus, orchestratorStatus, ingestLiveSnapshot])

  useEffect(() => {
    refreshBackendTelemetry({ orchUrl, apiKey })
    const interval = window.setInterval(() => refreshBackendTelemetry({ orchUrl, apiKey }), 30000)
    return () => window.clearInterval(interval)
  }, [orchUrl, apiKey, refreshBackendTelemetry])

  // Metrics SSE
  useEffect(() => {
    let eventSource = null, reconnectTimer = null, closed = false

    const connect = () => {
      if (closed) return
      if (typeof window === 'undefined' || !window.EventSource) return

      const url = new URL(`${orchUrl}/api/v1/metrics/stream`)
      url.searchParams.set('window_seconds', '900')
      url.searchParams.set('max_points', '240')
      if (apiKey) url.searchParams.set('api_key', apiKey)

      eventSource = new EventSource(url.toString())
      const handle = (ev) => {
        try { setDashboardSnapshot(JSON.parse(ev.data)?.payload || null) } catch {}
      }
      eventSource.addEventListener('metrics_snapshot', handle)
      eventSource.onmessage = handle
      eventSource.onerror = () => {
        if (eventSource) { eventSource.close(); eventSource = null }
        if (!closed) reconnectTimer = window.setTimeout(connect, 2000)
      }
    }

    connect()
    return () => {
      closed = true
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      if (eventSource) eventSource.close()
    }
  }, [orchUrl, apiKey, setDashboardSnapshot])

  // VPN leases SSE
  useEffect(() => {
    let eventSource = null, reconnectTimer = null, closed = false

    const connect = () => {
      if (closed) return
      if (typeof window === 'undefined' || !window.EventSource) return

      const url = new URL(`${orchUrl}/api/v1/vpn/leases/stream`)
      if (apiKey) url.searchParams.set('api_key', apiKey)

      eventSource = new EventSource(url.toString())
      const handle = (ev) => {
        try { setVpnLeaseSummary(JSON.parse(ev.data)?.payload || null) } catch {}
      }
      eventSource.addEventListener('vpn_leases_snapshot', handle)
      eventSource.onmessage = handle
      eventSource.onerror = () => {
        if (eventSource) { eventSource.close(); eventSource = null }
        if (!closed) reconnectTimer = window.setTimeout(connect, 2000)
      }
    }

    connect()
    return () => {
      closed = true
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      if (eventSource) eventSource.close()
    }
  }, [orchUrl, apiKey, setVpnLeaseSummary])

  // Events SSE for signal log
  useEffect(() => {
    let eventSource = null, reconnectTimer = null, closed = false

    const connect = () => {
      if (closed) return
      if (typeof window === 'undefined' || !window.EventSource) return

      const url = new URL(`${orchUrl}/api/v1/events/live`)
      url.searchParams.set('limit', '30')
      if (apiKey) url.searchParams.set('api_key', apiKey)

      eventSource = new EventSource(url.toString())
      const handle = (ev) => {
        try {
          const parsed = JSON.parse(ev.data)
          const evts = parsed?.payload?.events || []
          if (evts.length > 0) setEvents(prev => [...evts, ...prev].slice(0, 50))
        } catch {}
      }
      eventSource.addEventListener('events_snapshot', handle)
      eventSource.onmessage = handle
      eventSource.onerror = () => {
        if (eventSource) { eventSource.close(); eventSource = null }
        if (!closed) reconnectTimer = window.setTimeout(connect, 3000)
      }
    }

    connect()
    return () => {
      closed = true
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      if (eventSource) eventSource.close()
    }
  }, [orchUrl, apiKey])

  // Container logs SSE
  useEffect(() => {
    if (!selectedEngineId) return
    let eventSource = null, reconnectTimer = null, closed = false

    const connect = () => {
      if (closed) return
      setContainerLogsLoading({ containerId: selectedEngineId, loading: true })
      if (typeof window === 'undefined' || !window.EventSource) {
        fetchContainerLogs({ orchUrl, apiKey, containerId: selectedEngineId })
        return
      }

      const url = new URL(`${orchUrl}/api/v1/containers/${encodeURIComponent(selectedEngineId)}/logs/stream`)
      url.searchParams.set('tail', '300')
      url.searchParams.set('since_seconds', '1200')
      url.searchParams.set('interval_seconds', '2.5')
      if (apiKey) url.searchParams.set('api_key', apiKey)

      eventSource = new EventSource(url.toString())
      eventSource.addEventListener('container_logs_snapshot', (ev) => {
        try { setContainerLogsSnapshot({ containerId: selectedEngineId, payload: JSON.parse(ev.data)?.payload || {} }) }
        catch { setContainerLogsError({ containerId: selectedEngineId, error: 'parse error' }) }
      })
      eventSource.onerror = () => {
        setContainerLogsLoading({ containerId: selectedEngineId, loading: false })
        if (eventSource) { eventSource.close(); eventSource = null }
        if (!closed) reconnectTimer = window.setTimeout(connect, 2000)
      }
    }

    connect()
    return () => {
      closed = true
      if (reconnectTimer) window.clearTimeout(reconnectTimer)
      if (eventSource) eventSource.close()
    }
  }, [selectedEngineId, orchUrl, apiKey, fetchContainerLogs, setContainerLogsSnapshot, setContainerLogsLoading, setContainerLogsError])

  // ── Derived values ──────────────────────────────────────────────────────────
  const activeStreamsValue = Number(orchestratorStatus?.streams?.active ?? streams?.length ?? 0)
  const runningEnginesValue = Number(orchestratorStatus?.engines?.running ?? engines?.length ?? 0)
  const healthyEnginesValue = Number(
    orchestratorStatus?.engines?.healthy ?? engines.filter(e => e.health_status === 'healthy').length
  )
  const successRate = Number(
    dashboardSnapshot?.proxy?.request_window_1m?.success_rate_percent ??
    (orchestratorStatus?.status === 'healthy' ? 99.5 : 95)
  )
  const egressGbps = Number(dashboardSnapshot?.proxy?.throughput?.egress_mbps || 0) / 1000
  const egressDisplay = formatEgress(egressGbps)

  const vpnIncident =
    (vpnStatus?.mode === 'redundant' && (!vpnStatus?.vpn1?.connected || !vpnStatus?.vpn2?.connected)) ||
    (vpnStatus?.mode === 'single' && !vpnStatus?.connected)
  const breakerIncident =
    orchestratorStatus?.provisioning?.circuit_breaker_state &&
    orchestratorStatus.provisioning.circuit_breaker_state !== 'closed'
  const migrations = streams.filter(s => String(s.status || '').includes('failover')).length
  const isConnected = true // derived from parent SSE, we get data if we're here

  return (
    <div style={{
      display: 'flex', flexDirection: 'column',
      gap: 0,
      background: 'var(--bg-0)',
      backgroundImage: `radial-gradient(circle at 50% 40%, oklch(0.22 0.04 145 / 0.12), transparent 50%)`,
      minHeight: '100%',
    }}>
      <Marquee engines={engines} streams={streams} vpnStatus={vpnStatus} isConnected={true}/>

      {/* Alert banners */}
      {vpnIncident && (
        <AlertBanner
          title="VPN TUNNEL DEGRADATION"
          message="One or more VPN tunnels are disconnected. Monitor failover edges and check VPN settings."
          accent="red"
        />
      )}
      {breakerIncident && (
        <AlertBanner
          title={`CIRCUIT BREAKER · ${orchestratorStatus?.provisioning?.circuit_breaker_state?.toUpperCase()}`}
          message={orchestratorStatus?.provisioning?.blocked_reason || 'Provisioning is blocked. Circuit breaker is not closed.'}
          accent="amber"
        />
      )}

      {/* 3-column layout */}
      <div style={{ display: 'flex', gap: 12, padding: 16, flex: 1, alignItems: 'stretch', minHeight: 0 }}>

        {/* Left rail */}
        <div style={{ width: 220, display: 'flex', flexDirection: 'column', gap: 10, flexShrink: 0 }}>
          <BigCounter label="ACTIVE.STREAMS" value={String(activeStreamsValue)} accent="green"/>
          <BigCounter label="ACTIVE.ENGINES" value={String(healthyEnginesValue).padStart(2, '0')} accent="green"/>
          <BigCounter
            label="SUCCESS.RATE"
            value={`${Number(successRate).toFixed(1)}`}
            sub="%"
            accent={successRate < 97 ? 'red' : 'green'}
          />
          <BigCounter
            label="EGRESS"
            value={egressDisplay.value}
            sub={egressDisplay.suffix}
            accent="cyan"
          />
          <PolicyBlock orchestratorStatus={orchestratorStatus}/>
        </div>

        {/* Constellation canvas */}
        <div style={{
          flex: 1,
          display: 'flex', flexDirection: 'column',
          background: 'var(--bg-1)',
          border: '1px solid var(--acc-green-dim)',
          boxShadow: '0 0 0 1px var(--acc-green-bg) inset',
          minWidth: 0,
          minHeight: 480,
        }}>
          {/* Canvas header */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: 10,
            padding: '10px 14px',
            borderBottom: '1px solid var(--line)',
            flexShrink: 0,
          }}>
            <span className="dot pulse" style={{ color: 'var(--acc-green)' }}/>
            <span className="label">CONTROL.MESH /// EU</span>
            <span style={{ fontSize: 10, color: 'var(--fg-2)' }}>
              engines {engines.length} · streams {activeStreamsValue}
              {vpnStatus?.mode !== 'disabled' && ` · vpn ${vpnStatus?.mode || 'disabled'}`}
            </span>
            <div style={{ flex: 1 }}/>
            {migrations > 0 && (
              <span className="tag tag-magenta"><span className="dot pulse"/>MIG-{migrations}</span>
            )}
            {vpnIncident && (
              <span className="tag tag-red"><span className="dot pulse"/>VPN FAIL</span>
            )}
          </div>

          {/* SVG constellation */}
          <div style={{ flex: 1, padding: 12, position: 'relative', minHeight: 340 }}>
            <ConstellationGraph engines={engines} vpnStatus={vpnStatus}/>
          </div>

          {/* Waveform */}
          <SessionWaveform kpiHistory={kpiHistory}/>
        </div>

        {/* Signal log */}
        <div style={{
          width: 320,
          display: 'flex', flexDirection: 'column',
          background: 'var(--bg-1)',
          border: '1px solid var(--line-soft)',
          flexShrink: 0,
        }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '10px 14px',
            borderBottom: '1px solid var(--line-soft)',
            flexShrink: 0,
          }}>
            <span className="label">SIGNAL.LOG</span>
            <div style={{ flex: 1 }}/>
            <span style={{ fontSize: 10, color: 'var(--acc-green)' }}>● rec</span>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
            {events.length === 0 ? (
              <div style={{ padding: 14, fontSize: 10, color: 'var(--fg-3)', fontStyle: 'italic' }}>
                awaiting events...
              </div>
            ) : (
              events.slice(0, 40).map((e, i) => <SignalRow key={i} e={e} idx={i}/>)
            )}
          </div>
        </div>
      </div>

      {/* Container logs drawer */}
      {selectedEngineId && (
        <div style={{
          position: 'fixed', top: 0, right: 0, bottom: 0,
          width: 640,
          background: 'var(--bg-1)',
          borderLeft: '1px solid var(--line-soft)',
          display: 'flex', flexDirection: 'column',
          zIndex: 200,
        }}>
          <div style={{
            display: 'flex', alignItems: 'center', gap: 8,
            padding: '12px 16px',
            borderBottom: '1px solid var(--line-soft)',
            flexShrink: 0,
          }}>
            <span className="label">CONTAINER LOGS · {selectedEngineId.slice(0, 12)}</span>
            <div style={{ flex: 1 }}/>
            <button
              onClick={() => closeEngineLogs()}
              style={{
                background: 'transparent', border: '1px solid var(--line)',
                color: 'var(--fg-1)', cursor: 'pointer', padding: '2px 8px', fontSize: 10,
                fontFamily: 'var(--font-mono)',
              }}
            >✕ CLOSE</button>
          </div>
          <div style={{ flex: 1, overflowY: 'auto', minHeight: 0 }}>
            <pre style={{
              padding: '12px 16px',
              fontFamily: 'var(--font-mono)',
              fontSize: 11,
              lineHeight: 1.6,
              color: 'var(--fg-0)',
              margin: 0,
            }}>
              {logsLoadingByContainerId?.[selectedEngineId]
                ? 'Loading logs...'
                : (logsByContainerId?.[selectedEngineId]?.lines || []).join('\n') || 'No logs available.'}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}
