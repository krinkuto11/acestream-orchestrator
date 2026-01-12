# Changelog

All notable changes to AceStream Orchestrator will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.2.1] - 2026-01-12

### Fixed
- **UI Responsiveness**: Fixed issue where the UI would become unresponsive during HLS proxy operations with timeout errors
  - Pre-initialize `HLSProxyServer` during application startup alongside `ProxyServer`
  - Prevents lazy initialization from blocking HTTP request handlers in single-worker uvicorn mode
  - Ensures the dashboard remains responsive even when HLS streams experience network timeouts
  - Follows the same pattern already established for `ProxyServer` initialization

### Technical Details
- Modified `app/main.py::_init_proxy_server()` to initialize both proxy servers during startup
- Both servers are now initialized in a background thread before the application begins handling requests
- This prevents the singleton initialization from blocking UI polling requests to endpoints like `/proxy/streams/{stream_key}/clients` and `/ace/getstream`

## [1.5.1] - Previous Release

See git history for previous changes.
