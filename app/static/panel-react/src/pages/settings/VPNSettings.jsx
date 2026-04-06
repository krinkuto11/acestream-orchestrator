import React, { useEffect, useMemo, useState } from 'react'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Textarea } from '@/components/ui/textarea'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetFooter,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from '@/components/ui/collapsible'
import { AlertCircle, Plus, ShieldCheck, ShieldOff, Trash2, Zap, ZapOff, UploadCloud, ChevronDown, ChevronUp } from 'lucide-react'
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
  'private internet access',
  'protonvpn',
  'perfect privacy',
  'privatevpn',
])

const DEFAULTS = {
  enabled: false,
  api_port: 8001,
  health_check_interval_s: 5,
  port_cache_ttl_s: 60,
  restart_engines_on_reconnect: true,
  unhealthy_restart_timeout_s: 60,
  preferred_engines_per_vpn: 10,
  protocol: 'wireguard',
  provider: 'protonvpn',
  regionsText: '',
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

const parseRegionsInput = (value) => String(value || '')
  .split(',')
  .map((item) => item.trim())
  .filter(Boolean)

export function VPNSettings({ apiKey, orchUrl, authRequired }) {
  const sectionId = 'vpn'
  const { registerSection, unregisterSection, setSectionDirty, setSectionSaving } = useSettingsForm()

  const [loading, setLoading] = useState(true)
  const [initialState, setInitialState] = useState(DEFAULTS)
  const [draft, setDraft] = useState(DEFAULTS)
  const [credentials, setCredentials] = useState([])
  const [leaseSummary, setLeaseSummary] = useState(null)
  const [error, setError] = useState('')
  const [message, setMessage] = useState('')

  const [dialogOpen, setDialogOpen] = useState(false)
  const [expertOpen, setExpertOpen] = useState(false)
  const [dialogLoading, setDialogLoading] = useState(false)
  const [vpnToggleLoading, setVpnToggleLoading] = useState(false)
  const [triggerMigrationOnToggle, setTriggerMigrationOnToggle] = useState(true)
  const [isDragging, setIsDragging] = useState(false)

  // Per-Credential Settings
  const [credentialProvider, setCredentialProvider] = useState('protonvpn')
  const [credentialMode, setCredentialMode] = useState('wireguard')
  const [credentialRegions, setCredentialRegions] = useState('')
  const [credentialPortForwarding, setCredentialPortForwarding] = useState(true)
  const [wgText, setWgText] = useState('')
  const [openvpnUser, setOpenvpnUser] = useState('')
  const [openvpnPassword, setOpenvpnPassword] = useState('')

  const dirty = useMemo(() => JSON.stringify(draft) !== JSON.stringify(initialState), [draft, initialState])

  const sheetProviderNormalized = useMemo(() => normalizeProvider(credentialProvider), [credentialProvider])
  const sheetProviderSupportsForwarding = useMemo(() => isForwardingSupported(sheetProviderNormalized), [sheetProviderNormalized])
  const hasCredentials = credentials.length > 0
  const vpnToggleDisabled = vpnToggleLoading || (!hasCredentials && !draft.enabled)
  
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
    } catch {
      // non-blocking
    }
  }

  const fetchConfig = async () => {
    setLoading(true)
    setError('')
    try {
      const response = await fetch(`${orchUrl}/api/v1/settings/vpn`)
      if (!response.ok) throw new Error(`HTTP ${response.status}`)
      const payload = await response.json()

      const normalized = {
        enabled: Boolean(payload?.enabled),
        api_port: toNumber(payload?.api_port, DEFAULTS.api_port),
        health_check_interval_s: toNumber(payload?.health_check_interval_s, DEFAULTS.health_check_interval_s),
        port_cache_ttl_s: toNumber(payload?.port_cache_ttl_s, DEFAULTS.port_cache_ttl_s),
        restart_engines_on_reconnect: Boolean(payload?.restart_engines_on_reconnect),
        unhealthy_restart_timeout_s: toNumber(payload?.unhealthy_restart_timeout_s, DEFAULTS.unhealthy_restart_timeout_s),
        preferred_engines_per_vpn: toNumber(payload?.preferred_engines_per_vpn, DEFAULTS.preferred_engines_per_vpn),
        protocol: String(payload?.protocol || DEFAULTS.protocol).toLowerCase(),
        provider: normalizeProvider(payload?.provider || DEFAULTS.provider),
        regionsText: Array.isArray(payload?.regions) ? payload.regions.join(', ') : '',
      }

      setInitialState(normalized)
      setDraft(normalized)
      setCredentials(Array.isArray(payload?.credentials) ? payload.credentials : [])
      setTriggerMigrationOnToggle(true)
      setSectionDirty(sectionId, false)
      await fetchLeases()
    } catch (fetchError) {
      setError(`Failed to load VPN settings: ${fetchError.message || String(fetchError)}`)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    fetchConfig()
  }, [orchUrl])

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
        if (String(apiKey || '').trim()) {
          headers.Authorization = `Bearer ${String(apiKey).trim()}`
        }

        const payload = {
          enabled: Boolean(draft.enabled),
          api_port: toNumber(draft.api_port, DEFAULTS.api_port),
          health_check_interval_s: toNumber(draft.health_check_interval_s, DEFAULTS.health_check_interval_s),
          port_cache_ttl_s: toNumber(draft.port_cache_ttl_s, DEFAULTS.port_cache_ttl_s),
          restart_engines_on_reconnect: Boolean(draft.restart_engines_on_reconnect),
          unhealthy_restart_timeout_s: toNumber(draft.unhealthy_restart_timeout_s, DEFAULTS.unhealthy_restart_timeout_s),
          preferred_engines_per_vpn: Math.max(1, toNumber(draft.preferred_engines_per_vpn, DEFAULTS.preferred_engines_per_vpn)),
          protocol: draft.protocol, // preserving backend schema
          provider: draft.provider, // preserving backend schema
          regions: parseRegionsInput(draft.regionsText), // preserving backend schema
          credentials,
        }

        const response = await fetch(`${orchUrl}/api/v1/settings/vpn`, {
          method: 'POST',
          headers,
          body: JSON.stringify(payload),
        })

        if (!response.ok) {
          const failure = await response.json().catch(() => ({}))
          throw new Error(failure?.detail || `HTTP ${response.status}`)
        }

        setInitialState({ ...draft })
        setSectionDirty(sectionId, false)
        setMessage('VPN settings saved')
        await fetchLeases()
      } finally {
        setSectionSaving(sectionId, false)
      }
    }

    const discard = () => {
      setDraft(initialState)
      setSectionDirty(sectionId, false)
      setError('')
      setMessage('')
    }

    registerSection(sectionId, {
      title: 'VPN',
      requiresAuth: true,
      save,
      discard,
    })

    return () => unregisterSection(sectionId)
  }, [
    apiKey,
    authRequired,
    credentials,
    draft,
    initialState,
    orchUrl,
    registerSection,
    setSectionDirty,
    setSectionSaving,
    unregisterSection,
  ])

  useEffect(() => {
    setSectionDirty(sectionId, dirty)
  }, [dirty, setSectionDirty])

  const update = (field, value) => {
    setDraft((prev) => ({ ...prev, [field]: value }))
    setError('')
    setMessage('')
  }

  const applyVpnEnabled = async (value) => {
    const enabled = Boolean(value)
    const vpnStateChanged = enabled !== Boolean(initialState.enabled)
    const shouldTriggerMigration = Boolean(vpnStateChanged && triggerMigrationOnToggle)

    if (enabled && !hasCredentials) {
      setError('Add at least one VPN credential before enabling VPN routing')
      return
    }

    if (authRequired && !String(apiKey || '').trim()) {
      setError('API key required by server to toggle VPN routing')
      return
    }

    setVpnToggleLoading(true)
    setError('')
    setMessage('')

    try {
      const headers = { 'Content-Type': 'application/json' }
      if (String(apiKey || '').trim()) {
        headers.Authorization = `Bearer ${String(apiKey).trim()}`
      }

      const payload = {
        enabled,
        api_port: toNumber(draft.api_port, DEFAULTS.api_port),
        health_check_interval_s: toNumber(draft.health_check_interval_s, DEFAULTS.health_check_interval_s),
        port_cache_ttl_s: toNumber(draft.port_cache_ttl_s, DEFAULTS.port_cache_ttl_s),
        restart_engines_on_reconnect: Boolean(draft.restart_engines_on_reconnect),
        unhealthy_restart_timeout_s: toNumber(draft.unhealthy_restart_timeout_s, DEFAULTS.unhealthy_restart_timeout_s),
        preferred_engines_per_vpn: Math.max(1, toNumber(draft.preferred_engines_per_vpn, DEFAULTS.preferred_engines_per_vpn)),
        protocol: draft.protocol,
        provider: draft.provider,
        regions: parseRegionsInput(draft.regionsText),
        credentials,
        trigger_migration: shouldTriggerMigration,
      }

      const response = await fetch(`${orchUrl}/api/v1/settings/vpn`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      })

      const result = await response.json().catch(() => null)

      if (!response.ok) {
        throw new Error(result?.detail || `HTTP ${response.status}`)
      }

      setDraft((prev) => ({ ...prev, enabled }))
      setInitialState((prev) => ({ ...prev, enabled }))

      const marked = Math.max(0, Number(result?.migration_marked_engines || 0))
      if (shouldTriggerMigration) {
        const targetText = enabled ? 'VPN-backed engines' : 'normal internet engines'
        setMessage(`VPN routing ${enabled ? 'enabled' : 'disabled'} and applied immediately; marked ${marked} engine(s) as draining for migration to ${targetText}`)
      } else {
        setMessage(`VPN routing ${enabled ? 'enabled' : 'disabled'} and applied immediately`)
      }
    } catch (toggleError) {
      setError(`Failed to toggle VPN routing: ${toggleError.message || String(toggleError)}`)
    } finally {
      setVpnToggleLoading(false)
    }
  }

  const handleDragOver = (e) => {
    e.preventDefault()
    setIsDragging(true)
  }

  const handleDragLeave = (e) => {
    e.preventDefault()
    setIsDragging(false)
  }

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
    if (authRequired && !String(apiKey || '').trim()) {
      setError('API key required by server to add VPN credentials')
      return
    }

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
        if (!confText) {
          throw new Error('Wireguard .conf content is required')
        }

        const parseResponse = await fetch(`${orchUrl}/api/v1/vpn/parse-wireguard`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file_content: confText }),
        })
        const parsed = await parseResponse.json().catch(() => null)
        if (!parseResponse.ok) {
          throw new Error(parsed?.detail?.message || parsed?.detail || `HTTP ${parseResponse.status}`)
        }

        payload = {
          ...payload,
          private_key: parsed?.private_key,
          addresses: parsed?.address || (Array.isArray(parsed?.addresses) ? parsed.addresses.join(',') : ''),
          endpoint: parsed?.endpoint,
          source: 'sheet-paste.conf',
        }
      } else {
        const username = String(openvpnUser || '').trim()
        const password = String(openvpnPassword || '').trim()
        if (!username || !password) {
          throw new Error('OpenVPN username and password are required')
        }

        payload = {
          ...payload,
          openvpn_user: username,
          openvpn_password: password,
          username,
          password,
        }
      }

      const headers = { 'Content-Type': 'application/json' }
      if (String(apiKey || '').trim()) {
        headers.Authorization = `Bearer ${String(apiKey).trim()}`
      }

      const response = await fetch(`${orchUrl}/api/v1/settings/vpn/credentials`, {
        method: 'POST',
        headers,
        body: JSON.stringify(payload),
      })

      const result = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(result?.detail || `HTTP ${response.status}`)
      }

      setMessage('Credential added and saved immediately')
      setDialogOpen(false)
      setWgText('')
      setOpenvpnUser('')
      setOpenvpnPassword('')
      await fetchConfig()
    } catch (addError) {
      setError(`Failed to add credential: ${addError.message || String(addError)}`)
    } finally {
      setDialogLoading(false)
    }
  }

  const removeCredential = async (credentialId) => {
    if (authRequired && !String(apiKey || '').trim()) {
      setError('API key required by server to remove VPN credentials')
      return
    }

    try {
      const headers = {}
      if (String(apiKey || '').trim()) {
        headers.Authorization = `Bearer ${String(apiKey).trim()}`
      }

      const response = await fetch(`${orchUrl}/api/v1/settings/vpn/credentials/${encodeURIComponent(String(credentialId))}`, {
        method: 'DELETE',
        headers,
      })
      const payload = await response.json().catch(() => null)
      if (!response.ok) {
        throw new Error(payload?.detail || `HTTP ${response.status}`)
      }

      setMessage('Credential removed and saved immediately')
      await fetchConfig()
    } catch (removeError) {
      setError(`Failed to remove credential: ${removeError.message || String(removeError)}`)
    }
  }

  if (loading) {
    return (
      <Card>
        <CardContent className="py-10 text-sm text-muted-foreground">Loading VPN settings...</CardContent>
      </Card>
    )
  }

  return (
    <div className="space-y-5">
      {message && <p className="text-sm text-emerald-600 dark:text-emerald-400">{message}</p>}
      {error && <p className="text-sm text-red-600 dark:text-red-400">{error}</p>}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {draft.enabled ? <ShieldCheck className="h-5 w-5 text-emerald-500" /> : <ShieldOff className="h-5 w-5 text-slate-400" />}
            VPN Controller Settings
          </CardTitle>
          <CardDescription>Static VPN controller behavior participates in global save and unsaved-change protection.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <SettingRow
            label="Enable VPN Routing"
            description={
              Boolean(initialState.enabled)
                ? 'Disable to stop new engine scheduling on managed VPN nodes.'
                : 'Route new engine traffic through managed VPN nodes.'
            }
            warning={!hasCredentials ? 'Add at least one credential in the pool to enable routing.' : undefined}
          >
            <Switch
              checked={Boolean(draft.enabled)}
              disabled={vpnToggleDisabled}
              onCheckedChange={applyVpnEnabled}
            />
          </SettingRow>

          {(Boolean(initialState.enabled) || hasCredentials) && (
            <div className="rounded-lg border border-slate-200/70 bg-slate-50/60 p-3 dark:border-slate-800 dark:bg-slate-900/40">
              <SettingRow
                label="Gracefully migrate engines on VPN toggle"
                description={
                  Boolean(initialState.enabled)
                    ? 'When disabling VPN routing, marks current VPN engines as draining so new streams move to normal internet engines without dropping active streams.'
                    : 'When enabling VPN routing, marks current non-VPN engines as draining so new streams move to VPN-backed engines without dropping active streams.'
                }
              >
                <Switch checked={triggerMigrationOnToggle} onCheckedChange={setTriggerMigrationOnToggle} />
              </SettingRow>
            </div>
          )}

          <SettingRow label="Preferred Engines per VPN Node" description="Scheduler hint for desired VPN node count.">
            <Input type="number" min={1} max={100} value={draft.preferred_engines_per_vpn} onChange={(e) => update('preferred_engines_per_vpn', toNumber(e.target.value, DEFAULTS.preferred_engines_per_vpn))} className="max-w-xs" />
          </SettingRow>

          <Collapsible open={expertOpen} onOpenChange={setExpertOpen} className="w-full">
            <div className="flex items-center mt-6 mb-2">
              <div className="flex-grow border-t border-muted"></div>
              <CollapsibleTrigger asChild>
                <Button variant="ghost" size="sm" className="mx-2 text-xs uppercase tracking-wider text-muted-foreground hover:bg-transparent">
                  {expertOpen ? 'Hide Expert Settings' : 'Show Expert Settings'}
                  {expertOpen ? <ChevronUp className="ml-2 h-3 w-3" /> : <ChevronDown className="ml-2 h-3 w-3" />}
                </Button>
              </CollapsibleTrigger>
              <div className="flex-grow border-t border-muted"></div>
            </div>
            
            <CollapsibleContent className="space-y-3 pt-2">
              <SettingRow label="Gluetun API Port" description="Must match Gluetun HTTP control server port.">
                <Input type="number" min={1} max={65535} value={draft.api_port} onChange={(e) => update('api_port', toNumber(e.target.value, DEFAULTS.api_port))} className="max-w-xs" />
              </SettingRow>

              <SettingRow label="Health Check Interval (s)" description="VPN health polling cadence.">
                <Input type="number" min={1} max={120} value={draft.health_check_interval_s} onChange={(e) => update('health_check_interval_s', toNumber(e.target.value, DEFAULTS.health_check_interval_s))} className="max-w-xs" />
              </SettingRow>

              <SettingRow label="Port Cache TTL (s)" description="Forwarded-port cache TTL.">
                <Input type="number" min={1} max={300} value={draft.port_cache_ttl_s} onChange={(e) => update('port_cache_ttl_s', toNumber(e.target.value, DEFAULTS.port_cache_ttl_s))} className="max-w-xs" />
              </SettingRow>

              <SettingRow label="Unhealthy Restart Timeout (s)" description="Restart VPN node after this unhealthy duration.">
                <Input type="number" min={10} max={600} value={draft.unhealthy_restart_timeout_s} onChange={(e) => update('unhealthy_restart_timeout_s', toNumber(e.target.value, DEFAULTS.unhealthy_restart_timeout_s))} className="max-w-xs" />
              </SettingRow>

              <SettingRow label="Restart Engines on VPN Reconnect" description="Restart engines when VPN node reconnects to refresh routes.">
                <Switch checked={Boolean(draft.restart_engines_on_reconnect)} onCheckedChange={(value) => update('restart_engines_on_reconnect', Boolean(value))} />
              </SettingRow>
            </CollapsibleContent>
          </Collapsible>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex items-start justify-between gap-3">
            <div>
              <CardTitle>Credential Pool</CardTitle>
              <CardDescription>
                Operational credentials apply immediately and bypass global settings save state.
              </CardDescription>
            </div>
            <Button type="button" onClick={() => setDialogOpen(true)}>
              <Plus className="mr-2 h-4 w-4" />
              Add Credential
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="secondary">Total: {credentials.length}</Badge>
            <Badge variant="secondary">Leased: {leaseSummary?.leased ?? 0}</Badge>
            <Badge variant="secondary">Available: {leaseSummary?.available ?? 0}</Badge>
          </div>

          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Provider/Protocol</TableHead>
                  <TableHead>Identifier</TableHead>
                  <TableHead>Usage Status</TableHead>
                  <TableHead>Port Forwarding</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {credentials.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5} className="py-6 text-center text-sm text-muted-foreground">No credentials configured.</TableCell>
                  </TableRow>
                ) : (
                  credentials.map((credential) => {
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
                      <TableRow key={String(credential?.id || Math.random())}>
                        <TableCell>
                          <div className="font-medium">{provider}</div>
                          <div className="text-xs uppercase text-muted-foreground">
                            {protocol}
                            {credential?.regions && credential.regions.length > 0 && ` • ${credential.regions.join(', ')}`}
                          </div>
                        </TableCell>
                        <TableCell className="text-sm">{identifier}</TableCell>
                        <TableCell>
                          {inUse ? (
                            <div className="space-y-1">
                              <Badge className="bg-emerald-600 text-white hover:bg-emerald-600">In Use</Badge>
                              <p className="text-xs text-muted-foreground">
                                Node: {containerLabel || 'unknown'}
                              </p>
                            </div>
                          ) : (
                            <Badge variant="outline">Available</Badge>
                          )}
                        </TableCell>
                        <TableCell>
                          {hasForwarding ? (
                            <Badge variant="success"><Zap className="mr-1 h-3.5 w-3.5" />Enabled</Badge>
                          ) : (
                            <Badge variant="secondary"><ZapOff className="mr-1 h-3.5 w-3.5" />Disabled</Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button type="button" variant="ghost" size="icon" onClick={() => removeCredential(credential?.id)}>
                            <Trash2 className="h-4 w-4 text-red-500" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    )
                  })
                )}
              </TableBody>
            </Table>
          </div>
        </CardContent>
      </Card>

      <Sheet open={dialogOpen} onOpenChange={setDialogOpen}>
        <SheetContent side="right" className="dark text-foreground w-[400px] sm:w-[540px] overflow-y-auto">
          <SheetHeader className="mb-6">
            <SheetTitle>Add VPN Credential</SheetTitle>
            <SheetDescription>
              Credential operations are committed immediately to backend storage.
            </SheetDescription>
          </SheetHeader>

          <div className="space-y-6">
            <SettingRow label="Provider" description="VPN service provider.">
              <Select value={credentialProvider} onValueChange={setCredentialProvider}>
                <SelectTrigger className="w-full text-foreground"><SelectValue placeholder="Select provider" /></SelectTrigger>
                <SelectContent className="dark">
                  {PROVIDER_OPTIONS.map((option) => (
                    <SelectItem key={option.value} value={option.value}>{option.label}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </SettingRow>

            <SettingRow label="Protocol" description="Protocol type for this credential.">
              <Select value={credentialMode} onValueChange={setCredentialMode}>
                <SelectTrigger className="w-full text-foreground"><SelectValue placeholder="Select protocol" /></SelectTrigger>
                <SelectContent className="dark">
                  <SelectItem value="wireguard">Wireguard (.conf text)</SelectItem>
                  <SelectItem value="openvpn">OpenVPN (username/password)</SelectItem>
                </SelectContent>
              </Select>
            </SettingRow>

            <SettingRow label="Preferred Regions" description="Comma-separated preferred regions (e.g. us-east, nl).">
              <Input value={credentialRegions} onChange={(e) => setCredentialRegions(e.target.value)} placeholder="us-east, nl, region:paris" className="w-full" />
            </SettingRow>

            <SettingRow label="Port Forwarding" description="Enable only if credential/provider supports forwarded ports.">
              <Switch checked={credentialPortForwarding && sheetProviderSupportsForwarding} disabled={!sheetProviderSupportsForwarding} onCheckedChange={setCredentialPortForwarding} />
            </SettingRow>

            {credentialMode === 'wireguard' ? (
              <div className="space-y-4">
                <div
                  onDragOver={handleDragOver}
                  onDragLeave={handleDragLeave}
                  onDrop={handleDrop}
                  className={`border-2 border-dashed rounded-lg p-8 text-center transition-colors cursor-pointer ${
                    isDragging ? 'border-primary bg-primary/10' : 'border-muted-foreground/25 hover:border-primary/50'
                  }`}
                >
                  <UploadCloud className="mx-auto h-8 w-8 text-muted-foreground mb-3" />
                  <p className="text-sm font-medium">Drag & drop your .conf file here</p>
                  <p className="text-xs text-muted-foreground mt-1">or paste the content below</p>
                </div>
                
                <SettingRow label="Wireguard .conf Content" description="Paste full [Interface]/[Peer] configuration.">
                  <Textarea value={wgText} onChange={(e) => setWgText(e.target.value)} rows={10} placeholder="[Interface]&#10;PrivateKey = ...&#10;Address = ...&#10;&#10;[Peer]&#10;Endpoint = ..." />
                </SettingRow>
              </div>
            ) : (
              <div className="space-y-4">
                <SettingRow label="OpenVPN Username" description="Credential username.">
                  <Input value={openvpnUser} onChange={(e) => setOpenvpnUser(e.target.value)} className="w-full" />
                </SettingRow>
                <SettingRow label="OpenVPN Password" description="Credential password.">
                  <Input type="password" value={openvpnPassword} onChange={(e) => setOpenvpnPassword(e.target.value)} className="w-full" />
                </SettingRow>
              </div>
            )}
          </div>

          <SheetFooter className="mt-8">
            <Button type="button" variant="outline" className="text-foreground" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button type="button" onClick={addCredential} disabled={dialogLoading}>
              {dialogLoading ? <><AlertCircle className="mr-2 h-4 w-4" />Saving...</> : <><Plus className="mr-2 h-4 w-4" />Add Credential</>}
            </Button>
          </SheetFooter>
        </SheetContent>
      </Sheet>
    </div>
  )
}