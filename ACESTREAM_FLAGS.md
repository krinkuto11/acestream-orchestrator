# ACESTREAM ENGINE: FLAGS & CONFIGURATION
> **Source of Truth** - Updated 2026-01-25

This document serves as the primary reference for all command-line flags accepted by the `acestreamengine` executable. It consolidates information from standard help output, runtime analysis (ambiguity errors), and static bytecode inspection (`modules.zip`).

> [!NOTE]
> Flags are case-sensitive (usually lower-kebab-case). Values indicated as `<N>` expect integers. `Bool (0/1)` means the flag requires `0` (off) or `1` (on) and **is not** a standard switch.

---

## 1. Core Connection & Network
Basic network configuration for the P2P node.

| Flag | Description |
| :--- | :--- |
| `--port <PORT>` | Fixed port for P2P node communication (Default: 8621). |
| `--bind-all` | Bind API and P2P sockets to all interfaces (`0.0.0.0`). |
| `--http-port <PORT>` | HTTP API port (Default: 6878). |
| `--https-port <PORT>` | HTTPS API port. |
| `--api-port <PORT>` | Legacy TCP API port. |
| `--fallback-to-dynamic-ports` | If configured ports are busy, try random available ports. |
| `--random-port` | Use a random port for P2P. |
| `--upnp-nat-access` | Attempt UPnP port mapping for NAT traversal. |
| `--nat-detect` | Enable/Disable automatic NAT detection. |
| `--ipv6-enabled` | Enable IPv6 support (Experimental). |
| `--ipv6-binds-v4` | IPv6 sockets also handle IPv4. |
| `--max-connections <N>` | Global maximum number of TCP connections. |
| `--max-socket-connects <N>` | Max outgoing connection attempts. |
| `--timeout-check-interval <SEC>` | Interval to check for connection timeouts. |
| `--keepalive-interval <SEC>` | Interval for keepalive messages to peers. |

## 2. Bandwidth & Limits
Control upload/download rates and peering limits.

| Flag | Description |
| :--- | :--- |
| `--download-limit <Kb/s>` | Global download speed limit. |
| `--upload-limit <Kb/s>` | Global upload speed limit. |
| `--max-download-rate <Kb/s>` | Alias for download-limit. |
| `--max-upload-rate <Kb/s>` | Alias for upload-limit. |
| `--max-upload-slots <N>` | Max number of peers to upload to simultaneously. |
| `--max-peers <N>` | Max connected peers per stream. |
| `--max-peers-limit <N>` | Hard limit for max peers. |
| `--min-peers <N>` | Minimum desired peers before aggressive searching. |
| `--slots` | (Internal) Upload slots configuration. |

## 3. Cache & Storage
Manage how the engine buffers and stores data (disk vs RAM).

| Flag | Description |
| :--- | :--- |
| `--cache-dir <PATH>` | Path to store cache files. |
| `--state-dir <PATH>` | Path for persistent state (settings, keys). |
| `--cache-limit <GB>` | Max cache size limit (in GB). |
| `--cache-max-bytes <BYTES>` | Max cache size in bytes. |
| `--disk-cache-limit <BYTES>` | Max size of cache on disk. |
| `--memory-cache-limit <BYTES>` | Max size of cache in RAM. |
| `--max-file-size <BYTES>` | Max supported file size (prevents allocating too large files). |
| `--allow-multiple-threads` | Allow multithreaded disk I/O. |
| `--buffer-reads` | Enable/Disable read buffering. |
| `--reserve-space` | Pre-allocate disk space for files. |

## 4. Live Streaming (Advanced)
Critical tuning for live broadcasts. **Note:** Many flags here expect explicit `0` or `1` values, not just presence.

