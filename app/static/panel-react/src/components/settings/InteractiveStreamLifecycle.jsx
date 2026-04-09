import React, { useEffect, useRef } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import { AnimatePresence, motion } from 'framer-motion'
import {
  AlertCircle,
  Download,
  Link,
  MonitorPlay,
  Play,
  PowerOff,
  RefreshCcw,
  Unplug,
} from 'lucide-react'

const NORMAL_TRACK = [
  { id: 'request', label: 'Player Requests Stream', icon: Play },
  { id: 'upstream', label: 'Upstream Connected', icon: Link },
  { id: 'first_byte', label: 'First Video Byte Received', icon: Download },
  { id: 'buffer_full', label: 'Proxy Buffer Full (Playback Starts)', icon: MonitorPlay },
  { id: 'disconnect', label: 'Player Disconnects', icon: Unplug },
  { id: 'shutdown', label: 'Proxy Shuts Down', icon: PowerOff },
]

const STARVATION_TRACK = [
  { id: 'active_playback', label: 'Active Playback', icon: MonitorPlay },
  { id: 'video_stops', label: 'Video Data Stops Arriving', icon: Download },
  { id: 'no_data_phase', label: 'No Data Detection Phase', icon: AlertCircle },
  { id: 'forced_reconnect', label: 'Forced Reconnect / Teardown', icon: RefreshCcw },
]

const GAP_PHASES = {
  upstream_connect: { track: 'normal', from: 0, to: 1 },
  initial_data_wait: { track: 'normal', from: 1, to: 2 },
  proxy_prebuffer: { track: 'normal', from: 2, to: 3 },
  no_data_detection: { track: 'starvation', from: 1, to: 2 },
  health_monitor_reconnect: { track: 'starvation', from: 2, to: 3 },
  idle_shutdown: { track: 'normal', from: 4, to: 5 },
}

const NODE_PHASES = {
  live_edge_delay: { track: 'starvation', index: 0 },
}

const PRIMARY_NODE_PHASES = {
  upstream_connect: { track: 'normal', index: 1 },
  initial_data_wait: { track: 'normal', index: 2 },
  proxy_prebuffer: { track: 'normal', index: 3 },
  live_edge_delay: { track: 'starvation', index: 0 },
  no_data_detection: { track: 'starvation', index: 2 },
  health_monitor_reconnect: { track: 'starvation', index: 3 },
  idle_shutdown: { track: 'normal', index: 5 },
  overall_stream_timeout: { track: 'normal', index: 0 },
}

const TRACK_BY_PHASE = {
  upstream_connect: 'normal',
  initial_data_wait: 'normal',
  proxy_prebuffer: 'normal',
  idle_shutdown: 'normal',
  overall_stream_timeout: 'normal',
  live_edge_delay: 'starvation',
  no_data_detection: 'starvation',
  health_monitor_reconnect: 'starvation',
}

const containerVariants = {
  hidden: { opacity: 1 },
  visible: {
    opacity: 1,
    transition: {
      staggerChildren: 0.1,
      delayChildren: 0.05,
    },
  },
}

const itemVariants = {
  hidden: {
    opacity: 0,
    scale: 0,
  },
  visible: {
    opacity: 1,
    scale: 1,
    transition: {
      type: 'spring',
      stiffness: 300,
      damping: 24,
    },
  },
}

const PHASE_CONTENT = {
  upstream_connect: {
    title: 'Upstream Connect Timeout',
    description: 'Controls how long the proxy waits to establish an upstream connection after the player first requests the stream.',
  },
  initial_data_wait: {
    title: 'Initial Data Wait',
    description: 'Defines the startup wait window and polling cadence after upstream is connected but before the first video byte arrives.',
  },
  proxy_prebuffer: {
    title: 'Proxy Prebuffer',
    description: 'Represents the pre-playback buffering runway between first byte arrival and the point where playback can safely start.',
  },
  live_edge_delay: {
    title: 'Live Edge Delay',
    description: 'Applies during active playback to keep clients slightly behind the live edge for smoother recovery and fewer stalls.',
  },
  no_data_detection: {
    title: 'No Data Detection',
    description: 'Marks the starvation detection window after data stops arriving and before the system confirms an unhealthy stream.',
  },
  health_monitor_reconnect: {
    title: 'Health Monitor Reconnect',
    description: 'Defines the recovery handoff where the monitor escalates from no-data detection into forced reconnect or teardown.',
  },
  idle_shutdown: {
    title: 'Idle Shutdown Delay',
    description: 'Represents the grace period after player disconnect before the proxy process is terminated.',
  },
  overall_stream_timeout: {
    title: 'Overall Stream Timeout',
    description: 'Covers the full normal stream lifecycle from initial request through idle teardown as one upper-bound timeout budget.',
  },
  default: {
    title: 'Stream Lifecycle Map',
    description: 'Hover or focus a related setting to highlight the exact timeline window where that control applies.',
  },
}

