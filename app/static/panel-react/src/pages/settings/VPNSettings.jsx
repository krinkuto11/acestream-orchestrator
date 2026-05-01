import React, { useEffect, useMemo, useState } from 'react'
import { SettingRow } from '@/components/settings/SettingRow'
import { useSettingsForm } from '@/context/SettingsFormContext'

const PROVIDER_OPTIONS = [
  { value: 'protonvpn', label: 'ProtonVPN' },
  { value: 'private internet access', label: 'Private Internet Access (PIA)' },
  { value: 'privatevpn', label: 'PrivateVPN' },
  { value: 'perfect privacy', label: 'Perfect Privacy' },
  { value: 'mullvad', label: 'Mullvad' },
  { value: 'windscribe', label: 'Windscribe' },
  { value: 'custom', label: 'Custom Provider' },
]

const PORT_FORWARDING_SUPPORTED = new Set([
  'private internet access', 'protonvpn', 'perfect privacy', 'privatevpn',
])

const VPN_SERVER_REFRESH_SOURCE_OPTIONS = [
  { value: 'proton_paid', label: 'Proton Paid Catalog (auth required)' },
  { value: 'gluetun_official', label: 'Official Gluetun Catalog (public, may be outdated)' },
]

const GLUETUN_JSON_MODE_OPTIONS = [
  { value: 'update', label: 'Update providers in existing servers.json' },
  { value: 'replace', label: 'Replace servers.json completely' },
  { value: 'none', label: 'Do not modify servers.json' },
]

const PROTON_CREDENTIALS_SOURCE_OPTIONS = [
  { value: 'env', label: 'Environment Variables' },
  { value: 'settings', label: 'Store in Settings' },
]

const DEFAULTS = {
  enabled: false,
  api_port: 8001,
  health_check_interval_s: 5,
  port_cache_ttl_s: 60,
  restart_engines_on_reconnect: true,
  unhealthy_restart_timeout_s: 60,
  preferred_engines_per_vpn: 10,
  max_engines_per_vpn: 15,
  protocol: 'wireguard',
  provider: 'protonvpn',
  regionsText: '',
  vpn_servers_auto_refresh: false,
  vpn_servers_refresh_period_s: 86400,
  vpn_servers_refresh_source: 'gluetun_official',
  vpn_servers_gluetun_json_mode: 'update',
  vpn_servers_storage_path: '',
  vpn_servers_official_url: 'https://raw.githubusercontent.com/qdm12/gluetun/master/internal/storage/servers.json',
  vpn_servers_proton_credentials_source: 'env',
  vpn_servers_proton_username_env: 'PROTON_USERNAME',
  vpn_servers_proton_password_env: 'PROTON_PASSWORD',
  vpn_servers_proton_totp_code_env: 'PROTON_TOTP_CODE',
  vpn_servers_proton_totp_secret_env: 'PROTON_TOTP_SECRET',
  vpn_servers_proton_username: '',
  vpn_servers_proton_password: '',
  vpn_servers_proton_totp_code: '',
  vpn_servers_proton_totp_secret: '',
  wireguard_mtu: 0,
}

const toNumber = (value, fallback = 0) => {
  const next = Number(value)
  return Number.isFinite(next) ? next : fallback
}

const normalizeProvider = (provider) => {
  const value = String(provider || '').trim().toLowerCase()
  if (value === 'pia') return 'private internet access'
  return value
}

const isForwardingSupported = (provider) => PORT_FORWARDING_SUPPORTED.has(normalizeProvider(provider))

const mask = (value, left = 4, right = 3) => {
  const text = String(value || '').trim()
  if (!text) return 'N/A'
  if (text.length <= left + right) return text
  return `${text.slice(0, left)}...${text.slice(-right)}`
}

const parseRegionsInput = (value) => String(value || '').split(',').map((item) => item.trim()).filter(Boolean)

const inputStyle = {
  background: 'var(--bg-0)', border: '1px solid var(--line)', color: 'var(--fg-0)',
  padding: '4px 8px', fontFamily: 'var(--font-mono)', fontSize: 11, outline: 'none',
}
const selectStyle = { ...inputStyle, cursor: 'pointer', minWidth: 140 }
const textareaStyle = {
  ...inputStyle, width: '100%', minHeight: 180, resize: 'vertical',
  lineHeight: 1.5, display: 'block', boxSizing: 'border-box',
}

