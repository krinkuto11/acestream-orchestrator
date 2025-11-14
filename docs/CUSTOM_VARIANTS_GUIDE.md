# Custom Engine Variants - User Guide

## Overview

The Custom Engine Variants feature allows you to configure individual AceStream engine parameters through the web dashboard, giving you fine-grained control over engine behavior without editing environment variables or configuration files.

## Accessing the Feature

1. Open the AceStream Orchestrator dashboard at `http://localhost:8000/panel`
2. Click on **"Advanced Engine"** in the left sidebar navigation
3. The Advanced Engine Settings page will load

## Platform Detection

The system automatically detects your platform architecture:
- **amd64** (x86_64) - Most desktops and servers
- **arm32** - 32-bit ARM devices (Raspberry Pi 2/3)
- **arm64** - 64-bit ARM devices (Raspberry Pi 4/5)

The detected platform is displayed at the top of the page in a badge.

## Enabling Custom Variant

1. Locate the **"Enable Custom Engine Variant"** toggle
2. Switch it to **ON** (enabled)
3. The system will now use your custom parameters instead of the `ENGINE_VARIANT` environment variable
4. For ARM platforms, an additional version selector will appear

### ARM Version Selection

For ARM32 and ARM64 platforms, you can choose between:
- **v3.2.13** - Stable version
- **v3.2.14** - Latest version with newer features

## Configuring Parameters

Parameters are organized into 7 categories (tabs):

### 1. Basic Settings
Essential engine configuration:
- **Client Console Mode**: Run engine in console mode (recommended: enabled)
- **Bind All Interfaces**: Listen on all network interfaces (recommended: enabled)
- **Service Remote Access**: Enable remote access
- **Access Tokens**: Public and service access tokens for API
- **Allow User Config**: Allow per-user configuration

### 2. Cache Configuration
Memory and disk caching:
- **Cache Directory**: Where to store cache files
- **Live Cache Type**: memory/disk/hybrid for live streams
- **Live Cache Size**: Size in MB (default: 256MB)
- **VOD Cache Type**: memory/disk/hybrid for VOD
- **VOD Cache Size**: Size in MB (default: 512MB)
- **Max File Size**: Maximum file size to cache in GB

### 3. Buffer Settings
Streaming buffer configuration:
- **Live Buffer**: Buffer duration for live streams (seconds)
- **VOD Buffer**: Buffer duration for VOD (seconds)
- **Refill Buffer Interval**: How often to refill buffer (seconds)

### 4. Connection Settings
P2P and bandwidth configuration:
- **Max Connections**: Maximum simultaneous connections
- **Max Peers**: Maximum peers per torrent
- **Max Upload Slots**: Number of upload slots
- **Auto Slots**: Enable automatic slot adjustment
- **Download Limit**: Speed limit in KB/s (0 = unlimited)
- **Upload Limit**: Speed limit in KB/s (0 = unlimited)
- **P2P Port**: Port for P2P connections
  - ⚠️ When VPN is enabled, a warning appears suggesting use of Gluetun's forwarded port

### 5. WebRTC Settings
WebRTC connection options:
- **Allow Outgoing WebRTC**: Enable outgoing connections
- **Allow Incoming WebRTC**: Enable incoming connections

### 6. Advanced Settings
Fine-tuning options:
- **Stats Report Interval**: How often to report statistics (seconds)
- **Stats Report Peers**: Include peer information in stats
- **CPU Limit for Slots**: Use CPU limit for slot management
- **Skip Before Playback**: Skip downloaded pieces before playback
- **DLR Check Interval**: Periodic check interval
- **Live Position Check**: Interval for checking live position

### 7. Logging Settings
Debug and logging configuration:
- **Debug Level**: 0 (normal), 1 (verbose), 2 (very verbose)
- **Log File**: Path to log file
- **Max Log Size**: Maximum log file size in MB
- **Log Backup Count**: Number of backup log files to keep

## Using Parameters

Each parameter has:
1. **Enable/Disable Toggle** (right side): Turn the parameter on or off
2. **Configuration Area** (appears when enabled):
   - **Flags**: Simple enabled/disabled toggle
   - **Numbers**: Input field with proper units (MB, GB, seconds, etc.)
   - **Strings**: Text input field
   - **Options**: Dropdown selector

### Example: Configuring Live Cache

