import React, { useState, useEffect, useCallback } from 'react'
import { useNotifications } from '@/context/NotificationContext'
import { EngineConfiguration } from '@/components/CustomEngineBlocks'
import { ManualEngineList } from '@/components/ManualEngineList'

// ── Helpers ───────────────────────────────────────────────────────────────────
function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B'
  const k = 1024
  const sizes = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(k))
  return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
}

function AsciiBar({ value, max = 100, width = 10, color = 'var(--acc-green)' }) {
  const filled = Math.round((Math.min(value, max) / max) * width)
  const empty = width - filled
  return (
    <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--fg-3)', fontSize: 11 }}>
      [<span style={{ color }}>{'█'.repeat(filled)}</span>{'░'.repeat(empty)}]
    </span>
  )
}

function StatusTag({ status }) {
  const map = {
    healthy: 'green', active: 'green',
    unhealthy: 'red', failed: 'red',
    unknown: 'amber', pending: 'amber',
    draining: 'amber',
  }
  const color = map[status?.toLowerCase()] || 'amber'
  return <span className={`tag tag-${color}`}><span className="dot"/>{(status || 'unknown').toUpperCase()}</span>
}

// ── Rack Row ──────────────────────────────────────────────────────────────────
function EngineRackRow({ engine, idx, onDelete, maxStreamsPerEngine }) {
  const status = engine.health_status || 'unknown'
  const accent = {
    healthy: 'var(--acc-green)',
    unhealthy: 'var(--acc-red)',
    unknown: 'var(--acc-amber)',
    pending: 'var(--acc-cyan)',
  }[status] || 'var(--fg-2)'

  const cpu = Number(engine.docker_stats?.cpu_percent || 0)
  const memMb = Math.round(Number(engine.docker_stats?.memory_usage || 0) / 1024 / 1024)
  const streamCount = engine.stream_count ?? 0
  const name = engine.container_name || engine.container_id?.slice(0, 12) || '–'

  // Session blocks
  const sessionBlocks = Array.from({ length: Math.min(streamCount, 8) }).map((_, i) => (
    <div key={i} style={{
      flex: 1, height: 14,
      background: accent,
      opacity: 0.85 - i * 0.08,
      position: 'relative',
      overflow: 'hidden',
    }}>
      <div style={{
        position: 'absolute', inset: 0,
        background: 'repeating-linear-gradient(90deg, transparent 0 4px, rgba(0,0,0,0.18) 4px 5px)',
        animation: status === 'healthy' ? 'flow 1.2s linear infinite' : 'none',
      }}/>
    </div>
  ))

  return (
    <div style={{
      display: 'flex', alignItems: 'center',
      borderBottom: '1px solid var(--line-soft)',
      background: idx % 2 === 0 ? 'var(--bg-1)' : 'var(--bg-0)',
      height: 40,
      minWidth: 0,
    }}>
      {/* Slot */}
      <div style={{ width: 32, padding: '0 6px', borderRight: '1px solid var(--line-soft)', fontSize: 10, color: 'var(--fg-3)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
        U{String(idx + 1).padStart(2, '0')}
      </div>
      {/* Status bar */}
      <div style={{ width: 4, background: accent, alignSelf: 'stretch', flexShrink: 0 }}/>
      {/* Name */}
      <div style={{ width: 140, padding: '0 10px', display: 'flex', alignItems: 'center', gap: 6, overflow: 'hidden', flexShrink: 0 }}>
        <span style={{ fontSize: 11, color: 'var(--fg-0)', fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {name}
        </span>
      </div>
      {/* Port */}
      <div style={{ width: 72, padding: '0 8px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', fontSize: 10, color: 'var(--fg-2)', flexShrink: 0 }}>
        :{engine.port || '–'}
      </div>
      {/* VPN */}
      <div style={{ width: 120, padding: '0 8px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', fontSize: 10, color: 'var(--acc-cyan)', overflow: 'hidden', flexShrink: 0 }}>
        <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {engine.vpn_container ? `⌬ ${engine.vpn_container}` : '— none'}
        </span>
      </div>
      {/* Status */}
      <div style={{ width: 96, padding: '0 8px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', flexShrink: 0 }}>
        <StatusTag status={status}/>
      </div>
      {/* CPU */}
      <div style={{ width: 136, padding: '0 8px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
        <span style={{ fontSize: 10, color: 'var(--fg-2)' }}>cpu</span>
        <AsciiBar value={cpu} width={10} color={cpu > 80 ? 'var(--acc-red)' : cpu > 50 ? 'var(--acc-amber)' : 'var(--acc-green)'}/>
        <span style={{ fontSize: 10, color: 'var(--fg-1)', fontVariantNumeric: 'tabular-nums', width: 36, flexShrink: 0 }}>{cpu.toFixed(1)}%</span>
      </div>
      {/* Mem */}
      <div style={{ width: 72, padding: '0 8px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', fontSize: 10, color: 'var(--fg-1)', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
        {memMb}M
      </div>
      {/* Sessions */}
      <div style={{ flex: 1, borderLeft: '1px solid var(--line-soft)', padding: 8, display: 'flex', alignItems: 'center', gap: 2, minWidth: 80 }}>
        {sessionBlocks.length > 0
          ? sessionBlocks
          : <span style={{ fontSize: 10, color: 'var(--fg-3)', fontStyle: 'italic' }}>— idle —</span>
        }
      </div>
      {/* Count */}
      <div style={{ width: 56, padding: '0 10px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', justifyContent: 'flex-end', fontSize: 11, color: 'var(--fg-1)', fontVariantNumeric: 'tabular-nums', flexShrink: 0 }}>
        {streamCount}<span style={{ color: 'var(--fg-3)' }}>/
          {Number.isFinite(maxStreamsPerEngine) ? maxStreamsPerEngine : 0}
        </span>
      </div>
      {/* Actions */}
      <div style={{ width: 40, borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
        <button
          onClick={() => onDelete(engine.container_id)}
          style={{
            background: 'transparent', border: 0,
            color: 'var(--fg-3)', cursor: 'pointer', fontSize: 12,
            padding: '4px 8px',
            fontFamily: 'var(--font-mono)',
          }}
          title="Delete engine"
        >✕</button>
      </div>
    </div>
  )
}

// ── Column header ─────────────────────────────────────────────────────────────
function RackHeader() {
  return (
    <div style={{
      display: 'flex', alignItems: 'center',
      height: 24,
      background: 'var(--bg-2)',
      borderBottom: '1px solid var(--line)',
      fontSize: 9, letterSpacing: '0.1em', color: 'var(--fg-3)',
      fontFamily: 'var(--font-mono)',
    }}>
      <div style={{ width: 32, padding: '0 6px', borderRight: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>SLOT</div>
      <div style={{ width: 4, flexShrink: 0 }}/>
      <div style={{ width: 140, padding: '0 10px', display: 'flex', alignItems: 'center', flexShrink: 0 }}>ID</div>
      <div style={{ width: 72, padding: '0 8px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', flexShrink: 0 }}>PORT</div>
      <div style={{ width: 120, padding: '0 8px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', flexShrink: 0 }}>VPN</div>
      <div style={{ width: 96, padding: '0 8px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', flexShrink: 0 }}>STATUS</div>
      <div style={{ width: 136, padding: '0 8px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', flexShrink: 0 }}>CPU</div>
      <div style={{ width: 72, padding: '0 8px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', flexShrink: 0 }}>MEM</div>
      <div style={{ flex: 1, borderLeft: '1px solid var(--line-soft)', padding: '0 10px', display: 'flex', alignItems: 'center' }}>SESSIONS</div>
      <div style={{ width: 56, padding: '0 10px', borderLeft: '1px solid var(--line-soft)', display: 'flex', alignItems: 'center', justifyContent: 'flex-end', flexShrink: 0 }}>USED</div>
      <div style={{ width: 40, borderLeft: '1px solid var(--line-soft)', flexShrink: 0 }}/>
    </div>
  )
}

// ── Settings panel ────────────────────────────────────────────────────────────
function CfgRow({ k, v }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 11, padding: '8px 14px', borderBottom: '1px dashed var(--line-soft)' }}>
      <span style={{ color: 'var(--fg-3)', flex: 1 }}>{k}</span>
      <span style={{ color: 'var(--fg-0)' }}>{v}</span>
    </div>
  )
}

function Toggle({ checked, onChange, disabled }) {
  return (
    <button
      onClick={() => !disabled && onChange(!checked)}
      style={{
        width: 36, height: 18,
        background: checked ? 'var(--acc-green-bg)' : 'var(--bg-2)',
        border: `1px solid ${checked ? 'var(--acc-green-dim)' : 'var(--line)'}`,
        borderRadius: 2,
        cursor: disabled ? 'not-allowed' : 'pointer',
        display: 'flex', alignItems: 'center',
        padding: '0 2px',
        transition: 'background 0.15s',
        opacity: disabled ? 0.5 : 1,
      }}
    >
      <div style={{
        width: 12, height: 12,
        background: checked ? 'var(--acc-green)' : 'var(--fg-3)',
        borderRadius: 1,
        transform: checked ? 'translateX(18px)' : 'translateX(0)',
        transition: 'transform 0.15s, background 0.15s',
      }}/>
    </button>
  )
}

function SettingField({ label, desc, value, onChange, type = 'text', disabled }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '10px 14px', borderBottom: '1px solid var(--line-soft)' }}>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 11, color: 'var(--fg-1)' }}>{label}</div>
        {desc && <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 2 }}>{desc}</div>}
      </div>
      {type === 'toggle' ? (
        <Toggle checked={Boolean(value)} onChange={onChange} disabled={disabled}/>
      ) : (
        <input
          type={type === 'number' ? 'number' : 'text'}
          value={value}
          onChange={e => onChange(type === 'number' ? (parseInt(e.target.value) || 0) : e.target.value)}
          disabled={disabled}
          style={{
            background: 'var(--bg-0)',
            border: '1px solid var(--line)',
            color: 'var(--fg-0)',
            fontFamily: 'var(--font-mono)',
            fontSize: 11,
            padding: '4px 8px',
            width: 120,
            outline: 'none',
          }}
        />
      )}
    </div>
  )
}

// ── Main Page ─────────────────────────────────────────────────────────────────
export function EnginesPage({ engines, onDeleteEngine, vpnStatus, orchUrl, apiKey, fetchJSON }) {
  const { addNotification } = useNotifications()
  const [activeTab, setActiveTab] = useState('rack')

  const [engineSettings, setEngineSettings] = useState({
    min_replicas: 2, max_replicas: 6, auto_delete: true,
    live_cache_type: 'memory', total_max_download_rate: 0,
    total_max_upload_rate: 0, buffer_time: 10, max_peers: 50,
    memory_limit: null, manual_mode: false, manual_engines: [],
  })
  const [loadingSettings, setLoadingSettings] = useState(true)
  const [savingSettings, setSavingSettings] = useState(false)
  const [settingsChanged, setSettingsChanged] = useState(false)
  const [cacheStats, setCacheStats] = useState({ total_bytes: 0, volume_count: 0 })
  const [maxStreamsPerEngine, setMaxStreamsPerEngine] = useState(0)

  const loadEngineSettings = useCallback(async () => {
    try {
      setLoadingSettings(true)
      const s = await fetchJSON(`${orchUrl}/api/v1/settings/engine`)
      setEngineSettings(s)
      setSettingsChanged(false)
    } catch (err) {
      addNotification(`Failed to load engine settings: ${err.message}`, 'error')
    } finally {
      setLoadingSettings(false)
    }
  }, [orchUrl, fetchJSON, addNotification])

  const loadProxySettings = useCallback(async () => {
    try {
      const response = await fetchJSON(`${orchUrl}/api/v1/proxy/config`)
      const maxStreams = Number(response?.max_streams_per_engine)
      setMaxStreamsPerEngine(Number.isFinite(maxStreams) ? maxStreams : 0)
    } catch {
      setMaxStreamsPerEngine(0)
    }
  }, [orchUrl, fetchJSON])

  const loadCacheStats = useCallback(async () => {
    try {
      const s = await fetchJSON(`${orchUrl}/api/v1/engine-cache/stats`)
      setCacheStats(s)
    } catch { /* best-effort */ }
  }, [orchUrl, fetchJSON])

  useEffect(() => {
    loadEngineSettings()
    loadProxySettings()
    loadCacheStats()
    const interval = setInterval(loadCacheStats, 30000)
    return () => clearInterval(interval)
  }, [loadEngineSettings, loadProxySettings, loadCacheStats])

  const handleSettingChange = (key, value) => {
    setEngineSettings(prev => ({ ...prev, [key]: value }))
    setSettingsChanged(true)
  }

  const handleSaveSettings = useCallback(async () => {
    try {
      setSavingSettings(true)
      await fetchJSON(`${orchUrl}/api/v1/settings/engine`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Authorization': `Bearer ${apiKey}` },
        body: JSON.stringify(engineSettings),
      })
      addNotification('Engine settings saved successfully', 'success')
      setSettingsChanged(false)
    } catch (err) {
      addNotification(`Failed to save engine settings: ${err.message}`, 'error')
    } finally {
      setSavingSettings(false)
    }
  }, [orchUrl, fetchJSON, engineSettings, apiKey, addNotification])

  const healthy = engines.filter(e => e.health_status === 'healthy').length
  const unhealthy = engines.filter(e => e.health_status === 'unhealthy').length
  const unknown = engines.filter(e => !e.health_status || e.health_status === 'unknown').length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* Page header */}
      <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
        <div>
          <h1 style={{
            fontFamily: 'var(--font-display)',
            fontSize: 18, fontWeight: 600,
            color: 'var(--fg-0)', margin: 0,
          }}>Engines</h1>
          <div style={{ fontSize: 11, color: 'var(--fg-2)', marginTop: 2 }}>
            {engines.length} containers · {healthy} healthy · {unhealthy} unhealthy · {unknown} unknown
            {cacheStats.volume_count > 0 && ` · cache ${formatBytes(cacheStats.total_bytes)}`}
          </div>
        </div>
        <div style={{ flex: 1 }}/>
        <span className={`tag tag-green`} style={{ cursor: 'pointer' }}>
          <span className="dot"/> HEALTHY {healthy}
        </span>
        {unhealthy > 0 && (
          <span className="tag tag-red">
            <span className="dot"/> UNHEALTHY {unhealthy}
          </span>
        )}
      </div>

      {/* Tab bar */}
      <div style={{
        display: 'flex', gap: 1,
        background: 'var(--bg-1)',
        border: '1px solid var(--line-soft)',
        padding: 1,
        width: 'fit-content',
      }}>
        {[['rack', 'RACK · ENGINE STATUS'], ['settings', 'ENGINE SETTINGS']].map(([id, label]) => (
          <button key={id} onClick={() => setActiveTab(id)} style={{
            padding: '6px 14px',
            background: activeTab === id ? 'var(--bg-3)' : 'transparent',
            border: 0,
            color: activeTab === id ? 'var(--fg-0)' : 'var(--fg-2)',
            fontFamily: 'var(--font-mono)',
            fontSize: 10,
            letterSpacing: '0.08em',
            cursor: 'pointer',
            borderLeft: activeTab === id ? '2px solid var(--acc-green)' : '2px solid transparent',
          }}>{label}</button>
        ))}
      </div>

      {/* Rack view */}
      {activeTab === 'rack' && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{
            background: 'var(--bg-1)',
            border: '1px solid var(--line-soft)',
            overflow: 'hidden',
          }}>
            {/* Rack header row */}
            <div style={{
              display: 'flex', alignItems: 'center', gap: 10,
              padding: '10px 14px',
              borderBottom: '1px solid var(--line)',
            }}>
              <span className="label">RACK · ENGINES</span>
              <span style={{ fontSize: 10, color: 'var(--fg-2)' }}>
                {engines.length} units
              </span>
              <div style={{ flex: 1 }}/>
            </div>
            <RackHeader/>
            {engines.length === 0 ? (
              <div style={{ padding: 24, fontSize: 11, color: 'var(--fg-3)', textAlign: 'center', fontStyle: 'italic' }}>
                No engines provisioned
              </div>
            ) : (
              engines.map((e, i) => (
                <EngineRackRow
                  key={e.container_id}
                  engine={e}
                  idx={i}
                  onDelete={onDeleteEngine}
                  maxStreamsPerEngine={maxStreamsPerEngine}
                />
              ))
            )}
          </div>
        </div>
      )}

      {/* Settings */}
      {activeTab === 'settings' && (
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
          <div style={{
            flex: 1,
            background: 'var(--bg-1)',
            border: '1px solid var(--line-soft)',
          }}>
            {/* Panel header */}
            <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)' }}>
              <span className="label">ENGINE POOL MANAGEMENT</span>
            </div>

            <SettingField
              label="Pool Management Mode"
              desc={engineSettings.manual_mode ? 'Manual: specify external engines' : 'Auto: manage Docker lifecycle'}
              value={engineSettings.manual_mode}
              onChange={v => handleSettingChange('manual_mode', v)}
              type="toggle"
              disabled={loadingSettings}
            />

            {!engineSettings.manual_mode ? (
              <>
                <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)', marginTop: 8 }}>
                  <span className="label">SCALING</span>
                </div>
                <SettingField
                  label="Min Replicas"
                  desc="Minimum engines to keep running (0–50)"
                  value={engineSettings.min_replicas}
                  onChange={v => handleSettingChange('min_replicas', v)}
                  type="number"
                  disabled={loadingSettings}
                />
                <SettingField
                  label="Max Replicas"
                  desc="Maximum allowed engines (1–100)"
                  value={engineSettings.max_replicas}
                  onChange={v => handleSettingChange('max_replicas', v)}
                  type="number"
                  disabled={loadingSettings}
                />
                <SettingField
                  label="Auto Cleanup"
                  desc="Delete engines automatically when stopped"
                  value={engineSettings.auto_delete}
                  onChange={v => handleSettingChange('auto_delete', v)}
                  type="toggle"
                  disabled={loadingSettings}
                />
                <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)', marginTop: 8 }}>
                  <span className="label">ADVANCED</span>
                </div>
                <EngineConfiguration
                  engineSettings={engineSettings}
                  onSettingChange={handleSettingChange}
                  disabled={loadingSettings}
                />
              </>
            ) : (
              <div style={{ padding: 14 }}>
                <div style={{
                  background: 'var(--acc-cyan-bg)',
                  border: '1px solid var(--acc-cyan-dim)',
                  padding: '10px 14px',
                  marginBottom: 12,
                }}>
                  <div className="label" style={{ color: 'var(--acc-cyan)', marginBottom: 4 }}>MANUAL MODE ACTIVE</div>
                  <div style={{ fontSize: 11, color: 'var(--fg-1)' }}>
                    Docker provisioning is disabled. Only specified engines below are used.
                  </div>
                </div>
                <ManualEngineList
                  engines={engineSettings.manual_engines || []}
                  onChange={newList => handleSettingChange('manual_engines', newList)}
                  disabled={loadingSettings}
                />
              </div>
            )}

            {/* Save button */}
            <div style={{ padding: '12px 14px', borderTop: '1px solid var(--line)', display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button
                onClick={handleSaveSettings}
                disabled={savingSettings || loadingSettings || !settingsChanged}
                className="tag tag-green"
                style={{
                  cursor: savingSettings || loadingSettings || !settingsChanged ? 'not-allowed' : 'pointer',
                  opacity: savingSettings || loadingSettings || !settingsChanged ? 0.5 : 1,
                  padding: '4px 12px',
                }}
              >
                {savingSettings ? '⟳ SAVING...' : '✓ SAVE SETTINGS'}
              </button>
            </div>
          </div>
        </div>
      )}

      <style>{`@keyframes flow { from { background-position: 0 0; } to { background-position: 12px 0; } }`}</style>
    </div>
  )
}
