# Engine Cache Management with Docker Volumes

## Overview

This document explains the engine cache management system. The system uses Docker volumes exclusively for engine caches to ensure reliability, performance, and automatic cleanup.

## Problem with Bind Mounts

Previously, AceStream engine caches could be managed using host-mounted directories (bind mounts). This approach had several drawbacks:
- Cache directories on the host were not always cleaned up properly.
- Permission issues could prevent cleanup.
- Manual intervention was often required to remove orphaned cache directories.
- Portability was limited by host filesystem paths.

## Solution: Mandatory Docker Volumes

The system now uses **Docker named volumes** for all engine caches. Bind mounts are no longer supported for stream caches.

### Benefits of Docker Volumes

- **Automatic creation**: A unique Docker volume is created for each engine (e.g., `acestream-cache-abc123def456`).
- **Automatic cleanup**: When an engine is deleted, its volume is automatically removed.
- **Better isolation**: Each engine has its own isolated cache volume.
- **Performance**: Docker volumes offer better IO performance than bind mounts on many platforms.
- **No permission issues**: Docker handles all internal permissions.
- **No host filesystem clutter**: Volumes are managed by Docker and don't clutter your host's data directories.

## Configuration

Cache mounting is controlled via the **Advanced Engine Settings** in the panel.

1. Go to the **Engines** page.
2. Select the **Settings** tab.
3. Ensure **Use Custom Engine Variant** is enabled.
4. Locate the **Engine Disk Cache (Docker Volumes)** section.
5. Toggle the switch to enable or disable disk caching.

When enabled, the orchestrator will automatically:
- Create a Docker volume when an engine is provisioned.
- Mount the volume to the engine container at `/root/.ACEStream/.acestream_cache`.
- Remove the volume when the engine is deleted.

## Monitoring and Maintenance

### Cache Monitoring

The panel (both on the Engines dashboard and in Advanced Settings) displays the **total size** being used by all active engine caches. This information is updated periodically.

### Manual Purge

If you need to clear all cache volumes (e.g., to free up space), a **Purge All Caches** button is available in the Advanced Engine Settings.
> [!WARNING]
> Purging cache volumes while engines are running may cause brief buffering for active streams as the engines re-download required data.

### Automatic Pruning

The system includes a background service that:
1. **Removes Orphaned Volumes**: Deletes any `acestream-cache-*` volumes that do not belong to an active engine.
2. **Periodic Purge**: If configured in the custom variant settings, it can periodically clear the contents of all active volumes.

## Technical Details

### Volume Naming Convention

Docker volumes are named: `acestream-cache-{container_name_prefix}`

### Lifecycle

1. **Setup**: Volumes are created fresh for each new engine instance.
2. **Mounting**: Mounted to the internal cache path of the AceStream engine.
3. **Cleanup**: Removed immediately when the engine container is stopped and removed by the orchestrator.

## References

- [Docker Volumes Documentation](https://docs.docker.com/storage/volumes/)
- [Custom Variants Guide](./CUSTOM_VARIANTS_GUIDE.md)
- [Architecture Documentation](./ARCHITECTURE.md)