1. Navigate to the **Cache** tab
2. Find **"Live Cache Type"**
3. Toggle it to **enabled**
4. Select your preferred type from the dropdown: memory, disk, or hybrid
5. Find **"Live Cache Size"**
6. Toggle it to **enabled**
7. Enter the size in MB (e.g., 512 for 512MB)

## Saving Configuration

1. Make your desired changes to parameters
2. Click the **"Save Settings"** button in the top right
3. A success message will appear when saved
4. Configuration is stored in `custom_engine_variant.json`

⚠️ **Important**: Saving only persists the configuration. Engines must be reprovisioned to apply changes.

## Reprovisioning Engines

To apply your new configuration to running engines:

1. Click the **"Reprovision All Engines"** button in the top right
2. A confirmation dialog will appear warning that all streams will be interrupted
3. Click **"OK"** to proceed
4. All existing engines will be stopped and removed
5. New engines will be provisioned with your custom settings
6. The process happens in the background (takes a few seconds)

⚠️ **Warning**: Reprovisioning deletes ALL engines and interrupts ALL active streams. Only do this during maintenance windows or when no streams are active.

## Best Practices

### Performance Tuning
1. **Memory Cache**: Use memory cache for live streams on systems with adequate RAM
2. **Buffer Size**: Increase live buffer for unstable connections
3. **Connection Limits**: Increase max connections and peers for better P2P performance
4. **Upload Limits**: Set appropriate upload limits to prevent bandwidth saturation

### Resource Management
1. **Cache Sizes**: Balance between performance and available disk/memory
2. **Log Settings**: Keep log sizes reasonable to prevent disk space issues
3. **Max File Size**: Limit based on available storage

### VPN Configuration
- When using VPN, configure the P2P port to use Gluetun's forwarded port
- This ensures proper port forwarding through the VPN tunnel

### Debugging
1. Enable **Stats Report Peers** for detailed P2P information
2. Increase **Debug Level** to 1 or 2 for troubleshooting
3. Set **Log File** to persist logs for analysis
4. Adjust **Stats Report Interval** for more frequent updates

## Troubleshooting

### Configuration Not Applying
- Ensure you clicked **"Save Settings"**
- Check that custom variant is **enabled**
- Click **"Reprovision All Engines"** to apply changes

### Invalid Configuration Error
- Check that all required parameters have valid values
- Ensure numeric values are positive
- Verify string parameters don't have special characters

### Engines Not Starting
- Review log files for errors
- Check that cache directories exist and are writable
- Verify port ranges don't conflict with other services
- Ensure cache sizes don't exceed available resources

### Performance Issues
- Reduce cache sizes if memory is limited
- Lower connection limits on resource-constrained systems
- Adjust buffer sizes based on network conditions
- Check log files for warnings or errors

## Disabling Custom Variant

To revert to environment variable-based configuration:

1. Toggle **"Enable Custom Engine Variant"** to **OFF**
2. Click **"Save Settings"**
3. Click **"Reprovision All Engines"** to apply
4. The system will now use the `ENGINE_VARIANT` environment variable

## Advanced Usage

### Configuration File Location
The configuration is stored in:
```
/path/to/orchestrator/custom_engine_variant.json
```

### Manual Configuration
While not recommended, you can manually edit the JSON file:
```json
{
  "enabled": true,
  "platform": "amd64",
  "arm_version": "3.2.13",
  "parameters": [
    {
      "name": "--client-console",
      "type": "flag",
      "value": true,
      "enabled": true
    }
  ]
}
```

After manual edits:
1. Restart the orchestrator to reload the configuration
2. Or use the API endpoint: `POST /custom-variant/config` to reload

### API Integration
The feature exposes REST API endpoints:
- `GET /custom-variant/platform` - Get platform info
- `GET /custom-variant/config` - Get configuration
- `POST /custom-variant/config` - Update configuration (requires API key)
- `POST /custom-variant/reprovision` - Reprovision engines (requires API key)

See [API.md](API.md) for detailed endpoint documentation.

## Summary

The Custom Engine Variants feature provides:
- ✅ 35+ configurable parameters
- ✅ Platform auto-detection
- ✅ User-friendly web interface
- ✅ Per-parameter enable/disable
- ✅ Real-time validation
- ✅ VPN-aware configuration
- ✅ Hot-reload support via reprovisioning

For more information, see:
- [ENGINE_VARIANTS.md](ENGINE_VARIANTS.md) - Technical details
- [API.md](API.md) - API endpoint documentation
- [PANEL.md](PANEL.md) - Dashboard guide
