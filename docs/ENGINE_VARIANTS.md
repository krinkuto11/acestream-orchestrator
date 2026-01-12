# AceStream Engine Variants

The orchestrator supports multiple AceStream engine variants to accommodate different architectures and optimization preferences.

## Available Variants

### 1. krinkuto11-amd64 (Default)
- **Image**: `ghcr.io/krinkuto11/nano-ace:latest`
- **Architecture**: AMD64
- **Configuration Type**: Docker CMD
- **Description**: The default variant using Nano-Ace distroless image. Significantly smaller (300MB vs 1.2GB) with minimal configuration suitable for most use cases.

**Configuration Method**:
- Base command: `/acestream/acestreamengine --client-console --bind-all`
- Port settings appended to command: `--http-port <port> --https-port <port>`
- When using Gluetun VPN: P2P port appended as `--port <port>`
- **Image Size**: ~300MB (compared to 1.2GB for Jopsis variant)

### 2. jopsis-amd64
- **Image**: `jopsis/acestream:x64`
- **Architecture**: AMD64
- **Configuration Type**: Environment variables (ACESTREAM_ARGS)
- **Description**: Optimized variant with pre-configured performance settings.

**Configuration Method**:
- Uses `ACESTREAM_ARGS` environment variable with space-separated arguments
- Includes optimized defaults:
  - Memory cache: 200MB (live), variable (VOD)
  - Live buffer: 25s, VOD buffer: 10s
  - Max connections: 500, Max peers: 50
  - Access tokens configured
  - Stats reporting enabled
- Port settings appended: `--http-port <port> --https-port <port>`
- When using Gluetun VPN: P2P port appended as `--port <port>`

### 3. jopsis-arm32
- **Image**: `jopsis/acestream:${ENGINE_ARM32_VERSION}` (default: `arm32-v3.2.13`)
- **Architecture**: ARM32
- **Configuration Type**: Docker CMD
- **Description**: Variant for ARM32 devices (e.g., Raspberry Pi 2/3).

**Configuration Method**:
- Base command: `python main.py --bind-all --client-console --live-cache-type memory --live-mem-cache-size 104857600 --disable-sentry --log-stdout`
- Port settings appended to command: `--http-port <port> --https-port <port>`
- When using Gluetun VPN: P2P port appended as `--port <port>`
- Image version can be customized via `ENGINE_ARM32_VERSION` environment variable

### 4. jopsis-arm64
- **Image**: `jopsis/acestream:${ENGINE_ARM64_VERSION}` (default: `arm64-v3.2.13`)
- **Architecture**: ARM64
- **Configuration Type**: Docker CMD
- **Description**: Variant for ARM64 devices (e.g., Raspberry Pi 4, Raspberry Pi 5).

**Configuration Method**:
- Base command: `python main.py --bind-all --client-console --live-cache-type memory --live-mem-cache-size 104857600 --disable-sentry --log-stdout`
- Port settings appended to command: `--http-port <port> --https-port <port>`
- When using Gluetun VPN: P2P port appended as `--port <port>`
- Image version can be customized via `ENGINE_ARM64_VERSION` environment variable

## Usage

### Setting the Variant

Add the following to your `.env` file:

```bash
ENGINE_VARIANT=jopsis-amd64
```

Valid values: `krinkuto11-amd64`, `jopsis-amd64`, `jopsis-arm32`, `jopsis-arm64`

### Example Configurations

#### Using the default variant (krinkuto11-amd64)
```bash
ENGINE_VARIANT=krinkuto11-amd64
```

#### Using the optimized AMD64 variant
```bash
ENGINE_VARIANT=jopsis-amd64
```

#### Using ARM variants
```bash
# For ARM32 devices
ENGINE_VARIANT=jopsis-arm32

# For ARM64 devices
ENGINE_VARIANT=jopsis-arm64
```

#### Customizing ARM image versions
The ARM variants support configurable image versions to ensure compatibility with newer releases:

