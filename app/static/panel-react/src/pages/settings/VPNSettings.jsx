import React, { useState, useEffect, useMemo } from 'react'
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Button } from '@/components/ui/button'
import { Switch } from '@/components/ui/switch'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Textarea } from '@/components/ui/textarea'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  FileUp,
  Info,
  ShieldCheck,
  ShieldOff,
  Trash2,
  Upload,
} from 'lucide-react'

const DEFAULTS = {
  enabled: false,
  api_port: 8001,
  health_check_interval_s: 5,
  port_cache_ttl_s: 60,
  restart_engines_on_reconnect: true,
  unhealthy_restart_timeout_s: 60,
  dynamic_vpn_management: true,
  preferred_engines_per_vpn: 10,
  protocol: 'wireguard',
  provider: 'protonvpn',
  regions: [],
  credentials: [],
}

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

function normalizeProvider(provider) {
  const value = String(provider || '').trim().toLowerCase()
  if (value === 'pia') return 'private internet access'
  return value
}

function isForwardingSupported(provider) {
  return PORT_FORWARDING_SUPPORTED.has(normalizeProvider(provider))
}

function providerLabel(provider) {
  const normalized = normalizeProvider(provider)
  const option = PROVIDER_OPTIONS.find((item) => item.value === normalized)
  return option ? option.label : provider || 'Unknown'
}

function generateCredentialId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `cred-${Date.now()}-${Math.random().toString(16).slice(2)}`
}

function maskText(value, visibleStart = 4, visibleEnd = 3) {
  const text = String(value || '').trim()
  if (!text) return 'N/A'
  if (text.length <= visibleStart + visibleEnd) return text
  return `${text.slice(0, visibleStart)}...${text.slice(-visibleEnd)}`
}

function credentialIdentifier(credential) {
  if (credential.protocol === 'wireguard') {
    return `Key ${maskText(credential.private_key || credential.wireguard_private_key)}`
  }
  return `User ${maskText(credential.username || credential.openvpn_user, 3, 2)}`
}

function parseRegionsInput(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean)
}

function normalizeCredentialsWithIds(items) {
  if (!Array.isArray(items)) return []
  return items
    .filter((item) => item && typeof item === 'object')
    .map((item) => {
      const existingId = String(item.id || '').trim()
      return {
        ...item,
        id: existingId || generateCredentialId(),
      }
    })
}