| Flag | Type & Value | Description |
| :--- | :--- | :--- |
| `--live-cache-type <TYPE>` | `memory` / `disk` | Storage backend for live streams. |
| `--live-mem-cache-size <BYTES>` | Integer | Max RAM buffer size for live. |
| `--live-disk-cache-size <BYTES>` | Integer | Max Disk buffer size for live. |
| `--live-buffer-time <SEC>` | Integer | Target buffer duration before playback starts. |
| `--live-max-buffer-time <SEC>` | Integer | Maximum buffer to accumulate. |
| `--live-adjust-buffer-time <0/1>` | Bool (0/1) | Dynamically adjust buffer based on stability. |
| `--live-disable-multiple-read-threads <0/1>` | Bool (0/1) | Force single-threaded reading (CPU optimization). |
| `--live-stop-main-read-thread <0/1>` | Bool (0/1) | (Experimental) Threading optimization. |
| `--live-cache-auto-size <0/1>` | Bool (0/1) | Auto-scale cache based on available RAM. |
| `--live-cache-auto-size-reserve <BYTES>` | Integer | RAM to leave free when auto-scaling. |
| `--live-cache-max-memory-percent <PCT>` | Integer (0-100) | % of RAM allowed for auto-cache. |
| `--live-aux-seeders` | Flag | Enable auxiliary seeders for live streams. |
| `--check-live-pos-interval <SEC>` | Integer | Frequency to check live stream position (e.g. `1`, `5`). |

## 5. VOD (Video on Demand)
Tuning for file playback.

| Flag | Description |
| :--- | :--- |
| `--vod-cache-type <memory/disk>` | Storage backend for VOD. |
| `--vod-buffer <SEC>` | Buffer size in seconds. |
| `--vod-drop-max-age <SEC>` | Time to keep old data in VOD cache. |
| `--preload-vod` | Pre-buffer VOD content. |

## 6. Logging & Debugging
Essential for troubleshooting.

> **Warning:** There is no generic `--debug` flag. You must use specific sub-module debug flags or `--verbose`.

| Flag | Description |
| :--- | :--- |
| `--log-stdout` | Output logs to console standard output. |
| `--log-stderr` | Output logs to console standard error. |
| `--log-file <PATH>` | Write logs to specific file. |
| `--log-max-size <BYTES>` | Log rotation size. |
| `--log-backup-count <N>` | Keep N backup log files. |
| `--log-level <LEVEL>` | Set verbosity (`debug`, `info`, `warning`, `error`). |
| `--log-stdout-level <LEVEL>` | Force specific log level for stdout. |
| `--verbose <MODULES>` | Enable verbose logging for specific modules (comma separated). |
| `--debug-sentry` | Debug error reporting. |
| `--enable-profiler <0/1>` | Enable internal performance profiler. |
| `--stats-report-interval <SEC>` | Interval for reporting P2P statistics. |
| `--stats-report-peers <N>` | Enable (`1`) or limit (`N`) peer reporting in stats. |

### Module-Specific Debug Flags
Use these to enable detailed tracing for specific components:
*   `--debug-downloader`
*   `--debug-player`
*   `--debug-backend`
*   `--debug-connecter`
*   `--debug-pieces`
*   `--debug-choker`
*   `--debug-webrtc-transport`
*   `--debug-dht`
*   `--debug-magnet`
*   `--debug-memory-logger`

## 7. Account & Authentication

| Flag | Description |
| :--- | :--- |
| `--login <USER>` | AceStream account login. |
| `--password <PASS>` | AceStream account password. |
| `--access-token <TOKEN>` | API access token. |
| `--service-access-token` | Internal service token. |

## 8. Source Node & Broadcasting
Flags for running as a source or relay node.

| Flag | Description |
| :--- | :--- |
| `--stream-source-node` | Run in source mode. |
| `--support-node` | Run in support node mode (relay). |
| `--name <NAME>` | Node name. |
| `--title <TITLE>` | Stream title. |
| `--source <URL>` | Input source URL (HTTP/UDP). |
| `--bitrate <BPS>` | Stream bitrate hint. |
| `--piece-length <BYTES>` | Torrent piece size configuration. |
| `--duration <SEC>` | Stream duration hint. |
| `--publish-dir <PATH>` | Directory to publish for VOD. |
| `--tracker <URL>` | Custom tracker URL(s). |
| `--private-source` | Mark source as private. |
| `--provider-key <KEY>` | Content provider key. |

## 9. Internal / Unsupported
Discovered via static analysis. Likely used by internal P2P libraries (BitTornado).

- `--check-hashes`: Verify integrity of pieces.
- `--allow-source-download`: Allow downloading from the source (HTTP) directly if peers fail.
- `--allow-support-download`: Use support nodes.
- `--webui-port`: Port for the web interface (if enabled).
- `--super-seeder`: Enable super-seeder mode.
- `--ip-filter <PATH>`: Path to IP filter file.
- `--user-agent <STR>`: Custom user agent for HTTP requests.
- `--encryption <0/1/2>`: Protocol encryption (0=forced, 1=enabled, 2=required).
