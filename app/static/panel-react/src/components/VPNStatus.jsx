import React from 'react'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { ShieldCheck, CheckCircle, XCircle, AlertTriangle } from 'lucide-react'
import { formatTime } from '../utils/formatters'

function formatDuration(seconds) {
  if (!seconds) return '0s'
  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = Math.floor(seconds % 60)
  if (minutes === 0) return `${remainingSeconds}s`
  return `${minutes}m ${remainingSeconds}s`
}

// Convert country name to flag emoji
function getCountryFlag(countryName) {
  if (!countryName) return null
  
  // Map of country names to ISO 3166-1 alpha-2 codes
  const countryToCode = {
    'afghanistan': 'AF', 'albania': 'AL', 'algeria': 'DZ', 'andorra': 'AD', 'angola': 'AO',
    'argentina': 'AR', 'armenia': 'AM', 'australia': 'AU', 'austria': 'AT', 'azerbaijan': 'AZ',
    'bahamas': 'BS', 'bahrain': 'BH', 'bangladesh': 'BD', 'barbados': 'BB', 'belarus': 'BY',
    'belgium': 'BE', 'belize': 'BZ', 'benin': 'BJ', 'bhutan': 'BT', 'bolivia': 'BO',
    'bosnia and herzegovina': 'BA', 'botswana': 'BW', 'brazil': 'BR', 'brunei': 'BN', 'bulgaria': 'BG',
    'burkina faso': 'BF', 'burundi': 'BI', 'cambodia': 'KH', 'cameroon': 'CM', 'canada': 'CA',
    'cape verde': 'CV', 'central african republic': 'CF', 'chad': 'TD', 'chile': 'CL', 'china': 'CN',
    'colombia': 'CO', 'comoros': 'KM', 'congo': 'CG', 'costa rica': 'CR', 'croatia': 'HR',
    'cuba': 'CU', 'cyprus': 'CY', 'czech republic': 'CZ', 'czechia': 'CZ', 'denmark': 'DK',
    'djibouti': 'DJ', 'dominica': 'DM', 'dominican republic': 'DO', 'ecuador': 'EC', 'egypt': 'EG',
    'el salvador': 'SV', 'equatorial guinea': 'GQ', 'eritrea': 'ER', 'estonia': 'EE', 'ethiopia': 'ET',
    'fiji': 'FJ', 'finland': 'FI', 'france': 'FR', 'gabon': 'GA', 'gambia': 'GM',
    'georgia': 'GE', 'germany': 'DE', 'ghana': 'GH', 'greece': 'GR', 'grenada': 'GD',
    'guatemala': 'GT', 'guinea': 'GN', 'guinea-bissau': 'GW', 'guyana': 'GY', 'haiti': 'HT',
    'honduras': 'HN', 'hong kong': 'HK', 'hungary': 'HU', 'iceland': 'IS', 'india': 'IN',
    'indonesia': 'ID', 'iran': 'IR', 'iraq': 'IQ', 'ireland': 'IE', 'israel': 'IL',
    'italy': 'IT', 'jamaica': 'JM', 'japan': 'JP', 'jordan': 'JO', 'kazakhstan': 'KZ',
    'kenya': 'KE', 'kiribati': 'KI', 'korea': 'KR', 'south korea': 'KR', 'kuwait': 'KW',
    'kyrgyzstan': 'KG', 'laos': 'LA', 'latvia': 'LV', 'lebanon': 'LB', 'lesotho': 'LS',
    'liberia': 'LR', 'libya': 'LY', 'liechtenstein': 'LI', 'lithuania': 'LT', 'luxembourg': 'LU',
    'madagascar': 'MG', 'malawi': 'MW', 'malaysia': 'MY', 'maldives': 'MV', 'mali': 'ML',
    'malta': 'MT', 'marshall islands': 'MH', 'mauritania': 'MR', 'mauritius': 'MU', 'mexico': 'MX',
    'micronesia': 'FM', 'moldova': 'MD', 'monaco': 'MC', 'mongolia': 'MN', 'montenegro': 'ME',
    'morocco': 'MA', 'mozambique': 'MZ', 'myanmar': 'MM', 'namibia': 'NA', 'nauru': 'NR',
    'nepal': 'NP', 'netherlands': 'NL', 'new zealand': 'NZ', 'nicaragua': 'NI', 'niger': 'NE',
    'nigeria': 'NG', 'north korea': 'KP', 'north macedonia': 'MK', 'norway': 'NO', 'oman': 'OM',
    'pakistan': 'PK', 'palau': 'PW', 'palestine': 'PS', 'panama': 'PA', 'papua new guinea': 'PG',
    'paraguay': 'PY', 'peru': 'PE', 'philippines': 'PH', 'poland': 'PL', 'portugal': 'PT',
    'qatar': 'QA', 'romania': 'RO', 'russia': 'RU', 'russian federation': 'RU', 'rwanda': 'RW',
    'saint kitts and nevis': 'KN', 'saint lucia': 'LC', 'saint vincent and the grenadines': 'VC',
    'samoa': 'WS', 'san marino': 'SM', 'sao tome and principe': 'ST', 'saudi arabia': 'SA',
    'senegal': 'SN', 'serbia': 'RS', 'seychelles': 'SC', 'sierra leone': 'SL', 'singapore': 'SG',
    'slovakia': 'SK', 'slovenia': 'SI', 'solomon islands': 'SB', 'somalia': 'SO', 'south africa': 'ZA',
    'south sudan': 'SS', 'spain': 'ES', 'sri lanka': 'LK', 'sudan': 'SD', 'suriname': 'SR',
    'sweden': 'SE', 'switzerland': 'CH', 'syria': 'SY', 'taiwan': 'TW', 'tajikistan': 'TJ',
    'tanzania': 'TZ', 'thailand': 'TH', 'timor-leste': 'TL', 'togo': 'TG', 'tonga': 'TO',
    'trinidad and tobago': 'TT', 'tunisia': 'TN', 'turkey': 'TR', 'turkmenistan': 'TM', 'tuvalu': 'TV',
    'uganda': 'UG', 'ukraine': 'UA', 'united arab emirates': 'AE', 'united kingdom': 'GB',
    'united states': 'US', 'usa': 'US', 'uruguay': 'UY', 'uzbekistan': 'UZ', 'vanuatu': 'VU',
    'vatican city': 'VA', 'venezuela': 'VE', 'vietnam': 'VN', 'yemen': 'YE', 'zambia': 'ZM',
    'zimbabwe': 'ZW'
  }
  
  const normalized = countryName.toLowerCase().trim()
  const code = countryToCode[normalized]
  
  if (!code) return null
  
  // Convert ISO code to flag emoji
  // Flag emojis are created by combining regional indicator symbols
  const codePoints = [...code].map(char => 127397 + char.charCodeAt(0))
  return String.fromCodePoint(...codePoints)
}