export function VPNSettings({ apiKey, orchUrl }) {
  const [loading, setLoading] = useState(false)
  const [loadingLeases, setLoadingLeases] = useState(false)
  const [message, setMessage] = useState(null)
  const [error, setError] = useState(null)
  const [showExpert, setShowExpert] = useState(false)
  const [wireguardInput, setWireguardInput] = useState('')
  const [isDropActive, setIsDropActive] = useState(false)
  const [isParsingWireguard, setIsParsingWireguard] = useState(false)
  const [leaseSummary, setLeaseSummary] = useState(null)

  // Basic settings
  const [enabled, setEnabled] = useState(DEFAULTS.enabled)
  const [apiPort, setApiPort] = useState(DEFAULTS.api_port)

  // Expert settings
  const [healthCheckIntervalS, setHealthCheckIntervalS] = useState(DEFAULTS.health_check_interval_s)
  const [portCacheTtlS, setPortCacheTtlS] = useState(DEFAULTS.port_cache_ttl_s)
  const [restartEnginesOnReconnect, setRestartEnginesOnReconnect] = useState(DEFAULTS.restart_engines_on_reconnect)
  const [unhealthyRestartTimeoutS, setUnhealthyRestartTimeoutS] = useState(DEFAULTS.unhealthy_restart_timeout_s)

  // Dynamic VPN wizard state
  const [preferredEnginesPerVpn, setPreferredEnginesPerVpn] = useState(DEFAULTS.preferred_engines_per_vpn)
  const [protocol, setProtocol] = useState(DEFAULTS.protocol)
  const [selectedProvider, setSelectedProvider] = useState(DEFAULTS.provider)
  const [regionsText, setRegionsText] = useState('')
  const [credentials, setCredentials] = useState(DEFAULTS.credentials)

  // Conditional OpenVPN + PIA fields
  const [piaUsername, setPiaUsername] = useState('')
  const [piaPassword, setPiaPassword] = useState('')

  const forwardingSupported = isForwardingSupported(selectedProvider)

  const leasesByCredentialId = useMemo(() => {
    const leaseMap = new Map()
    const leases = Array.isArray(leaseSummary?.leases) ? leaseSummary.leases : []
    for (const lease of leases) {
      const credentialId = String(lease?.credential_id || '').trim()
      if (!credentialId) continue
      leaseMap.set(credentialId, lease)
    }
    return leaseMap
  }, [leaseSummary])

  useEffect(() => {
    fetchConfig()
  }, [orchUrl])

  const fetchLeases = async () => {
    try {
      setLoadingLeases(true)
      const response = await fetch(`${orchUrl}/api/v1/vpn/leases`)
      if (!response.ok) return
      const data = await response.json()
      setLeaseSummary(data)
    } catch (err) {
      console.error('Failed to fetch VPN lease summary:', err)
    } finally {
      setLoadingLeases(false)
    }
  }

  const fetchConfig = async () => {
    try {
      const response = await fetch(`${orchUrl}/api/v1/settings/vpn`)
      if (response.ok) {
        const data = await response.json()
        setEnabled(data.enabled ?? DEFAULTS.enabled)
        setApiPort(data.api_port ?? DEFAULTS.api_port)
        setHealthCheckIntervalS(data.health_check_interval_s ?? DEFAULTS.health_check_interval_s)
        setPortCacheTtlS(data.port_cache_ttl_s ?? DEFAULTS.port_cache_ttl_s)
        setRestartEnginesOnReconnect(data.restart_engines_on_reconnect ?? DEFAULTS.restart_engines_on_reconnect)
        setUnhealthyRestartTimeoutS(data.unhealthy_restart_timeout_s ?? DEFAULTS.unhealthy_restart_timeout_s)

        setPreferredEnginesPerVpn(Math.max(1, Number(data.preferred_engines_per_vpn ?? DEFAULTS.preferred_engines_per_vpn) || DEFAULTS.preferred_engines_per_vpn))

        const loadedProtocol = String(data.protocol || DEFAULTS.protocol).toLowerCase()
        setProtocol(loadedProtocol === 'openvpn' ? 'openvpn' : 'wireguard')

        const loadedProvider = normalizeProvider(data.provider || DEFAULTS.provider)
        setSelectedProvider(loadedProvider || DEFAULTS.provider)

        const loadedRegions = Array.isArray(data.regions) ? data.regions : []
        setRegionsText(loadedRegions.join(', '))

        const loadedCredentials = normalizeCredentialsWithIds(data.credentials)
        setCredentials(loadedCredentials)
      }
      await fetchLeases()
    } catch (err) {
      console.error('Failed to fetch VPN config:', err)
    }
  }

  const addOpenVpnPiaCredential = () => {
    const username = piaUsername.trim()
    const password = piaPassword.trim()
    if (!username || !password) {
      setError('Username and password are required for OpenVPN + PIA credential')
      return
    }

    const provider = normalizeProvider(selectedProvider)
    const credential = {
      id: generateCredentialId(),
      provider,
      protocol: 'openvpn',
      username,
      password,
      openvpn_user: username,
      openvpn_password: password,
    }

    setCredentials((prev) => [...prev, credential])
    setPiaUsername('')
    setPiaPassword('')
    setMessage('OpenVPN credential added to pool')
    setError(null)
  }

  const addParsedWireguardCredential = (parsed, source = 'uploaded.conf') => {
    const provider = normalizeProvider(selectedProvider || 'custom')
    const address = parsed.address || (Array.isArray(parsed.addresses) ? parsed.addresses.join(',') : '')
    const privateKey = parsed.private_key

    const credential = {
      id: generateCredentialId(),
      provider,
      protocol: 'wireguard',
      private_key: privateKey,
      wireguard_private_key: privateKey,
      addresses: address,
      wireguard_addresses: address,
      endpoint: parsed.endpoint,
      endpoints: parsed.endpoint,
      wireguard_endpoints: parsed.endpoint,
      source,
    }

    setCredentials((prev) => [...prev, credential])
    setMessage('Wireguard credential parsed and added to pool')
    setError(null)
  }

  const parseWireguardText = async (fileContent, sourceLabel) => {
    setIsParsingWireguard(true)
    setError(null)
    try {
      const response = await fetch(`${orchUrl}/api/v1/vpn/parse-wireguard`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ file_content: fileContent }),
      })

      const data = await response.json()
      if (!response.ok) {
        const detail = data?.detail
        const detailMessage = typeof detail === 'string' ? detail : detail?.message
        throw new Error(detailMessage || 'Could not parse Wireguard configuration')
      }

      setProtocol('wireguard')
      addParsedWireguardCredential(data, sourceLabel)
    } catch (err) {
      setError(`Wireguard parse failed: ${err.message}`)
    } finally {
      setIsParsingWireguard(false)
    }
  }

  const handleWireguardFile = async (file) => {
    if (!file) return
    const text = await file.text()
    await parseWireguardText(text, file.name || 'uploaded.conf')
  }

  const handleDrop = async (event) => {
    event.preventDefault()
    setIsDropActive(false)

    const file = event.dataTransfer?.files?.[0]
    if (!file) return
    await handleWireguardFile(file)
  }

  const deleteCredential = (credentialId) => {
    setCredentials((prev) => prev.filter((credential) => credential.id !== credentialId))
  }

  const saveConfig = async () => {
    if (!apiKey) {
      setError('API Key is required to update settings')
      return
    }
    setLoading(true)
    setMessage(null)
    setError(null)

    const normalizedCredentials = normalizeCredentialsWithIds(credentials)
    setCredentials(normalizedCredentials)

    const payload = {
      enabled,
      api_port: apiPort,
      health_check_interval_s: healthCheckIntervalS,
      port_cache_ttl_s: portCacheTtlS,
      restart_engines_on_reconnect: restartEnginesOnReconnect,
      unhealthy_restart_timeout_s: unhealthyRestartTimeoutS,
      dynamic_vpn_management: true,
      preferred_engines_per_vpn: Math.max(1, Number(preferredEnginesPerVpn) || DEFAULTS.preferred_engines_per_vpn),
      protocol,
      provider: selectedProvider ? normalizeProvider(selectedProvider) : '',
      regions: parseRegionsInput(regionsText),
      credentials: normalizedCredentials,
    }

    try {
      const response = await fetch(`${orchUrl}/api/v1/settings/vpn`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`,
        },
        body: JSON.stringify(payload),
      })
      if (response.ok) {
        const data = await response.json()
        setMessage(data.message || 'VPN settings saved successfully')
        await fetchConfig()
        await fetchLeases()
      } else {
        const errorData = await response.json()
        setError(errorData.detail || 'Failed to update VPN configuration')
      }
    } catch (err) {
      setError('Failed to save VPN configuration: ' + err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-6">
      {/* Master VPN toggle */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            {enabled
              ? <ShieldCheck className="h-5 w-5 text-green-500" />
              : <ShieldOff className="h-5 w-5 text-muted-foreground" />
            }
            VPN Integration (Gluetun)
          </CardTitle>
          <CardDescription>
            Connect to a Gluetun VPN container for routing engine traffic through a VPN.
            When disabled, engines connect directly without VPN.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex items-center gap-3">
            <Switch
              id="vpn-enabled"
              checked={enabled}
              onCheckedChange={setEnabled}
            />
            <div>
              <Label htmlFor="vpn-enabled" className="text-base font-medium">
                {enabled ? 'VPN Enabled' : 'VPN Disabled'}
              </Label>
              <p className="text-xs text-muted-foreground">
                {enabled
                  ? 'Engines will route traffic through the configured Gluetun container.'
                  : 'Engines connect directly — no VPN routing.'}
              </p>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* VPN connection config — only when enabled */}
      {enabled && (
        <>
          <Card>
            <CardHeader>
              <CardTitle>Smart VPN Wizard</CardTitle>
              <CardDescription>
                Configure protocol/provider combinations and build a credential pool for orchestrator-managed dynamic VPN nodes.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-5">
              <div className="space-y-2 max-w-xs">
                <Label htmlFor="preferred-engines-per-vpn">Preferred Engines per VPN Node</Label>
                <Input
                  id="preferred-engines-per-vpn"
                  type="number"
                  min={1}
                  value={preferredEnginesPerVpn}
                  onChange={(e) => setPreferredEnginesPerVpn(Math.max(1, parseInt(e.target.value, 10) || 1))}
                />
                <p className="text-xs text-muted-foreground">
                  Scheduler target used by the controller to estimate how many VPN nodes should be active.
                </p>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <Label htmlFor="vpn-protocol">Protocol</Label>
                  <Select value={protocol} onValueChange={setProtocol}>
                    <SelectTrigger id="vpn-protocol">
                      <SelectValue placeholder="Select VPN protocol" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="wireguard">Wireguard</SelectItem>
                      <SelectItem value="openvpn">OpenVPN</SelectItem>
                    </SelectContent>
                  </Select>
                </div>

                <div className="space-y-2">
                  <Label htmlFor="vpn-provider">Provider</Label>
                  <Select value={selectedProvider} onValueChange={setSelectedProvider}>
                    <SelectTrigger id="vpn-provider">
                      <SelectValue placeholder="Select VPN provider" />
                    </SelectTrigger>
                    <SelectContent>
                      {PROVIDER_OPTIONS.map((option) => (
                        <SelectItem key={option.value} value={option.value}>
                          {option.label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  {forwardingSupported ? (
                    <Badge className="bg-green-600 hover:bg-green-600 text-white">
                      <CheckCircle2 className="h-3.5 w-3.5 mr-1" />
                      Port Forwarding Supported
                    </Badge>
                  ) : (
                    <Badge variant="secondary" className="bg-yellow-500/20 text-yellow-700 dark:text-yellow-300">
                      <AlertTriangle className="h-3.5 w-3.5 mr-1" />
                      Provider does not support native port forwarding
                    </Badge>
                  )}
                </div>
              </div>

              <div className="space-y-2">
                <Label htmlFor="vpn-regions">Preferred Regions (comma-separated)</Label>
                <Input
                  id="vpn-regions"
                  value={regionsText}
                  onChange={(e) => setRegionsText(e.target.value)}
                  placeholder="us-east, nl, region:paris"
                />
              </div>

              {protocol === 'openvpn' && normalizeProvider(selectedProvider) === 'private internet access' && (
                <Card className="border-border/60">
                  <CardHeader>
                    <CardTitle className="text-base">OpenVPN + PIA Credentials</CardTitle>
                    <CardDescription>
                      Add username/password credentials to the pool for dynamic node provisioning.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                      <div className="space-y-1">
                        <Label htmlFor="pia-username">Username</Label>
                        <Input
                          id="pia-username"
                          value={piaUsername}
                          onChange={(e) => setPiaUsername(e.target.value)}
                          placeholder="PIA username"
                        />
                      </div>
                      <div className="space-y-1">
                        <Label htmlFor="pia-password">Password</Label>
                        <Input
                          id="pia-password"
                          type="password"
                          value={piaPassword}
                          onChange={(e) => setPiaPassword(e.target.value)}
                          placeholder="PIA password"
                        />
                      </div>
                    </div>
                    <Button type="button" onClick={addOpenVpnPiaCredential}>Add Credential</Button>
                  </CardContent>
                </Card>
              )}

              {protocol === 'wireguard' && (
                <Card className="border-border/60">
                  <CardHeader>
                    <CardTitle className="text-base">Wireguard Configuration</CardTitle>
                    <CardDescription>
                      Drop a .conf file to parse and add credentials automatically.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-4">
                    <div
                      onDragOver={(event) => {
                        event.preventDefault()
                        setIsDropActive(true)
                      }}
                      onDragLeave={() => setIsDropActive(false)}
                      onDrop={handleDrop}
                      className={[
                        'rounded-lg border-2 border-dashed p-6 transition-colors',
                        isDropActive ? 'border-blue-500 bg-blue-500/10' : 'border-border bg-muted/20',
                      ].join(' ')}
                    >
                      <div className="flex flex-col items-center gap-3 text-center">
                        <FileUp className="h-8 w-8 text-muted-foreground" />
                        <div>
                          <p className="text-sm font-medium">Drag and drop a Wireguard .conf file here</p>
                          <p className="text-xs text-muted-foreground">or upload manually using the picker below</p>
                        </div>
                        <Label htmlFor="wg-file" className="cursor-pointer">
                          <div className="inline-flex items-center gap-2 rounded-md border px-3 py-2 text-sm">
                            <Upload className="h-4 w-4" />
                            Select File
                          </div>
                        </Label>
                        <Input
                          id="wg-file"
                          type="file"
                          accept=".conf,text/plain"
                          className="hidden"
                          onChange={async (event) => {
                            const file = event.target.files?.[0]
                            if (file) await handleWireguardFile(file)
                            event.target.value = ''
                          }}
                        />
                      </div>
                    </div>

                    <div className="space-y-2">
                      <Label htmlFor="wireguard-text">Or paste .conf content</Label>
                      <Textarea
                        id="wireguard-text"
                        value={wireguardInput}
                        onChange={(event) => setWireguardInput(event.target.value)}
                        placeholder="[Interface]\nPrivateKey = ...\nAddress = ...\n\n[Peer]\nEndpoint = ..."
                        rows={8}
                      />
                      <Button
                        type="button"
                        variant="secondary"
                        disabled={isParsingWireguard || !wireguardInput.trim()}
                        onClick={() => parseWireguardText(wireguardInput, 'pasted.conf')}
                      >
                        {isParsingWireguard ? 'Parsing...' : 'Parse and Add to Pool'}
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              )}
            </CardContent>
          </Card>

          {/* Expert Toggle */}
          <button
            type="button"
            onClick={() => setShowExpert(!showExpert)}
            className="flex items-center gap-2 text-sm font-medium text-muted-foreground hover:text-foreground transition-colors"
          >
            {showExpert ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />}
            {showExpert ? 'Hide Expert Settings' : 'Show Expert Settings'}
          </button>

          {showExpert && (
            <>
              {/* Health & Recovery */}
              <Card>
                <CardHeader>
                  <CardTitle>Health & Recovery Settings</CardTitle>
                  <CardDescription>How the orchestrator monitors VPN health and recovers from failures</CardDescription>
                </CardHeader>
                <CardContent className="space-y-4">
                  <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div className="space-y-1">
                      <Label htmlFor="api-port">Gluetun HTTP API Port</Label>
                      <Input
                        id="api-port"
                        type="number"
                        min={1}
                        max={65535}
                        value={apiPort}
                        onChange={(e) => setApiPort(parseInt(e.target.value, 10) || 8001)}
                        className="max-w-xs"
                      />
                      <p className="text-xs text-muted-foreground">
                        Must match HTTP_CONTROL_SERVER_ADDRESS in Gluetun. Default: 8001
                      </p>
                    </div>

                    <div className="space-y-1">
                      <Label htmlFor="health-check-interval">Health Check Interval (seconds)</Label>
                      <Input
                        id="health-check-interval"
                        type="number"
                        min={1} max={60}
                        value={healthCheckIntervalS}
                        onChange={(e) => setHealthCheckIntervalS(parseInt(e.target.value) || 5)}
                        className="max-w-xs"
                      />
                      <p className="text-xs text-muted-foreground">How often to check VPN health. Default: 5s</p>
                    </div>

                    <div className="space-y-1">
                      <Label htmlFor="port-cache-ttl">Port Cache TTL (seconds)</Label>
                      <Input
                        id="port-cache-ttl"
                        type="number"
                        min={1} max={300}
                        value={portCacheTtlS}
                        onChange={(e) => setPortCacheTtlS(parseInt(e.target.value) || 60)}
                        className="max-w-xs"
                      />
                      <p className="text-xs text-muted-foreground">How long to cache forwarded port info. Default: 60s</p>
                    </div>

                    <div className="space-y-1">
                      <Label htmlFor="unhealthy-restart-timeout">Unhealthy Restart Timeout (seconds)</Label>
                      <Input
                        id="unhealthy-restart-timeout"
                        type="number"
                        min={10} max={600}
                        value={unhealthyRestartTimeoutS}
                        onChange={(e) => setUnhealthyRestartTimeoutS(parseInt(e.target.value) || 60)}
                        className="max-w-xs"
                      />
                      <p className="text-xs text-muted-foreground">
                        Force-restart VPN container after being unhealthy for this long. Default: 60s
                      </p>
                    </div>

                    <div className="flex items-start gap-3 pt-1">
                      <Switch
                        id="restart-engines"
                        checked={restartEnginesOnReconnect}
                        onCheckedChange={setRestartEnginesOnReconnect}
                      />
                      <div>
                        <Label htmlFor="restart-engines">Restart Engines on VPN Reconnect</Label>
                        <p className="text-xs text-muted-foreground">
                          Restart engine containers when VPN reconnects to refresh their network routes. Default: on
                        </p>
                      </div>
                    </div>
                  </div>
                </CardContent>
              </Card>
            </>
          )}
        </>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Credential Pool</CardTitle>
          <CardDescription>
            Credentials are leased to dynamic VPN nodes and released when nodes are removed.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="secondary">Total: {credentials.length}</Badge>
            <Badge variant="secondary">Leased: {leaseSummary?.leased ?? 0}</Badge>
            <Badge variant="secondary">Available: {leaseSummary?.available ?? 0}</Badge>
            {loadingLeases && <span>Refreshing lease data...</span>}
          </div>

          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Provider / Protocol</TableHead>
                  <TableHead>Identifier</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {credentials.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={4} className="text-center text-sm text-muted-foreground py-6">
                      No credentials in pool yet.
                    </TableCell>
                  </TableRow>
                ) : (
                  credentials.map((credential) => {
                    const credentialId = String(credential.id || '').trim()
                    const lease = credentialId ? leasesByCredentialId.get(credentialId) : null
                    const inUse = Boolean(lease)
                    const statusText = inUse
                      ? `In Use (Node: ${lease.container_id || 'unknown'})`
                      : 'Available'

                    return (
                      <TableRow key={credential.id}>
                        <TableCell>
                          <div className="font-medium">{providerLabel(credential.provider || selectedProvider)}</div>
                          <div className="text-xs text-muted-foreground uppercase">{credential.protocol || protocol}</div>
                        </TableCell>
                        <TableCell className="text-sm">{credentialIdentifier(credential)}</TableCell>
                        <TableCell>
                          {inUse ? (
                            <Badge className="bg-green-600 hover:bg-green-600 text-white">{statusText}</Badge>
                          ) : (
                            <Badge variant="outline">{statusText}</Badge>
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            onClick={() => deleteCredential(credential.id)}
                            aria-label="Delete credential"
                          >
                            <Trash2 className="h-4 w-4 text-destructive" />
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

      {/* Save Button */}
      <div className="pt-2">
        <Button onClick={saveConfig} disabled={loading || !apiKey}>
          {loading ? 'Saving...' : 'Save VPN Settings'}
        </Button>
        {!apiKey && (
          <p className="text-xs text-destructive mt-2">API Key is required to update settings</p>
        )}
      </div>

      {message && (
        <div className="flex items-center gap-2 p-3 bg-green-500/10 border border-green-500/30 rounded-md">
          <CheckCircle2 className="h-4 w-4 text-green-500" />
          <span className="text-sm text-green-600 dark:text-green-400">{message}</span>
        </div>
      )}

      {error && (
        <div className="flex items-center gap-2 p-3 bg-destructive/10 border border-destructive rounded-md">
          <AlertCircle className="h-4 w-4 text-destructive" />
          <span className="text-sm text-destructive">{error}</span>
        </div>
      )}

      <Alert>
        <Info className="h-4 w-4" />
        <AlertDescription>
          VPN settings are persisted and applied immediately through the dynamic controller and Docker events.
          Existing engines are not restarted unless Restart Engines on VPN Reconnect is enabled.
        </AlertDescription>
      </Alert>
    </div>
  )
}
