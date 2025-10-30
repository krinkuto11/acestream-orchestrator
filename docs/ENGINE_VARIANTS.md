# AceStream Engine Variants

The orchestrator supports multiple AceStream engine variants to accommodate different architectures and optimization preferences.

## Available Variants

### 1. krinkuto11-amd64 (Default)
- **Image**: `ghcr.io/krinkuto11/acestream-http-proxy:latest`
- **Architecture**: AMD64
- **Configuration Type**: Environment variables (CONF)
- **Description**: The default variant with minimal configuration suitable for most use cases.

**Configuration Method**:
- Uses `CONF` environment variable with newline-separated arguments
- Port settings: `--http-port=<port>`, `--https-port=<port>`, `--bind-all`
- Additional environment variables: `HTTP_PORT`, `HTTPS_PORT`, `BIND_ALL`, `INTERNAL_BUFFERING`, `CACHE_LIMIT`
- When using Gluetun VPN: P2P port set via `P2P_PORT` environment variable

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
- **Image**: `jopsis/acestream:arm32-v3.2.13`
- **Architecture**: ARM32
- **Configuration Type**: Docker CMD
- **Description**: Variant for ARM32 devices (e.g., Raspberry Pi 2/3).

**Configuration Method**:
- Base command: `python main.py --bind-all --client-console --live-cache-type memory --live-mem-cache-size 104857600 --disable-sentry --log-stdout`
- Port settings appended to command: `--http-port <port> --https-port <port>`
- When using Gluetun VPN: P2P port appended as `--port <port>`

### 4. jopsis-arm64
- **Image**: `jopsis/acestream:arm64-v3.2.13`
- **Architecture**: ARM64
- **Configuration Type**: Docker CMD
- **Description**: Variant for ARM64 devices (e.g., Raspberry Pi 4).

**Configuration Method**:
- Base command: `python main.py --bind-all --client-console --live-cache-type memory --live-mem-cache-size 104857600 --disable-sentry --log-stdout`
- Port settings appended to command: `--http-port <port> --https-port <port>`
- When using Gluetun VPN: P2P port appended as `--port <port>`

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
TARGET_IMAGE=ghcr.io/krinkuto11/acestream-http-proxy:latest
```

#### Using the optimized AMD64 variant
```bash
ENGINE_VARIANT=jopsis-amd64
# TARGET_IMAGE is automatically set based on variant
```

#### Using ARM variants
```bash
# For ARM32 devices
ENGINE_VARIANT=jopsis-arm32

# For ARM64 devices
ENGINE_VARIANT=jopsis-arm64
```

## How It Works

The orchestrator automatically configures each variant based on its type:

### ENV-based Variants (krinkuto11-amd64, jopsis-amd64)
- Configuration passed via environment variables
- Port settings injected into the appropriate environment variable
- Compatible with the acestream-http-proxy image wrapper

### CMD-based Variants (jopsis-arm32, jopsis-arm64)
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

- **krinkuto11-amd64**: P2P port set via `P2P_PORT` environment variable
- **jopsis-amd64**: P2P port appended to `ACESTREAM_ARGS` as `--port <port>`
- **jopsis-arm32**: P2P port appended to command as `--port <port>`
- **jopsis-arm64**: P2P port appended to command as `--port <port>`

When Gluetun is not configured, no P2P port is passed and engines use their default P2P behavior.

## Choosing a Variant

### Choose `krinkuto11-amd64` if:
- You want the default, well-tested configuration
- You're running on AMD64/x86_64 architecture
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
3. Existing `TARGET_IMAGE` setting is still respected when `ENGINE_VARIANT=krinkuto11-amd64`

## Advanced Usage

### Custom Image Override

While variants have default images, you can override them:

```bash
ENGINE_VARIANT=jopsis-amd64
TARGET_IMAGE=my-custom-registry/acestream:custom-tag
```

The variant's configuration method will still be used, but with your custom image.

### Debugging Variant Configuration

To see how a variant is configured:

```python
from app.services.provisioner import _get_variant_config

config = _get_variant_config('jopsis-amd64')
print(config)
```

## See Also

- [Configuration Documentation](CONFIG.md)
- [API Documentation](API.md)
- [Deployment Guide](DEPLOY.md)