function SingleVPNDisplay({ vpnData, label, emergencyMode }) {
  const isHealthy = vpnData.connected
  const HealthIcon = isHealthy ? CheckCircle : XCircle
  
  // Check if this VPN is in emergency (failed)
  const isEmergencyFailed = emergencyMode?.active && emergencyMode?.failed_vpn === vpnData.container_name

  return (
    <div>
      {label && (
        <h3 className="text-xl font-semibold mb-3">{label}</h3>
      )}
      
      {/* Emergency mode alert for this specific VPN */}
      {isEmergencyFailed && (
        <Alert variant="destructive" className="mb-4">
          <AlertTriangle className="h-4 w-4" />
          <AlertTitle>Emergency Mode - VPN Failed</AlertTitle>
          <AlertDescription>
            <p className="text-sm">
              This VPN has failed and is currently unavailable. All engines assigned to this VPN have been stopped.
            </p>
            <p className="text-sm mt-2">
              Duration: {formatDuration(emergencyMode.duration_seconds)}
            </p>
            {emergencyMode.entered_at && (
              <p className="text-xs text-muted-foreground mt-1">
                Started at: {formatTime(emergencyMode.entered_at)}
              </p>
            )}
          </AlertDescription>
        </Alert>
      )}
      
      <div className="flex gap-2 mb-4">
        <Badge variant={isHealthy ? "success" : "destructive"} className="flex items-center gap-1">
          <HealthIcon className="h-3 w-3" />
          {isHealthy ? 'Healthy' : 'Unhealthy'}
        </Badge>
        <Badge variant={vpnData.connected ? "success" : "destructive"}>
          {vpnData.connected ? 'Connected' : 'Disconnected'}
        </Badge>
        {isEmergencyFailed && (
          <Badge variant="destructive" className="flex items-center gap-1 font-bold">
            <AlertTriangle className="h-3 w-3" />
            EMERGENCY
          </Badge>
        )}
      </div>
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <div>
          <p className="text-xs text-muted-foreground">Container</p>
          <p className="text-sm font-medium">{vpnData.container_name || 'N/A'}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Public IP</p>
          <p className="text-sm font-medium font-mono">{vpnData.public_ip || 'N/A'}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Forwarded Port</p>
          <p className="text-sm font-medium">{vpnData.forwarded_port || 'N/A'}</p>
        </div>
        <div>
          <p className="text-xs text-muted-foreground">Last Check</p>
          <p className="text-sm font-medium">{formatTime(vpnData.last_check_at)}</p>
        </div>
        {vpnData.provider && (
          <div>
            <p className="text-xs text-muted-foreground">Provider</p>
            <p className="text-sm font-medium capitalize">{vpnData.provider}</p>
          </div>
        )}
        {vpnData.country && (
          <div>
            <p className="text-xs text-muted-foreground">Country</p>
            <p className="text-sm font-medium flex items-center gap-2">
              {getCountryFlag(vpnData.country) && (
                <span className="text-lg">{getCountryFlag(vpnData.country)}</span>
              )}
              {vpnData.country}
            </p>
          </div>
        )}
        {vpnData.city && (
          <div>
            <p className="text-xs text-muted-foreground">City</p>
            <p className="text-sm font-medium">{vpnData.city}</p>
          </div>
        )}
      </div>
    </div>
  )
}