function Toggle({ checked, onChange, disabled }) {
  return (
    <button
      type="button"
      onClick={() => !disabled && onChange(!checked)}
      style={{
        width: 36, height: 18,
        background: checked ? 'var(--acc-green-bg)' : 'var(--bg-2)',
        border: `1px solid ${checked ? 'var(--acc-green-dim)' : 'var(--line)'}`,
        borderRadius: 2, cursor: disabled ? 'not-allowed' : 'pointer',
        display: 'flex', alignItems: 'center', padding: '0 2px',
        transition: 'background 0.15s', opacity: disabled ? 0.5 : 1, flexShrink: 0,
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

function Pane({ title, description, children, actions }) {
  return (
    <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-soft)' }}>
      <div style={{ padding: '10px 14px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'flex-start', gap: 8 }}>
        <div style={{ flex: 1 }}>
          <span className="label">{title}</span>
          {description && <div style={{ fontSize: 10, color: 'var(--fg-2)', marginTop: 2 }}>{description}</div>}
        </div>
        {actions}
      </div>
      <div style={{ padding: '12px 14px' }}>{children}</div>
    </div>
  )
}

export function VPNSettings({ apiKey, orchUrl, authRequired }) {
  const sectionId = 'vpn'
  const { registerSection, unregisterSection, setSectionDirty, setSectionSaving } = useSettingsForm()

  const [loading, setLoading] = useState(true)
  const [initialState, setInitialState] = useState(DEFAULTS)
  const [draft, setDraft] = useState(DEFAULTS)
  const [initialCredentials, setInitialCredentials] = useState([])
  const [credentials, setCredentials] = useState([])
  const [leaseSummary, setLeaseSummary] = useState(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')
  const [refreshStatus, setRefreshStatus] = useState(null)
  const [refreshingServers, setRefreshingServers] = useState(false)
  const [dialogOpen, setDialogOpen] = useState(false)
  const [expertOpen, setExpertOpen] = useState(false)
  const [dialogLoading, setDialogLoading] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [credentialProvider, setCredentialProvider] = useState('protonvpn')
  const [credentialMode, setCredentialMode] = useState('wireguard')
  const [credentialRegions, setCredentialRegions] = useState('')
  const [credentialPortForwarding, setCredentialPortForwarding] = useState(true)
  const [wgText, setWgText] = useState('')
  const [openvpnUser, setOpenvpnUser] = useState('')
  const [openvpnPassword, setOpenvpnPassword] = useState('')

  const dirty = useMemo(() => {
    return JSON.stringify(draft) !== JSON.stringify(initialState)
      || JSON.stringify(credentials) !== JSON.stringify(initialCredentials)
  }, [draft, initialState, credentials, initialCredentials])

  const sheetProviderNormalized = useMemo(() => normalizeProvider(credentialProvider), [credentialProvider])
  const sheetProviderSupportsForwarding = useMemo(() => isForwardingSupported(sheetProviderNormalized), [sheetProviderNormalized])
  const hasCredentials = credentials.length > 0
  const vpnToggleDisabled = !hasCredentials && !draft.enabled

  const leasesByCredentialId = useMemo(() => {
    const byCredentialId = new Map()
    const leases = Array.isArray(leaseSummary?.leases) ? leaseSummary.leases : []
    for (const lease of leases) {
      const credentialId = String(lease?.credential_id || '').trim()
      if (!credentialId) continue
      byCredentialId.set(credentialId, lease)
    }
    return byCredentialId
  }, [leaseSummary])

  const fetchLeases = async () => {
    try {
      const response = await fetch(`${orchUrl}/api/v1/vpn/leases`)
      if (!response.ok) return
      const payload = await response.json()
      setLeaseSummary(payload)
    } catch { /* non-blocking */ }
  }

  const fetchRefreshStatus = async () => {
    try {
      const response = await fetch(`${orchUrl}/api/v1/vpn/servers/refresh/status`)
      if (!response.ok) return
      const payload = await response.json()
      setRefreshStatus(payload)
    } catch { /* non-blocking */ }
  }

  const fetchConfig = async () => {
    setLoading(true)
    setError('')
    try {
      let payload = null
      const consolidated = await fetch(`${orchUrl}/api/v1/settings`)
      if (consolidated.ok) {
        const settingsBundle = await consolidated.json().catch(() => ({}))
        payload = settingsBundle?.vpn_settings || null
      }
      if (!payload) {
        const response = await fetch(`${orchUrl}/api/v1/settings/vpn`)
        if (!response.ok) throw new Error(`HTTP ${response.status}`)
        payload = await response.json()
      }
      const normalized = {
        enabled: Boolean(payload?.enabled),
        api_port: toNumber(payload?.api_port, DEFAULTS.api_port),
        health_check_interval_s: toNumber(payload?.health_check_interval_s, DEFAULTS.health_check_interval_s),
        port_cache_ttl_s: toNumber(payload?.port_cache_ttl_s, DEFAULTS.port_cache_ttl_s),
        restart_engines_on_reconnect: Boolean(payload?.restart_engines_on_reconnect),
        unhealthy_restart_timeout_s: toNumber(payload?.unhealthy_restart_timeout_s, DEFAULTS.unhealthy_restart_timeout_s),
        preferred_engines_per_vpn: toNumber(payload?.preferred_engines_per_vpn, DEFAULTS.preferred_engines_per_vpn),
        max_engines_per_vpn: toNumber(payload?.max_engines_per_vpn, DEFAULTS.max_engines_per_vpn),
        protocol: String(payload?.protocol || DEFAULTS.protocol).toLowerCase(),
        provider: normalizeProvider(payload?.provider || DEFAULTS.provider),
        regionsText: Array.isArray(payload?.regions) ? payload.regions.join(', ') : '',
        vpn_servers_auto_refresh: Boolean(payload?.vpn_servers_auto_refresh),
        vpn_servers_refresh_period_s: toNumber(payload?.vpn_servers_refresh_period_s, DEFAULTS.vpn_servers_refresh_period_s),
        vpn_servers_refresh_source: String(payload?.vpn_servers_refresh_source || DEFAULTS.vpn_servers_refresh_source).toLowerCase(),
        vpn_servers_gluetun_json_mode: String(payload?.vpn_servers_gluetun_json_mode || DEFAULTS.vpn_servers_gluetun_json_mode).toLowerCase(),
        vpn_servers_storage_path: String(payload?.vpn_servers_storage_path || DEFAULTS.vpn_servers_storage_path),
        vpn_servers_official_url: String(payload?.vpn_servers_official_url || DEFAULTS.vpn_servers_official_url),
        vpn_servers_proton_credentials_source: String(payload?.vpn_servers_proton_credentials_source || DEFAULTS.vpn_servers_proton_credentials_source).toLowerCase(),
        vpn_servers_proton_username_env: String(payload?.vpn_servers_proton_username_env || DEFAULTS.vpn_servers_proton_username_env),
        vpn_servers_proton_password_env: String(payload?.vpn_servers_proton_password_env || DEFAULTS.vpn_servers_proton_password_env),
        vpn_servers_proton_totp_code_env: String(payload?.vpn_servers_proton_totp_code_env || DEFAULTS.vpn_servers_proton_totp_code_env),
        vpn_servers_proton_totp_secret_env: String(payload?.vpn_servers_proton_totp_secret_env || DEFAULTS.vpn_servers_proton_totp_secret_env),
        vpn_servers_proton_username: String(payload?.vpn_servers_proton_username || ''),
        vpn_servers_proton_password: String(payload?.vpn_servers_proton_password || ''),
        vpn_servers_proton_totp_code: String(payload?.vpn_servers_proton_totp_code || ''),
        vpn_servers_proton_totp_secret: String(payload?.vpn_servers_proton_totp_secret || ''),
        wireguard_mtu: toNumber(payload?.wireguard_mtu, DEFAULTS.wireguard_mtu),
      }
      setInitialState(normalized)
      setDraft(normalized)
      const loadedCredentials = Array.isArray(payload?.credentials) ? payload.credentials : []
      setInitialCredentials(loadedCredentials)
      setCredentials(loadedCredentials)
      setSectionDirty(sectionId, false)
      await fetchLeases()
      await fetchRefreshStatus()
    } catch (fetchError) {
      setError(`Failed to load VPN settings: ${fetchError.message || String(fetchError)}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { fetchConfig() }, [orchUrl])

  useEffect(() => {
    const save = async () => {
      if (authRequired && !String(apiKey || '').trim()) {
        throw new Error('API key required by server for VPN settings updates')
      }
      setSectionSaving(sectionId, true)
      setError('')
      setMessage('')
      try {
        const headers = { 'Content-Type': 'application/json' }
        if (String(apiKey || '').trim()) headers.Authorization = `Bearer ${String(apiKey).trim()}`
        const payload = {
          enabled: Boolean(draft.enabled),
          api_port: toNumber(draft.api_port, DEFAULTS.api_port),
          health_check_interval_s: toNumber(draft.health_check_interval_s, DEFAULTS.health_check_interval_s),
          port_cache_ttl_s: toNumber(draft.port_cache_ttl_s, DEFAULTS.port_cache_ttl_s),
          restart_engines_on_reconnect: Boolean(draft.restart_engines_on_reconnect),
          unhealthy_restart_timeout_s: toNumber(draft.unhealthy_restart_timeout_s, DEFAULTS.unhealthy_restart_timeout_s),
          preferred_engines_per_vpn: Math.max(1, toNumber(draft.preferred_engines_per_vpn, DEFAULTS.preferred_engines_per_vpn)),
          max_engines_per_vpn: Math.max(1, toNumber(draft.max_engines_per_vpn, DEFAULTS.max_engines_per_vpn)),
          protocol: draft.protocol,
          provider: draft.provider,
          regions: parseRegionsInput(draft.regionsText),
          credentials,
          trigger_migration: Boolean(draft.enabled) !== Boolean(initialState.enabled),
          vpn_servers_auto_refresh: Boolean(draft.vpn_servers_auto_refresh),
          vpn_servers_refresh_period_s: Math.max(60, toNumber(draft.vpn_servers_refresh_period_s, DEFAULTS.vpn_servers_refresh_period_s)),
          vpn_servers_refresh_source: String(draft.vpn_servers_refresh_source || DEFAULTS.vpn_servers_refresh_source),
          vpn_servers_gluetun_json_mode: String(draft.vpn_servers_gluetun_json_mode || DEFAULTS.vpn_servers_gluetun_json_mode),
          vpn_servers_storage_path: String(draft.vpn_servers_storage_path || '').trim() || null,
          vpn_servers_official_url: String(draft.vpn_servers_official_url || DEFAULTS.vpn_servers_official_url).trim(),
          vpn_servers_proton_credentials_source: String(draft.vpn_servers_proton_credentials_source || DEFAULTS.vpn_servers_proton_credentials_source),
          vpn_servers_proton_username_env: String(draft.vpn_servers_proton_username_env || DEFAULTS.vpn_servers_proton_username_env).trim(),
          vpn_servers_proton_password_env: String(draft.vpn_servers_proton_password_env || DEFAULTS.vpn_servers_proton_password_env).trim(),
          vpn_servers_proton_totp_code_env: String(draft.vpn_servers_proton_totp_code_env || DEFAULTS.vpn_servers_proton_totp_code_env).trim(),
          vpn_servers_proton_totp_secret_env: String(draft.vpn_servers_proton_totp_secret_env || DEFAULTS.vpn_servers_proton_totp_secret_env).trim(),
          vpn_servers_proton_username: String(draft.vpn_servers_proton_username || '').trim() || null,
          vpn_servers_proton_password: String(draft.vpn_servers_proton_password || '').trim() || null,
          vpn_servers_proton_totp_code: String(draft.vpn_servers_proton_totp_code || '').trim() || null,
          vpn_servers_proton_totp_secret: String(draft.vpn_servers_proton_totp_secret || '').trim() || null,
          wireguard_mtu: Math.max(0, toNumber(draft.wireguard_mtu, DEFAULTS.wireguard_mtu)),
        }
        const response = await fetch(`${orchUrl}/api/v1/settings/vpn`, {
          method: 'POST', headers, body: JSON.stringify(payload),
        })
        if (!response.ok) {
          const failure = await response.json().catch(() => ({}))
          throw new Error(failure?.detail || `HTTP ${response.status}`)
        }
        const result = await response.json().catch(() => null)
        setInitialState({ ...draft })
        setInitialCredentials([...credentials])
        setSectionDirty(sectionId, false)
        const marked = Math.max(0, Number(result?.migration_marked_engines || 0))
        if (Boolean(draft.enabled) !== Boolean(initialState.enabled)) {
          const targetText = draft.enabled ? 'VPN-backed engines' : 'normal internet engines'
          setMessage(`VPN settings saved; marked ${marked} engine(s) for migration to ${targetText}`)
        } else {
          setMessage('VPN settings saved')
        }
        await fetchLeases()
        await fetchRefreshStatus()
      } finally {
        setSectionSaving(sectionId, false)
      }
    }

    const discard = () => {
      setDraft(initialState)
      setCredentials(initialCredentials)
      setSectionDirty(sectionId, false)
      setError('')
      setMessage('')
    }

    registerSection(sectionId, { title: 'VPN', requiresAuth: true, save, discard })
    return () => unregisterSection(sectionId)
  }, [apiKey, authRequired, credentials, draft, initialState, orchUrl, registerSection, setSectionDirty, setSectionSaving, unregisterSection])

  useEffect(() => { setSectionDirty(sectionId, dirty) }, [dirty, setSectionDirty])

  const update = (field, value) => {
    setDraft((prev) => ({ ...prev, [field]: value }))
    setError('')
    setMessage('')
  }

  const queueVpnEnabled = (value) => {
    const enabled = Boolean(value)
    if (enabled && !hasCredentials) {
      setError('Add at least one VPN credential before enabling VPN routing')
      return
    }
    setDraft((prev) => ({ ...prev, enabled }))
    setError('')
    setMessage(`VPN routing ${enabled ? 'enabled' : 'disabled'} queued; save changes to apply`)
  }

  const handleDragOver = (e) => { e.preventDefault(); setIsDragging(true) }
  const handleDragLeave = (e) => { e.preventDefault(); setIsDragging(false) }

  const handleDrop = async (e) => {
    e.preventDefault()
    setIsDragging(false)
    const file = e.dataTransfer.files[0]
    if (file && (file.name.endsWith('.conf') || file.type === 'text/plain' || file.name.endsWith('.txt'))) {
      const text = await file.text()
      setWgText(text)
    } else {
      setError('Please drop a valid .conf or text file.')
    }
  }

  const addCredential = async () => {
    setDialogLoading(true)
    setError('')
    setMessage('')
    try {
      let payload = {
        provider: sheetProviderNormalized,
        protocol: credentialMode,
        regions: parseRegionsInput(credentialRegions),
        port_forwarding: Boolean(credentialPortForwarding && sheetProviderSupportsForwarding),
      }
      if (credentialMode === 'wireguard') {
        const confText = String(wgText || '').trim()
        if (!confText) throw new Error('Wireguard .conf content is required')
        const parseResponse = await fetch(`${orchUrl}/api/v1/vpn/parse-wireguard`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_content: confText }),
        })
        const parsed = await parseResponse.json().catch(() => null)
        if (!parseResponse.ok) throw new Error(parsed?.detail?.message || parsed?.detail || `HTTP ${parseResponse.status}`)
        payload = {
          ...payload, ...parsed,
          addresses: parsed?.address || (Array.isArray(parsed?.addresses) ? parsed.addresses.join(',') : ''),
          source: 'sheet-paste.conf',
        }
      } else {
        const username = String(openvpnUser || '').trim()
        const password = String(openvpnPassword || '').trim()
        if (!username || !password) throw new Error('OpenVPN username and password are required')
        payload = { ...payload, openvpn_user: username, openvpn_password: password, username, password }
      }
      const credentialId = String(payload?.id || `cred-draft-${Date.now()}-${Math.random().toString(16).slice(2)}`)
      setCredentials((prev) => [...prev, { ...payload, id: credentialId }])
      setMessage('Credential added to draft; save changes to apply')
      setDialogOpen(false)
      setWgText('')
      setOpenvpnUser('')
      setOpenvpnPassword('')
      setCredentialRegions('')
    } catch (addError) {
      setError(`Failed to add credential: ${addError.message || String(addError)}`)
    } finally {
      setDialogLoading(false)
    }
  }

  const removeCredential = (credentialId) => {
    setCredentials((prev) => prev.filter((c) => String(c?.id || '') !== String(credentialId || '')))
    setError('')
    setMessage('Credential removal queued; save changes to apply')
  }

  const refreshServersNow = async () => {
    if (authRequired && !String(apiKey || '').trim()) {
      setError('API key required by server for manual VPN server refresh')
      return
    }
    setRefreshingServers(true)
    setError('')
    setMessage('')
    try {
      const headers = { 'Content-Type': 'application/json' }
      if (String(apiKey || '').trim()) headers.Authorization = `Bearer ${String(apiKey).trim()}`
      const response = await fetch(`${orchUrl}/api/v1/vpn/servers/refresh`, {
        method: 'POST', headers,
        body: JSON.stringify({ source: draft.vpn_servers_refresh_source, gluetun_json_mode: draft.vpn_servers_gluetun_json_mode, reason: 'manual-ui' }),
      })
      if (!response.ok) {
        const failure = await response.json().catch(() => ({}))
        throw new Error(failure?.detail || `HTTP ${response.status}`)
      }
      const result = await response.json().catch(() => ({}))
      setMessage(`VPN server list refreshed from ${String(result?.source || draft.vpn_servers_refresh_source)}`)
      await fetchRefreshStatus()
    } catch (refreshError) {
      setError(`Failed to refresh VPN server list: ${refreshError.message || String(refreshError)}`)
    } finally {
      setRefreshingServers(false)
    }
  }

  if (loading) {
    return (
      <div style={{ background: 'var(--bg-1)', border: '1px solid var(--line-soft)', padding: '32px 14px', textAlign: 'center', fontSize: 11, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
        loading vpn settings...
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      {message && <div style={{ fontSize: 11, color: 'var(--acc-green)', fontFamily: 'var(--font-mono)', padding: '4px 0' }}>{message}</div>}
      {error && <div style={{ fontSize: 11, color: 'var(--acc-red)', fontFamily: 'var(--font-mono)', padding: '4px 0' }}>{error}</div>}

      {/* VPN Controller */}
      <Pane
        title={`${draft.enabled ? '⛨' : '○'} VPN CONTROLLER SETTINGS`}
        description="Static VPN controller behavior participates in global save and unsaved-change protection."
      >
        <SettingRow
          label="Enable VPN Routing"
          description={Boolean(initialState.enabled) ? 'Disable to stop new engine scheduling on managed VPN nodes.' : 'Route new engine traffic through managed VPN nodes.'}
          warning={!hasCredentials ? 'Add at least one credential in the pool to enable routing.' : undefined}
        >
          <Toggle checked={Boolean(draft.enabled)} disabled={vpnToggleDisabled} onChange={queueVpnEnabled}/>
        </SettingRow>
        <SettingRow label="Preferred Engines per VPN Node" description="Scheduler hint for desired VPN node count.">
          <input type="number" min={1} max={100} value={draft.preferred_engines_per_vpn} style={inputStyle} onChange={(e) => update('preferred_engines_per_vpn', toNumber(e.target.value, DEFAULTS.preferred_engines_per_vpn))}/>
        </SettingRow>
        <SettingRow label="Max Engines per VPN Node" description="Hard limit to prevent resource saturation per node.">
          <input type="number" min={1} max={100} value={draft.max_engines_per_vpn} style={inputStyle} onChange={(e) => update('max_engines_per_vpn', toNumber(e.target.value, DEFAULTS.max_engines_per_vpn))}/>
        </SettingRow>

        {/* Expert settings toggle */}
        <div style={{ borderTop: '1px solid var(--line-soft)', marginTop: 12, paddingTop: 12 }}>
          <button
            type="button"
            onClick={() => setExpertOpen(v => !v)}
            style={{ background: 'none', border: '1px solid var(--line)', color: 'var(--fg-2)', padding: '4px 12px', fontFamily: 'var(--font-mono)', fontSize: 10, cursor: 'pointer' }}
          >
            {expertOpen ? '▲ HIDE EXPERT SETTINGS' : '▼ SHOW EXPERT SETTINGS'}
          </button>
        </div>

        {expertOpen && (
          <div style={{ marginTop: 12 }}>
            <SettingRow label="Gluetun API Port" description="Must match Gluetun HTTP control server port.">
              <input type="number" min={1} max={65535} value={draft.api_port} style={inputStyle} onChange={(e) => update('api_port', toNumber(e.target.value, DEFAULTS.api_port))}/>
            </SettingRow>
            <SettingRow label="WireGuard MTU" description="Force a specific MTU for the WireGuard tunnel. Set to 0 to let Gluetun auto-detect.">
              <input type="number" min={0} max={9000} value={draft.wireguard_mtu} style={inputStyle} placeholder="0 = auto-detect" onChange={(e) => update('wireguard_mtu', toNumber(e.target.value, DEFAULTS.wireguard_mtu))}/>
            </SettingRow>
            <SettingRow label="Health Check Interval (s)" description="VPN health polling cadence.">
              <input type="number" min={1} max={120} value={draft.health_check_interval_s} style={inputStyle} onChange={(e) => update('health_check_interval_s', toNumber(e.target.value, DEFAULTS.health_check_interval_s))}/>
            </SettingRow>
            <SettingRow label="Port Cache TTL (s)" description="Forwarded-port cache TTL.">
              <input type="number" min={1} max={300} value={draft.port_cache_ttl_s} style={inputStyle} onChange={(e) => update('port_cache_ttl_s', toNumber(e.target.value, DEFAULTS.port_cache_ttl_s))}/>
            </SettingRow>
            <SettingRow label="Unhealthy Restart Timeout (s)" description="Restart VPN node after this unhealthy duration.">
              <input type="number" min={10} max={600} value={draft.unhealthy_restart_timeout_s} style={inputStyle} onChange={(e) => update('unhealthy_restart_timeout_s', toNumber(e.target.value, DEFAULTS.unhealthy_restart_timeout_s))}/>
            </SettingRow>
            <SettingRow label="Restart Engines on VPN Reconnect" description="Restart engines when VPN node reconnects to refresh routes.">
              <Toggle checked={Boolean(draft.restart_engines_on_reconnect)} onChange={(value) => update('restart_engines_on_reconnect', Boolean(value))}/>
            </SettingRow>

            {/* VPN Server Refresh */}
            <div style={{ borderTop: '1px solid var(--line-soft)', marginTop: 12, paddingTop: 12 }}>
              <div className="label" style={{ marginBottom: 10 }}>VPN SERVER LIST REFRESH</div>
              <SettingRow label="Refresh Source" description="Choose where server catalog updates come from.">
                <select value={draft.vpn_servers_refresh_source} onChange={(e) => update('vpn_servers_refresh_source', e.target.value)} style={selectStyle}>
                  {VPN_SERVER_REFRESH_SOURCE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </SettingRow>
              <SettingRow label="Automatic Refresh" description="Periodically refresh VPN provider server lists.">
                <Toggle checked={Boolean(draft.vpn_servers_auto_refresh)} onChange={(value) => update('vpn_servers_auto_refresh', Boolean(value))}/>
              </SettingRow>
              <SettingRow label="Refresh Period (s)" description="How often automatic refresh should run.">
                <input type="number" min={60} max={604800} value={draft.vpn_servers_refresh_period_s} style={inputStyle} onChange={(e) => update('vpn_servers_refresh_period_s', toNumber(e.target.value, DEFAULTS.vpn_servers_refresh_period_s))}/>
              </SettingRow>
              <SettingRow label="servers.json Write Mode" description="How refreshed data is applied to servers.json.">
                <select value={draft.vpn_servers_gluetun_json_mode} onChange={(e) => update('vpn_servers_gluetun_json_mode', e.target.value)} style={selectStyle}>
                  {GLUETUN_JSON_MODE_OPTIONS.map((o) => (
                    <option key={o.value} value={o.value}>{o.label}</option>
                  ))}
                </select>
              </SettingRow>
              <SettingRow label="Storage Path (Optional)" description="Directory where servers json files are written.">
                <input value={draft.vpn_servers_storage_path} placeholder="/data/gluetun" style={{ ...inputStyle, width: 200 }} onChange={(e) => update('vpn_servers_storage_path', e.target.value)}/>
              </SettingRow>
              {draft.vpn_servers_refresh_source === 'gluetun_official' && (
                <SettingRow label="Official Catalog URL" description="Official Gluetun catalog URL (override if needed).">
                  <input value={draft.vpn_servers_official_url} style={{ ...inputStyle, width: 280 }} onChange={(e) => update('vpn_servers_official_url', e.target.value)}/>
                </SettingRow>
              )}
              {draft.vpn_servers_refresh_source === 'proton_paid' && (
                <>
                  <SettingRow label="Proton Credentials Source" description="Use environment variables or persisted settings values.">
                    <select value={draft.vpn_servers_proton_credentials_source} onChange={(e) => update('vpn_servers_proton_credentials_source', e.target.value)} style={selectStyle}>
                      {PROTON_CREDENTIALS_SOURCE_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </SettingRow>
                  {draft.vpn_servers_proton_credentials_source === 'env' ? (
                    <>
                      <SettingRow label="Username Env Var" description="Environment variable containing Proton username.">
                        <input value={draft.vpn_servers_proton_username_env} style={{ ...inputStyle, width: 200 }} onChange={(e) => update('vpn_servers_proton_username_env', e.target.value)}/>
                      </SettingRow>
                      <SettingRow label="Password Env Var" description="Environment variable containing Proton password.">
                        <input value={draft.vpn_servers_proton_password_env} style={{ ...inputStyle, width: 200 }} onChange={(e) => update('vpn_servers_proton_password_env', e.target.value)}/>
                      </SettingRow>
                      <SettingRow label="TOTP Code Env Var" description="Optional one-time TOTP code variable.">
                        <input value={draft.vpn_servers_proton_totp_code_env} style={{ ...inputStyle, width: 200 }} onChange={(e) => update('vpn_servers_proton_totp_code_env', e.target.value)}/>
                      </SettingRow>
                      <SettingRow label="TOTP Secret Env Var" description="Optional base32 TOTP secret variable.">
                        <input value={draft.vpn_servers_proton_totp_secret_env} style={{ ...inputStyle, width: 200 }} onChange={(e) => update('vpn_servers_proton_totp_secret_env', e.target.value)}/>
                      </SettingRow>
                    </>
                  ) : (
                    <>
                      <SettingRow label="Proton Username" description="Stored in settings for automated refresh.">
                        <input value={draft.vpn_servers_proton_username} style={{ ...inputStyle, width: 200 }} onChange={(e) => update('vpn_servers_proton_username', e.target.value)}/>
                      </SettingRow>
                      <SettingRow label="Proton Password" description="Stored in settings for automated refresh.">
                        <input type="password" value={draft.vpn_servers_proton_password} style={{ ...inputStyle, width: 200 }} onChange={(e) => update('vpn_servers_proton_password', e.target.value)}/>
                      </SettingRow>
                      <SettingRow label="TOTP Code (Optional)" description="One-time code; if empty, TOTP secret can be used.">
                        <input value={draft.vpn_servers_proton_totp_code} style={inputStyle} onChange={(e) => update('vpn_servers_proton_totp_code', e.target.value)}/>
                      </SettingRow>
                      <SettingRow label="TOTP Secret (Optional)" description="Base32 secret for automatic token generation.">
                        <input type="password" value={draft.vpn_servers_proton_totp_secret} style={{ ...inputStyle, width: 200 }} onChange={(e) => update('vpn_servers_proton_totp_secret', e.target.value)}/>
                      </SettingRow>
                    </>
                  )}
                </>
              )}
              <div style={{ display: 'flex', alignItems: 'center', gap: 12, paddingTop: 10 }}>
                <button
                  type="button"
                  onClick={refreshServersNow}
                  disabled={refreshingServers}
                  style={{ background: 'none', border: '1px solid var(--line)', color: 'var(--fg-1)', padding: '5px 14px', fontFamily: 'var(--font-mono)', fontSize: 11, cursor: refreshingServers ? 'not-allowed' : 'pointer', opacity: refreshingServers ? 0.5 : 1 }}
                >
                  {refreshingServers ? '⟳ REFRESHING...' : '↺ REFRESH VPN SERVER LIST NOW'}
                </button>
                {refreshStatus?.last_finished_at && (
                  <span style={{ fontSize: 10, color: 'var(--fg-3)', fontFamily: 'var(--font-mono)' }}>
                    Last run: {new Date(refreshStatus.last_finished_at).toLocaleString()}
                  </span>
                )}
              </div>
            </div>
          </div>
        )}
      </Pane>

      {/* Credential Pool */}
      <Pane
        title="CREDENTIAL POOL"
        description="Credential changes are part of the VPN draft and are applied only when you save."
        actions={
          <button
            type="button"
            onClick={() => setDialogOpen(true)}
            className="tag tag-green"
            style={{ cursor: 'pointer', padding: '4px 10px', fontSize: 10 }}
          >
            + ADD CREDENTIAL
          </button>
        }
      >
        {/* Stats */}
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 12 }}>
          <span className="tag" style={{ fontSize: 9 }}>Total: {credentials.length}</span>
          <span className="tag" style={{ fontSize: 9 }}>Leased: {leaseSummary?.leased ?? 0}</span>
          <span className="tag" style={{ fontSize: 9 }}>Available: {leaseSummary?.available ?? 0}</span>
        </div>

        {/* Credentials table */}
        <table className="data" style={{ width: '100%' }}>
          <thead>
            <tr>
              <th>PROVIDER / PROTOCOL</th>
              <th>IDENTIFIER</th>
              <th>STATUS</th>
              <th>PORT FWD</th>
              <th style={{ textAlign: 'right' }}>ACTIONS</th>
            </tr>
          </thead>
          <tbody>
            {credentials.length === 0 ? (
              <tr>
                <td colSpan={5} style={{ textAlign: 'center', padding: '20px', color: 'var(--fg-3)', fontSize: 10 }}>No credentials configured.</td>
              </tr>
            ) : credentials.map((credential) => {
              const protocol = String(credential?.protocol || 'wireguard').toLowerCase()
              const provider = normalizeProvider(credential?.provider || 'Unknown')
              const hasForwarding = Boolean(credential?.port_forwarding) && isForwardingSupported(provider)
              const credentialId = String(credential?.id || '').trim()
              const lease = credentialId ? leasesByCredentialId.get(credentialId) : null
              const inUse = Boolean(lease)
              const containerLabel = String(lease?.container_id || '').trim()
              const identifier = protocol === 'wireguard'
                ? `Key ${mask(credential?.private_key || credential?.wireguard_private_key)}`
                : `User ${mask(credential?.openvpn_user || credential?.username, 3, 2)}`

              return (
                <tr key={String(credential?.id || Math.random())}>
                  <td>
                    <div style={{ fontWeight: 600, color: 'var(--fg-0)', fontSize: 11 }}>{provider}</div>
                    <div style={{ fontSize: 9, color: 'var(--fg-3)', textTransform: 'uppercase', letterSpacing: 1 }}>
                      {protocol}{credential?.regions?.length > 0 ? ` · ${credential.regions.join(', ')}` : ''}
                    </div>
                  </td>
                  <td style={{ fontSize: 10 }}>{identifier}</td>
                  <td>
                    {inUse ? (
                      <div>
                        <span className="tag tag-green" style={{ fontSize: 9 }}>IN USE</span>
                        {containerLabel && <div style={{ fontSize: 9, color: 'var(--fg-3)', marginTop: 2 }}>{containerLabel}</div>}
                      </div>
                    ) : (
                      <span className="tag" style={{ fontSize: 9 }}>AVAILABLE</span>
                    )}
                  </td>
                  <td>
                    {hasForwarding
                      ? <span className="tag tag-cyan" style={{ fontSize: 9 }}>⚡ ENABLED</span>
                      : <span className="tag" style={{ fontSize: 9 }}>DISABLED</span>
                    }
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      type="button"
                      onClick={() => removeCredential(credential?.id)}
                      className="tag tag-red"
                      style={{ cursor: 'pointer', padding: '2px 8px', fontSize: 9 }}
                    >
                      ✕ REMOVE
                    </button>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </Pane>

      {/* Add Credential overlay */}
      {dialogOpen && (
        <div style={{
          position: 'fixed', inset: 0, zIndex: 200,
          background: 'rgba(0,0,0,0.5)',
          display: 'flex', alignItems: 'flex-start', justifyContent: 'flex-end',
        }}>
          <div style={{
            width: 480, height: '100vh', overflowY: 'auto',
            background: 'var(--bg-1)', borderLeft: '1px solid var(--line)',
            display: 'flex', flexDirection: 'column',
          }}>
            <div style={{ padding: '14px 16px', borderBottom: '1px solid var(--line)', display: 'flex', alignItems: 'center', gap: 8 }}>
              <div style={{ flex: 1 }}>
                <div className="label">ADD VPN CREDENTIAL</div>
                <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 2 }}>Credential changes stay local until you save settings.</div>
              </div>
              <button
                type="button"
                onClick={() => setDialogOpen(false)}
                style={{ background: 'none', border: '1px solid var(--line)', color: 'var(--fg-2)', padding: '3px 8px', fontFamily: 'var(--font-mono)', fontSize: 11, cursor: 'pointer' }}
              >
                ✕
              </button>
            </div>

            <div style={{ padding: '14px 16px', flex: 1 }}>
              <SettingRow label="Provider" description="VPN service provider.">
                <select value={credentialProvider} onChange={(e) => setCredentialProvider(e.target.value)} style={selectStyle}>
                  {PROVIDER_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
                </select>
              </SettingRow>
              <SettingRow label="Protocol" description="Protocol type for this credential.">
                <select value={credentialMode} onChange={(e) => setCredentialMode(e.target.value)} style={selectStyle}>
                  <option value="wireguard">Wireguard (.conf text)</option>
                  <option value="openvpn">OpenVPN (username/password)</option>
                </select>
              </SettingRow>
              <SettingRow label="Preferred Regions" description="Comma-separated preferred regions (e.g. us-east, nl).">
                <input value={credentialRegions} onChange={(e) => setCredentialRegions(e.target.value)} placeholder="us-east, nl, region:paris" style={{ ...inputStyle, width: '100%' }}/>
              </SettingRow>
              <SettingRow label="Port Forwarding" description="Enable only if credential/provider supports forwarded ports.">
                <Toggle checked={credentialPortForwarding && sheetProviderSupportsForwarding} disabled={!sheetProviderSupportsForwarding} onChange={setCredentialPortForwarding}/>
              </SettingRow>

              {credentialMode === 'wireguard' ? (
                <div style={{ marginTop: 16 }}>
                  <div
                    onDragOver={handleDragOver}
                    onDragLeave={handleDragLeave}
                    onDrop={handleDrop}
                    style={{
                      border: `2px dashed ${isDragging ? 'var(--acc-green)' : 'var(--line)'}`,
                      background: isDragging ? 'var(--acc-green-bg)' : 'var(--bg-0)',
                      padding: '24px 16px',
                      textAlign: 'center',
                      cursor: 'pointer',
                      marginBottom: 12,
                    }}
                  >
                    <div style={{ fontSize: 11, color: 'var(--fg-2)', fontFamily: 'var(--font-mono)' }}>↑ Drag &amp; drop your .conf file here</div>
                    <div style={{ fontSize: 10, color: 'var(--fg-3)', marginTop: 4 }}>or paste the content below</div>
                  </div>
                  <div style={{ fontSize: 10, color: 'var(--fg-2)', marginBottom: 4, fontFamily: 'var(--font-mono)' }}>Wireguard .conf Content</div>
                  <textarea
                    value={wgText}
                    onChange={(e) => setWgText(e.target.value)}
                    rows={10}
                    placeholder="[Interface]&#10;PrivateKey = ...&#10;Address = ...&#10;&#10;[Peer]&#10;Endpoint = ..."
                    style={textareaStyle}
                  />
                </div>
              ) : (
                <div style={{ marginTop: 16 }}>
                  <SettingRow label="OpenVPN Username" description="Credential username.">
                    <input value={openvpnUser} onChange={(e) => setOpenvpnUser(e.target.value)} style={{ ...inputStyle, width: '100%' }}/>
                  </SettingRow>
                  <SettingRow label="OpenVPN Password" description="Credential password.">
                    <input type="password" value={openvpnPassword} onChange={(e) => setOpenvpnPassword(e.target.value)} style={{ ...inputStyle, width: '100%' }}/>
                  </SettingRow>
                </div>
              )}
            </div>

            <div style={{ padding: '12px 16px', borderTop: '1px solid var(--line)', display: 'flex', justifyContent: 'flex-end', gap: 8 }}>
              <button
                type="button"
                onClick={() => setDialogOpen(false)}
                style={{ background: 'none', border: '1px solid var(--line)', color: 'var(--fg-1)', padding: '6px 16px', fontFamily: 'var(--font-mono)', fontSize: 11, cursor: 'pointer' }}
              >
                Cancel
              </button>
              <button
                type="button"
                onClick={addCredential}
                disabled={dialogLoading}
                className="tag tag-green"
                style={{ cursor: dialogLoading ? 'not-allowed' : 'pointer', padding: '6px 16px', opacity: dialogLoading ? 0.6 : 1 }}
              >
                {dialogLoading ? '⟳ SAVING...' : '+ ADD CREDENTIAL'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
