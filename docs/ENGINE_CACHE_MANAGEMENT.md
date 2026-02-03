# Engine Cache Management with Docker Volumes

## Overview

This document explains the engine cache management system and the improvements made to handle deletion properly.

## Problem

Previously, AceStream engine caches were managed using host-mounted directories (bind mounts). When engines were deleted:
- The cache directories on the host were not always cleaned up properly
- Permission issues could prevent cleanup
- Manual intervention was sometimes required to remove orphaned cache directories

## Solution

The system now supports **Docker named volumes** as the default method for managing engine caches, with backwards compatibility for host mounts.

### Docker Volumes (Recommended - Default)

When `ACESTREAM_CACHE_ROOT` is **not set** in your `.env` file:

- **Automatic creation**: A unique Docker volume is created for each engine (e.g., `acestream-cache-abc123def456`)
- **Automatic cleanup**: When an engine is deleted, its volume is automatically removed
- **Better isolation**: Each engine has its own isolated cache volume
- **No permission issues**: Docker handles all permissions internally
- **No host filesystem clutter**: Volumes are managed by Docker, not visible in host filesystem

### Host Mounts (Legacy - Optional)

When `ACESTREAM_CACHE_ROOT` **is set** in your `.env` file:

- Uses the traditional bind mount approach
- Creates subdirectories under the specified host path
- Requires proper permissions on the host filesystem
- May need manual cleanup in some cases

## Configuration

### Using Docker Volumes (Recommended)

1. **Do not set** `ACESTREAM_CACHE_ROOT` in your `.env` file (or leave it commented out)
2. Enable cache mounting in the custom variant configuration UI
3. Start the orchestrator

Example `.env`:
```bash
# ACESTREAM_CACHE_ROOT not set - will use Docker volumes
```

The orchestrator will automatically:
- Create a Docker volume when an engine is provisioned
- Mount the volume to the engine container
- Remove the volume when the engine is deleted

### Using Host Mounts (Legacy)

1. Set `ACESTREAM_CACHE_ROOT` to a host path in your `.env` file
2. Ensure the orchestrator container has write access to that path
3. Update `docker-compose.yml` to mount the path
4. Enable cache mounting in the custom variant configuration UI

Example `.env`:
```bash
ACESTREAM_CACHE_ROOT=/var/acestream/cache
```

Example `docker-compose.yml`:
```yaml
volumes:
  - /var/run/docker.sock:/var/run/docker.sock
  - ${ACESTREAM_CACHE_ROOT}:/app/data/engine_cache
  - ./config:/app/app/config
```

## Technical Details

### Volume Naming Convention

Docker volumes are named using the pattern: `acestream-cache-{container_id}`

Where `{container_id}` is the first 12 characters of the engine container's ID.

### Volume Lifecycle

1. **Creation**: When `setup_cache()` is called during engine provisioning
2. **Mounting**: Volume is mounted to the engine at the configured cache path (default: `/root/.ACEStream/.acestream_cache`)
3. **Deletion**: When `cleanup_cache()` is called during engine removal

### Cleanup Mechanisms

The system includes multiple cleanup mechanisms:

1. **Immediate cleanup**: When an engine is deleted, its cache is immediately removed
2. **Orphan pruning**: Periodic background task removes cache volumes/directories for engines that no longer exist
3. **Age-based pruning**: If configured, old cache files can be pruned based on age

## Migration Guide

### From Host Mounts to Docker Volumes

If you're currently using host mounts and want to switch to Docker volumes:

1. **Backup** any important data in your current cache directory
2. **Stop** the orchestrator
3. **Comment out** or remove `ACESTREAM_CACHE_ROOT` from your `.env` file
4. **Update** `docker-compose.yml` to remove the cache directory mount (use the new version)
5. **Start** the orchestrator
6. **Verify** that new engines are created with Docker volumes:
   ```bash
   docker volume ls | grep acestream-cache
   ```

### From Docker Volumes to Host Mounts

If you need to switch to host mounts:

1. **Stop** the orchestrator
2. **Set** `ACESTREAM_CACHE_ROOT=/your/desired/path` in your `.env` file
3. **Create** the directory: `mkdir -p /your/desired/path`
4. **Update** `docker-compose.yml` to mount the cache directory
5. **Start** the orchestrator

## Troubleshooting

### Volumes not being cleaned up

If you notice orphaned volumes:

```bash
# List all acestream cache volumes
docker volume ls | grep acestream-cache

# Manually remove orphaned volumes (be careful!)
docker volume rm acestream-cache-{id}

# Or use Docker's built-in prune (removes ALL unused volumes!)
docker volume prune
```

The orphan pruning task should handle this automatically, but manual intervention may be needed in rare cases.

### Permission issues with host mounts

If you're using host mounts and experiencing permission issues:

1. Check the ownership of the cache directory:
   ```bash
   ls -la /path/to/cache
   ```

2. Ensure the orchestrator container user has write access

3. Consider switching to Docker volumes to avoid permission issues entirely

### Checking volume usage

To see disk space used by cache volumes:

```bash
docker system df -v
```

## Benefits of Docker Volumes

1. **Automatic cleanup**: No manual intervention needed
2. **Better reliability**: Docker handles the lifecycle
3. **No permission issues**: Docker manages permissions
4. **Portable**: Volumes work consistently across different host filesystems
5. **Isolated**: Each engine has its own volume, preventing conflicts

## Performance Considerations

Docker volumes typically have similar or better performance compared to bind mounts, especially on:
- macOS (where bind mounts can be slow)
- Windows (where bind mounts have overhead)
- Linux with modern storage drivers

## References

- [Docker Volumes Documentation](https://docs.docker.com/storage/volumes/)
- [Custom Variants Guide](./CUSTOM_VARIANTS_GUIDE.md)
- [Architecture Documentation](./ARCHITECTURE.md)
