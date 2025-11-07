# VPN Exit Log Issue - Fix Documentation

## Problem Summary

Analysis of `vpn_exit.log` revealed that the `gluetun_2` container (second VPN in redundant mode) was experiencing connectivity issues and repeatedly exiting, requiring automatic restarts by the orchestrator.

## Root Cause

Network configuration inconsistency in `docker-compose.gluetun-redundant.yml`:

```yaml
# Before the fix:
services:
  gluetun1:
    networks:
      - aceserver  # ✓ Correct

  gluetun2:
    networks:
      - acestream  # ✗ TYPO - should be aceserver

  orchestrator:
    networks:
      - acestream  # ✗ Inconsistent with gluetun1

networks:
  acestream:      # ✗ Confusing alias
    name: aceserver
```

**The Issue:**
- `gluetun1` and `orchestrator` were both trying to use network "acestream"
- `gluetun2` was also trying to use "acestream"
- But the actual network name defined was "aceserver" (with an alias)
- This network name typo/inconsistency caused connectivity issues between the services

## Log Evidence

From `vpn_exit.log` (lines 72-106):

```
Line 72: Error getting VPN status for 'gluetun_2': Read timed out
Line 74: VPN container 'gluetun_2' is not running (status: exited)
Line 75: VPN 'gluetun_2' became unhealthy
Line 78: VPN double-check: None of 5 engine(s) have internet connectivity
Lines 79-101: Repeated warnings about gluetun_2 not running
Line 102: Force restarting VPN container 'gluetun_2' after 60s timeout
Line 103: VPN container 'gluetun_2' restart initiated
Line 105: VPN 'gluetun_2' recovered and is now healthy
```

The container would exit due to network connectivity issues, the orchestrator would detect this and force restart it after 60 seconds, then it would recover temporarily before failing again.

## Solution

Fixed network configuration to ensure all services use the same network:

```yaml
# After the fix:
services:
  gluetun1:
    networks:
      - aceserver  # ✓ Correct

  gluetun2:
    networks:
      - aceserver  # ✓ Fixed - now matches gluetun1

  orchestrator:
    networks:
      - aceserver  # ✓ Fixed - now matches both VPN containers

networks:
  aceserver:      # ✓ Simplified - no confusing alias
    driver: bridge
```

**Changes Made:**
1. Changed `gluetun2` network from `acestream` → `aceserver`
2. Changed `orchestrator` network from `acestream` → `aceserver`
3. Simplified network definition to use `aceserver` directly (removed alias)

## Verification

To verify the fix is working:

1. **Before deploying:**
   ```bash
   # Validate docker-compose syntax
   docker compose -f docker-compose.gluetun-redundant.yml config
   ```

2. **After deploying:**
   ```bash
   # Start the services
   docker compose -f docker-compose.gluetun-redundant.yml up -d
   
   # Check all containers are on the same network
   docker network inspect aceserver
   
   # Monitor VPN health
   docker compose -f docker-compose.gluetun-redundant.yml logs -f orchestrator | grep gluetun_2
   
   # Check orchestrator can reach both VPN containers
   docker exec orchestrator ping -c 3 gluetun1
   docker exec orchestrator ping -c 3 gluetun2
   ```

3. **Expected behavior:**
   - Both VPN containers start successfully and stay healthy
   - No "VPN container 'gluetun_2' is not running" warnings
   - No force restarts of gluetun_2
   - Engines can be successfully provisioned on both VPNs
   - VPN forwarded ports are retrieved successfully for both containers

## Impact on Other Configurations

This fix is specific to `docker-compose.gluetun-redundant.yml`. The other configurations were already correct:

- ✓ `docker-compose.yml` (standalone mode) - Uses network `orchestrator` correctly
- ✓ `docker-compose.gluetun.yml` (single VPN mode) - Uses network `aceserver` correctly

## Related Files

- Fixed: `docker-compose.gluetun-redundant.yml`
- Reference logs: `vpn_exit.log` (root directory)
- Related code: `app/services/gluetun.py` (VPN monitoring)

## Additional Notes

The orchestrator's VPN monitoring system was working correctly - it detected the unhealthy VPN and triggered automatic recovery. However, the underlying network configuration issue prevented stable operation. With this fix, the VPN containers should remain stable without requiring frequent restarts.