```bash
# Using a specific ARM32 version
ENGINE_VARIANT=jopsis-arm32
ENGINE_ARM32_VERSION=arm32-v3.2.14

# Using a specific ARM64 version
ENGINE_VARIANT=jopsis-arm64
ENGINE_ARM64_VERSION=arm64-v3.2.14
```

Available versions (as of documentation):
- ARM32: `arm32-v3.2.13`, `arm32-v3.2.14`
- ARM64: `arm64-v3.2.13`, `arm64-v3.2.14`

Check [jopsis/acestream Docker Hub](https://hub.docker.com/r/jopsis/acestream/tags) for the latest available versions.

## How It Works

The orchestrator automatically configures each variant based on its type:

### ENV-based Variants (jopsis-amd64)
- Configuration passed via environment variables
- Port settings injected into the ACESTREAM_ARGS environment variable

### CMD-based Variants (krinkuto11-amd64, jopsis-arm32, jopsis-arm64)
- Configuration passed via Docker CMD instruction
- Base command includes default settings
- Port settings appended to the command line

## Port Configuration

All variants require HTTP and HTTPS ports:
- **HTTP Port**: Used for the main AceStream HTTP API
- **HTTPS Port**: Used for secure connections (optional mapping via `ACE_MAP_HTTPS`)
- **P2P Port** (optional): Used for peer-to-peer connections when using Gluetun VPN

The orchestrator automatically:
1. Allocates available ports from configured ranges
2. Injects port settings in the format appropriate for each variant
3. Sets up port mappings (unless using Gluetun VPN)
4. When Gluetun is configured, retrieves the forwarded P2P port and passes it to engines

### P2P Port Handling by Variant

- **krinkuto11-amd64**: P2P port appended to command as `--port <port>`
- **jopsis-amd64**: P2P port appended to `ACESTREAM_ARGS` as `--port <port>`
- **jopsis-arm32**: P2P port appended to command as `--port <port>`
- **jopsis-arm64**: P2P port appended to command as `--port <port>`

When Gluetun is not configured, no P2P port is passed and engines use their default P2P behavior.

## Choosing a Variant

### Choose `krinkuto11-amd64` if:
- You want the default, well-tested configuration
- You're running on AMD64/x86_64 architecture
- You prefer a smaller Docker image size (300MB vs 1.2GB)
- You don't need specific optimizations

### Choose `jopsis-amd64` if:
- You want optimized performance settings
- You're running on AMD64/x86_64 architecture
- You need pre-configured connection limits and cache settings

### Choose `jopsis-arm32` if:
- You're running on ARM32 architecture (32-bit ARM)
- Examples: Raspberry Pi 2, Raspberry Pi 3

### Choose `jopsis-arm64` if:
- You're running on ARM64 architecture (64-bit ARM)
- Examples: Raspberry Pi 4, Raspberry Pi 5

## Testing

Run the test suite to verify variant configuration:

```bash
python tests/test_engine_variants.py
```

Run the demonstration to see how each variant is configured:

```bash
python tests/demo_engine_variants.py
```

Run the P2P port handling tests:

```bash
python tests/test_p2p_port_variants.py
```

## Troubleshooting

### Variant not loading
- Ensure `ENGINE_VARIANT` is set in your `.env` file
- Restart the orchestrator after changing the variant
- Check logs for validation errors

### Port conflicts
- Each variant uses the same port allocation system
- Ensure your port ranges (`PORT_RANGE_HOST`, `ACE_HTTP_RANGE`, `ACE_HTTPS_RANGE`) are configured correctly
- Check that ports are not already in use

### Image pull failures
- Ensure the Docker host can pull from the specified registry
- For jopsis images: verify images exist and are accessible
- For krinkuto11 image: verify GHCR access if using private registry

## Migration from Single Variant

If you're upgrading from a version without variant support:

1. The default variant (`krinkuto11-amd64`) maintains backward compatibility
2. No configuration changes required unless you want to use a different variant
3. Each variant uses its predefined Docker image automatically

## Advanced Usage

### Custom Engine Variants

The orchestrator now supports **Custom Engine Variants** that allow you to configure individual AceStream engine parameters via the UI, overriding the environment variable-based variant selection.

#### Enabling Custom Variants

1. Navigate to the **Advanced Engine** section in the dashboard sidebar
2. Toggle "Enable Custom Engine Variant" switch
3. The system will automatically detect your platform (amd64, arm32, or arm64)
4. **For AMD64 platform**: Select your preferred Nano-Ace version (3.2.11-py3.10, 3.2.11-py3.8, 3.1.75rc4-py3.7, or 3.1.74)
5. **For ARM platforms**: Select your preferred AceStream version (3.2.13 or 3.2.14)
6. Configure individual parameters across 7 categories:
   - **Basic Settings**: Console mode, bind options, access tokens
   - **Cache Configuration**: Memory/disk cache settings, cache sizes
   - **Buffer Settings**: Live and VOD buffer configuration
   - **Connection Settings**: P2P connections, bandwidth limits, port settings
   - **WebRTC Settings**: WebRTC connection options
   - **Advanced Settings**: Stats reporting, slot management, periodic checks
   - **Logging Settings**: Debug levels, log files, log rotation

#### Custom Variant Configuration

**Base Images**:
- **amd64**: `ghcr.io/krinkuto11/nano-ace:latest` (or specific version tags)
  - `ghcr.io/krinkuto11/nano-ace:3.2.11-py3.10` (default/latest - Python 3.10)
  - `ghcr.io/krinkuto11/nano-ace:3.2.11-py3.8` (Python 3.8)
  - `ghcr.io/krinkuto11/nano-ace:3.1.75rc4-py3.7` (Python 3.7)
  - `ghcr.io/krinkuto11/nano-ace:3.1.74` (Python 2.7)
- **arm32**: `jopsis/acestream:arm32-v3.2.13` or `jopsis/acestream:arm32-v3.2.14`
- **arm64**: `jopsis/acestream:arm64-v3.2.13` or `jopsis/acestream:arm64-v3.2.14`

**Note**: The AMD64 custom variant now uses the Nano-Ace distroless image (300MB), significantly smaller than the previous Jopsis variant (1.2GB).

**Parameter Configuration**:
- Each parameter can be individually enabled/disabled
- Parameters include proper units (MB, GB, seconds, etc.)
- Flag parameters show as enabled/disabled toggles
- Integer parameters that represent boolean states are shown as toggles
- P2P port parameter is VPN-aware and shows a warning when VPN is enabled

**Configuration Storage**:
- Settings are stored in `custom_engine_variant.json` in the root directory
- Configuration persists across restarts
- When enabled, custom variant overrides the `ENGINE_VARIANT` environment variable

**Applying Changes**:
1. Make your parameter changes in the UI
2. Click "Save Settings" to persist the configuration
3. Click "Reprovision All Engines" to apply changes to running engines
   - ⚠️ **Warning**: This will delete all engines and recreate them, interrupting active streams

**API Endpoints**:
- `GET /custom-variant/platform` - Get detected platform information
- `GET /custom-variant/config` - Get current custom variant configuration
- `POST /custom-variant/config` - Update custom variant configuration (requires API key)
- `POST /custom-variant/reprovision` - Reprovision all engines with new settings (requires API key)

**Available Parameters** (35 total):

| Category | Parameter | Type | Default | Description |
|----------|-----------|------|---------|-------------|
| Basic | `--client-console` | Flag | Enabled | Run engine in console mode |
| Basic | `--bind-all` | Flag | Enabled | Listen on all network interfaces |
| Basic | `--service-remote-access` | Flag | Disabled | Enable remote access to service |
| Basic | `--access-token` | String | - | Public access token for API |
| Basic | `--service-access-token` | String | - | Administrative access token |
| Basic | `--allow-user-config` | Flag | Disabled | Allow per-user custom configuration |
| Cache | `--cache-dir` | Path | ~/.ACEStream | Directory for storing cache |
| Cache | `--live-cache-type` | String | memory | Cache type for live streams (memory/disk/hybrid) |
| Cache | `--live-cache-size` | Bytes | 256MB | Live cache size |
| Cache | `--vod-cache-type` | String | disk | Cache type for VOD (memory/disk/hybrid) |
| Cache | `--vod-cache-size` | Bytes | 512MB | VOD cache size |
| Cache | `--vod-drop-max-age` | Integer | 0 | Maximum age before dropping VOD cache |
| Cache | `--max-file-size` | Bytes | 2GB | Maximum file size to cache |
| Buffer | `--live-buffer` | Integer | 10 | Live stream buffer (seconds) |
| Buffer | `--vod-buffer` | Integer | 5 | VOD buffer (seconds) |
| Buffer | `--refill-buffer-interval` | Integer | 5 | Buffer refill interval (seconds) |
| Connections | `--max-connections` | Integer | 200 | Maximum simultaneous connections |
| Connections | `--max-peers` | Integer | 40 | Maximum peers per torrent |
| Connections | `--max-upload-slots` | Integer | 4 | Number of simultaneous upload slots |
| Connections | `--auto-slots` | Boolean | Enabled | Automatic slot adjustment |
| Connections | `--download-limit` | Integer | 0 | Download speed limit (KB/s, 0=unlimited) |
| Connections | `--upload-limit` | Integer | 0 | Upload speed limit (KB/s, 0=unlimited) |
| Connections | `--port` | Integer | 8621 | Port for P2P connections (VPN-aware) |
| WebRTC | `--webrtc-allow-outgoing-connections` | Boolean | Disabled | Allow outgoing WebRTC connections |
| WebRTC | `--webrtc-allow-incoming-connections` | Boolean | Disabled | Allow incoming WebRTC connections |
| Advanced | `--stats-report-interval` | Integer | 60 | Interval for statistics reports (seconds) |
| Advanced | `--stats-report-peers` | Flag | Disabled | Include peer info in statistics |
| Advanced | `--slots-manager-use-cpu-limit` | Boolean | Disabled | Use CPU limit for slot management |
| Advanced | `--core-skip-have-before-playback-pos` | Boolean | Disabled | Skip downloaded pieces before playback position |
| Advanced | `--core-dlr-periodic-check-interval` | Integer | 10 | Periodic DLR check interval (seconds) |
| Advanced | `--check-live-pos-interval` | Integer | 10 | Interval for checking live position (seconds) |
| Logging | `--log-debug` | Integer | 0 | Debug level (0=normal, 1=verbose, 2=very verbose) |
| Logging | `--log-file` | Path | - | File for saving logs |
| Logging | `--log-max-size` | Bytes | 10MB | Max log size |
| Logging | `--log-backup-count` | Integer | 3 | Number of backup log files |

**VPN Integration**:
- When VPN (Gluetun) is enabled, the P2P port parameter shows a warning
- You can configure engines to use Gluetun's forwarded port
- Port assignment respects VPN health and redundant VPN mode

**Priority Order**:
1. Custom Variant (when enabled via UI)
2. `ENGINE_VARIANT` environment variable
3. Default variant (krinkuto11-amd64)

### Debugging Variant Configuration

To see how a variant is configured:

```python
from app.services.provisioner import get_variant_config

config = get_variant_config('jopsis-amd64')
print(config)
# Example output: {'image': 'jopsis/acestream:x64', 'config_type': 'env', ...}
```

## See Also

- [Configuration Documentation](CONFIG.md)
- [API Documentation](API.md)
- [Deployment Guide](DEPLOY.md)