function VPNStatus({ vpnStatus }) {
  const isRedundantMode = vpnStatus.mode === 'redundant'
  const overallHealthy = vpnStatus.connected
  const OverallHealthIcon = overallHealthy ? CheckCircle : XCircle
  const emergencyMode = vpnStatus.emergency_mode

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center gap-3">
          <ShieldCheck className="h-10 w-10 text-primary" />
          <div>
            <CardTitle className="text-2xl">
              VPN Status {isRedundantMode && '(Redundant Mode)'}
            </CardTitle>
            <div className="flex gap-2 mt-2">
              <Badge variant={overallHealthy ? "success" : "destructive"} className="flex items-center gap-1">
                <OverallHealthIcon className="h-3 w-3" />
                {overallHealthy ? 'Healthy' : 'Unhealthy'}
              </Badge>
              <Badge variant={vpnStatus.connected ? "success" : "destructive"}>
                {vpnStatus.connected ? 'Connected' : 'Disconnected'}
              </Badge>
              {emergencyMode?.active && (
                <Badge variant="destructive" className="flex items-center gap-1 font-bold">
                  <AlertTriangle className="h-3 w-3" />
                  EMERGENCY MODE
                </Badge>
              )}
            </div>
          </div>
        </div>
      </CardHeader>

      <CardContent>
        {/* Overall emergency mode alert for redundant mode */}
        {isRedundantMode && emergencyMode?.active && (
          <Alert variant="warning" className="mb-6">
            <AlertTriangle className="h-4 w-4" />
            <AlertTitle>System in Emergency Mode</AlertTitle>
            <AlertDescription>
              <p className="text-sm">
                Operating with reduced capacity on a single VPN due to VPN failure.
                System will automatically restore full capacity once the failed VPN recovers.
              </p>
              <div className="mt-2 flex gap-4 flex-wrap text-sm">
                <div>
                  <strong>Failed VPN:</strong> {emergencyMode.failed_vpn || 'N/A'}
                </div>
                <div>
                  <strong>Healthy VPN:</strong> {emergencyMode.healthy_vpn || 'N/A'}
                </div>
                <div>
                  <strong>Duration:</strong> {formatDuration(emergencyMode.duration_seconds)}
                </div>
              </div>
            </AlertDescription>
          </Alert>
        )}

        {isRedundantMode ? (
          <div className="space-y-6">
            {/* VPN 1 */}
            {vpnStatus.vpn1 && (
              <>
                <SingleVPNDisplay vpnData={vpnStatus.vpn1} label="VPN 1" emergencyMode={emergencyMode} />
                {vpnStatus.vpn2 && <div className="border-t my-6" />}
              </>
            )}
            
            {/* VPN 2 */}
            {vpnStatus.vpn2 && (
              <SingleVPNDisplay vpnData={vpnStatus.vpn2} label="VPN 2" emergencyMode={emergencyMode} />
            )}
          </div>
        ) : (
          /* Single VPN mode - show simple view */
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <div>
              <p className="text-xs text-muted-foreground">Container</p>
              <p className="text-sm font-medium">{vpnStatus.container || 'N/A'}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Public IP</p>
              <p className="text-sm font-medium font-mono">{vpnStatus.public_ip || 'N/A'}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Forwarded Port</p>
              <p className="text-sm font-medium">{vpnStatus.forwarded_port || 'N/A'}</p>
            </div>
            <div>
              <p className="text-xs text-muted-foreground">Last Check</p>
              <p className="text-sm font-medium">{formatTime(vpnStatus.last_check_at)}</p>
            </div>
            {vpnStatus.provider && (
              <div>
                <p className="text-xs text-muted-foreground">Provider</p>
                <p className="text-sm font-medium capitalize">{vpnStatus.provider}</p>
              </div>
            )}
            {vpnStatus.country && (
              <div>
                <p className="text-xs text-muted-foreground">Country</p>
                <p className="text-sm font-medium flex items-center gap-2">
                  {getCountryFlag(vpnStatus.country) && (
                    <span className="text-lg">{getCountryFlag(vpnStatus.country)}</span>
                  )}
                  {vpnStatus.country}
                </p>
              </div>
            )}
            {vpnStatus.city && (
              <div>
                <p className="text-xs text-muted-foreground">City</p>
                <p className="text-sm font-medium">{vpnStatus.city}</p>
              </div>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  )
}

export default VPNStatus
