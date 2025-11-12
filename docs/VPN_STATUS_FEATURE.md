# VPN Status Enhancement and Engine Version Display

## Overview

This feature enhances the orchestrator dashboard with detailed VPN location information and engine version details, providing better visibility into the system's operation.

## Features

### 1. VPN Location Information

The orchestrator now matches VPN public IPs to Gluetun's server list to display:
- **Provider**: VPN provider name (e.g., nordvpn, protonvpn, mullvad)
- **Country**: Server country
- **City**: Server city
- **Public IP**: Current public IP address

#### How It Works

1. **Server List Caching**: Fetches Gluetun's server list from GitHub (21,874+ server IPs)
2. **Daily Updates**: Automatically refreshes the server list every 24 hours
3. **Optimized Lookup**: Uses an in-memory IP index for fast lookups
4. **Disk Caching**: Persists to `/tmp/gluetun_servers_cache.json` to survive restarts

#### API Endpoint

The `/vpn/status` endpoint now includes location information:

```json
{
  "mode": "single",
  "enabled": true,
  "connected": true,
  "public_ip": "58.98.64.104",
  "provider": "nordvpn",
  "country": "United States",
  "city": "New York",
  "forwarded_port": 51234,
  "container_name": "gluetun"
}
```

In redundant mode, location data is included for both VPN containers:

```json
{
  "mode": "redundant",
  "vpn1": {
    "container_name": "gluetun",
    "public_ip": "58.98.64.104",
    "provider": "nordvpn",
    "country": "United States",
    "city": "New York"
  },
  "vpn2": {
    "container_name": "gluetun2",
    "public_ip": "45.12.34.56",
    "provider": "protonvpn",
    "country": "Germany",
    "city": "Frankfurt"
  }
}
```

### 2. Engine Version Information

Each engine now displays:
- **Engine Variant**: Variant name from configuration (e.g., krinkuto11, jopsis)
- **Platform**: Operating system (e.g., linux)
- **Version**: AceStream engine version (e.g., 3.2.11)
- **Forwarded Port**: P2P port for forwarded engines only

#### How It Works

1. **Version Endpoint**: Queries each engine's `/webui/api/service?method=get_version` endpoint
2. **Real-time Data**: Fetched when the `/engines` endpoint is called
3. **Graceful Degradation**: Returns null if engine is unreachable

#### API Endpoint

The `/engines` endpoint now includes version information:

```json
[
  {
    "container_id": "abc123...",
    "host": "172.17.0.5",
    "port": 6878,
    "forwarded": true,
    "engine_variant": "krinkuto11",
    "platform": "linux",
    "version": "3.2.11",
    "forwarded_port": 51234
  }
]
```

### 3. UI Enhancements

#### Engines Page

- **Variant Badge**: Shows the engine variant name
- **Collapsible Details**: Click "Show Details" to see:
  - Platform
  - AceStream Version
  - Forwarded Port (for forwarded engines only)

#### VPN Page

- **Location Information**: Displays provider, country, and city
- **Public IP**: Shows the current public IP address
- **Per-VPN Details**: In redundant mode, shows location for each VPN

## Compatibility

### VPN Modes

The feature works with all VPN modes:
- **Disabled**: No VPN information shown
- **Single**: Shows location for the single VPN
- **Redundant**: Shows location for both VPNs

### Backward Compatibility

- All new fields are optional
- Existing APIs continue to work without modification
- UI gracefully handles missing data

## Performance

### VPN Location Service

- **Initial Load**: ~2-5 seconds to fetch and index 21,874 IPs
- **Subsequent Lookups**: <1ms (in-memory lookup)
- **Cache Duration**: 24 hours
- **Storage**: ~2-3 MB disk space

### Engine Version Service

- **Per-Engine Lookup**: ~50-200ms
- **Timeout**: 5 seconds
- **Caching**: None (fresh data on every request)

## Troubleshooting

### VPN Location Not Showing

1. **First Request**: Location data appears after the first request fetches the server list
2. **Network Issues**: Check if the orchestrator can reach GitHub
3. **Logs**: Check logs for "Failed to fetch Gluetun server list"

```bash
docker logs orchestrator 2>&1 | grep "Gluetun server"
```

### Engine Version Not Showing

1. **Engine Unreachable**: Ensure engines are running and healthy
2. **Firewall**: Check if the orchestrator can reach engine HTTP ports
3. **Logs**: Check for "Failed to get engine version"

```bash
docker logs orchestrator 2>&1 | grep "engine version"
```

### Performance Issues

If the server list fetch is slow:
1. Check network connectivity to GitHub
2. Consider pre-warming the cache by calling `/vpn/status` after startup
3. The cache persists to disk, so subsequent starts are faster

## Security

- **No Sensitive Data**: Location information is publicly available
- **No External Dependencies**: Uses official Gluetun server list only
- **Safe Error Handling**: Exceptions are logged but not exposed to users
- **No Authentication Required**: Uses same auth as existing endpoints

## Future Improvements

Potential enhancements for future versions:
- Background refresh of server list
- Engine version caching
- Historical location tracking
- GeoIP fallback for unknown IPs