function TimelineTrack({
  trackId,
  title,
  nodes,
  activePhase,
}) {
  const scrollRef = useRef(null)
  const nodeRefs = useRef([])
  const connectorRefs = useRef([])

  const hasSelection = Boolean(activePhase)
  const activeGap = GAP_PHASES[activePhase]
  const activeNode = NODE_PHASES[activePhase]
  const primaryNode = PRIMARY_NODE_PHASES[activePhase]
  const trackWideActive = activePhase === 'overall_stream_timeout' && trackId === 'normal'

  useEffect(() => {
    const container = scrollRef.current
    if (!container || !activePhase) return

    let target = null

    if (activePhase === 'overall_stream_timeout' && trackId === 'normal') {
      target = nodeRefs.current[2] || nodeRefs.current[0] || null
    } else if (activeGap && activeGap.track === trackId) {
      target = connectorRefs.current[activeGap.from] || nodeRefs.current[activeGap.to] || null
    } else if (primaryNode && primaryNode.track === trackId) {
      target = nodeRefs.current[primaryNode.index] || null
    } else if (activeNode && activeNode.track === trackId) {
      target = nodeRefs.current[activeNode.index] || null
    }

    if (!target) return

    const centerTarget = () => {
      const targetCenter = target.offsetLeft + target.offsetWidth / 2
      const nextLeft = Math.max(0, targetCenter - container.clientWidth / 2)
      container.scrollTo({ left: nextLeft, behavior: 'smooth' })
    }

    let raf2 = 0
    const raf1 = window.requestAnimationFrame(() => {
      raf2 = window.requestAnimationFrame(centerTarget)
    })

    return () => {
      window.cancelAnimationFrame(raf1)
      if (raf2) window.cancelAnimationFrame(raf2)
    }
  }, [activeGap, activeNode, activePhase, primaryNode, trackId])

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{title}</p>
      </div>

      <div ref={scrollRef} className="overflow-x-auto pb-2">
        <motion.div
          className="flex min-w-max items-start px-1"
          variants={containerVariants}
          initial="hidden"
          animate="visible"
        >
          {nodes.map((node, index) => {
            const Icon = node.icon
            const nodeInActiveGap = Boolean(
              activeGap &&
              activeGap.track === trackId &&
              (index === activeGap.from || index === activeGap.to),
            )
            const nodeExplicitlyActive = Boolean(
              activeNode &&
              activeNode.track === trackId &&
              activeNode.index === index,
            )
            const nodeActive = trackWideActive || nodeInActiveGap || nodeExplicitlyActive
            const nodeHighlightActive = Boolean(
              primaryNode &&
              primaryNode.track === trackId &&
              primaryNode.index === index,
            )

            const connectorActive =
              index < nodes.length - 1 &&
              (trackWideActive || Boolean(
                activeGap &&
                activeGap.track === trackId &&
                activeGap.from === index &&
                activeGap.to === index + 1,
              ))

            return (
              <div
                key={node.id}
                className="flex items-start"
                ref={(element) => {
                  nodeRefs.current[index] = element
                }}
              >
                <motion.div className="w-36 shrink-0" variants={itemVariants}>
                  <div className="flex flex-col items-center gap-2 text-center">
                    <div
                      className={cn(
                        'relative flex h-11 w-11 items-center justify-center rounded-full border border-border bg-background text-muted-foreground transition-all',
                        hasSelection && !nodeActive && 'opacity-50',
                      )}
                    >
                      {nodeHighlightActive && (
                        <motion.div
                          layoutId="activeHighlight"
                          className="absolute inset-0 rounded-full ring-2 ring-primary bg-primary/20"
                          transition={{ type: 'spring', stiffness: 300, damping: 30 }}
                        />
                      )}
                      <Icon className={cn('relative z-10 h-5 w-5', nodeActive && 'text-primary')} />
                    </div>
                    <p
                      className={cn(
                        'text-[11px] leading-4 text-muted-foreground transition-opacity',
                        nodeActive && 'font-medium text-foreground',
                        hasSelection && !nodeActive && 'opacity-50',
                      )}
                    >
                      {node.label}
                    </p>
                  </div>
                </motion.div>

                {index < nodes.length - 1 && (
                  <div
                    className="flex w-16 items-center pt-5"
                    ref={(element) => {
                      connectorRefs.current[index] = element
                    }}
                  >
                    <div
                      className={cn(
                        'h-1 w-full rounded-full bg-border transition-all',
                        connectorActive && 'bg-primary/30 ring-2 ring-primary',
                        hasSelection && !connectorActive && 'opacity-50',
                      )}
                    />
                  </div>
                )}
              </div>
            )
          })}
        </motion.div>
      </div>
    </div>
  )
}

export function InteractiveStreamLifecycle({ activePhase }) {
  const copy = PHASE_CONTENT[activePhase] || PHASE_CONTENT.default
  const trackId = TRACK_BY_PHASE[activePhase] || 'normal'
  const track = trackId === 'starvation'
    ? { id: 'starvation', title: 'Failure / Starvation Lifecycle', nodes: STARVATION_TRACK }
    : { id: 'normal', title: 'Normal Lifecycle', nodes: NORMAL_TRACK }

  return (
    <div className="space-y-4">
      <Card className="border-slate-200/70 bg-white/70 dark:border-slate-800 dark:bg-slate-900/50">
        <CardContent className="space-y-6 p-4 sm:p-6">
          <TimelineTrack
            trackId={track.id}
            title={track.title}
            nodes={track.nodes}
            activePhase={activePhase}
          />
        </CardContent>
      </Card>

      <Card className="border-dashed border-primary/40 bg-primary/5 dark:bg-primary/10">
        <CardContent className="space-y-1 p-4">
          <AnimatePresence mode="wait">
            <motion.div
              key={activePhase || 'default'}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2, ease: 'easeOut' }}
              className="space-y-1"
            >
              <p className="text-sm font-semibold text-foreground">{copy.title}</p>
              <p className="text-sm text-muted-foreground">{copy.description}</p>
            </motion.div>
          </AnimatePresence>
        </CardContent>
      </Card>
    </div>
  )
}
