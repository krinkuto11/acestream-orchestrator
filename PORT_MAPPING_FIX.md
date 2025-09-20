# AceStream Port Mapping Fix

## Problem Solved

The acestream containers weren't using the port that was passed to them via the CONF environment variable. This caused a mismatch where:

- Docker mapped `host_port → allocated_container_port` (e.g., 19000 → 40001)
- But acestream inside the container bound to the port from CONF (e.g., 6879)
- Result: No connection possible because the ports didn't align

## Solution

Modified `start_acestream()` in `app/services/provisioner.py` to:

1. **Parse ports from user CONF**: Extract `--http-port=XXXX` and `--https-port=YYYY` from user-provided CONF
2. **Use parsed ports for Docker mapping**: Set container ports to match what acestream will bind to
3. **Validate ports**: Ensure ports are in valid range (1-65535) and don't conflict  
4. **Reserve managed ports**: If user ports fall within orchestrator ranges, reserve them to prevent conflicts

## Key Changes

### Before Fix
```python
# User provides CONF: "--http-port=6879\n--https-port=6880\n--bind-all"
c_http = alloc.alloc_http()  # Returns 40001
c_https = alloc.alloc_https() # Returns 45001
ports = {f"{c_http}/tcp": host_http}  # {"40001/tcp": 19000}
# Result: Docker maps 19000→40001, but acestream binds to 6879 (mismatch!)
```

### After Fix
```python
# User provides CONF: "--http-port=6879\n--https-port=6880\n--bind-all"
user_http, user_https = _parse_ports_from_conf(final_conf)  # 6879, 6880
c_http = user_http if user_http else alloc.alloc_http()      # 6879
c_https = user_https if user_https else alloc.alloc_https() # 6880
ports = {f"{c_http}/tcp": host_http}  # {"6879/tcp": 19000}
# Result: Docker maps 19000→6879, acestream binds to 6879 (match!)
```

## Docker Compose Example

This working Docker Compose configuration should now work with the orchestrator:

```yaml
services:
  acestream:
    container_name: acestream
    image: ghcr.io/krinkuto11/acestream-http-proxy:latest
    restart: unless-stopped
    ports:
       - "6879:6879"
    environment:
       CONF: |-
         --http-port=6879
         --https-port=6880
         --bind-all
```

When the orchestrator provisions this, it will now:
- Parse 6879 and 6880 from the CONF
- Use 6879 as the container HTTP port for Docker mapping
- Use 6880 as the container HTTPS port 
- The acestream process inside will bind to 6879 (from CONF)
- Traffic flow: `external → host_port → 6879 → acestream process`

## Testing

Two focused tests validate the fix:

1. `test_conf_fix.py` - Ensures CONF environment variable handling still works
2. `test_port_mapping_fix.py` - Validates the new port parsing and mapping logic

Both tests pass, confirming the fix resolves the issue while maintaining backward compatibility.