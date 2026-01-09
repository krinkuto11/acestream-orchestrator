# Changelog

All notable changes to the AceStream Orchestrator project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.5.1] - 2026-01-09

### Changed
- **Improved lookahead provisioning algorithm**: The autoscaler now tracks a "lookahead layer" to prevent repeated provisioning triggers until all engines (including newly provisioned ones) reach the same stream layer.
  - When lookahead provisions a new engine (triggered when any engine reaches MAX_STREAMS - 1), it records the current minimum stream count across all engines
  - Subsequent lookahead triggers are blocked until ALL engines reach that recorded layer
  - This prevents the system from continuously trying to provision when only some engines are near capacity
  - Example: With 6 engines at layer 3 and one reaching layer 4, a 7th engine is provisioned. The lookahead won't trigger again until the 7th engine also reaches layer 3.

### Added
- Added `set_lookahead_layer()`, `get_lookahead_layer()`, and `reset_lookahead_layer()` methods to State class for tracking lookahead provisioning state
- Added comprehensive test suite for lookahead layer tracking (`test_lookahead_layer_tracking.py`)

### Technical Details
- Modified `app/services/autoscaler.py` to implement layer-aware lookahead logic
- Extended `app/services/state.py` with lookahead layer tracking capabilities
- Lookahead layer automatically resets when engines drop below the threshold, allowing fresh provisioning on load increase

## [Unreleased]

### Planned
- Additional performance optimizations
- Enhanced monitoring and alerting capabilities

---

## Version History

For detailed version history and release notes, see the [Releases](https://github.com/krinkuto11/acestream-orchestrator/releases) page.
