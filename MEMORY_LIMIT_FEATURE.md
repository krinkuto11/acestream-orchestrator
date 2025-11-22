# Memory Limit Feature - UI Changes

## New Field Added to Advanced Engine Settings

### Location
The memory limit field has been added to the **Platform Configuration** section of the Advanced Engine Settings page.

### Field Details

**Label:** Engine Memory Limit (Optional)

**Input Type:** Text input

**Placeholder:** e.g., 512m, 2g, 1024m

**Helper Text:**
> Set Docker memory limit for engine containers. Leave empty for unlimited.
> Valid formats: number with suffix (b, k, m, g). Examples: '512m', '2g', '1024m'.
> Minimum: 32m. This applies to all engine types when set.

### Position
The memory limit field is placed:
- After the "AceStream Engine Version" selector (for ARM platforms)
- Before the "Custom variant is disabled" alert message
- Inside the Platform Configuration card

### Validation
- **Frontend**: Accepts text input with format hints
- **Backend**: Validates on save using the `validate_memory_limit()` function
- **Error Messages**: Returns clear error messages for invalid formats
  - "Invalid format. Expected: number with optional suffix (b, k, m, g). Examples: '512m', '2g', '1024m'"
  - "Memory limit too low. Minimum is 32m"
  - "Memory limit too high. Maximum is 128g"
  - "Value too large or invalid" (for overflow protection)

### Accepted Values
- Empty/blank (unlimited)
- "0" (unlimited)
- "32m" to "128g" with suffixes: b, k, m, g (case insensitive)
- Examples: "512m", "2g", "1024m", "512M", "2G", "32768k"

### Configuration Priority
When provisioning engines, the system applies memory limits in this order:
1. **Custom variant config** `memory_limit` (set via UI)
2. **Global environment variable** `ENGINE_MEMORY_LIMIT` (set in .env)
3. **No limit** (if neither is configured)

## Backend Changes

### Environment Variable
Added to `.env.example`:
```bash
# Engine resource limits
# ENGINE_MEMORY_LIMIT=512m  # Optional: Set Docker memory limit for engine containers (e.g., 512m, 2g, 1024m)
```

### API Endpoints
Uses existing endpoints:
- `GET /custom-variant/config` - Returns config including `memory_limit`
- `POST /custom-variant/config` - Saves config with validation

### Docker Integration
Memory limit is applied via Docker's `mem_limit` parameter when creating containers in `start_acestream()` function.

## Testing

All 11 tests pass successfully:
- ✅ Valid memory format validation
- ✅ Invalid memory format rejection
- ✅ Too high memory limit rejection (>128g)
- ✅ Too low memory limit rejection (<32m)
- ✅ Boundary value testing
- ✅ Overflow protection
- ✅ Config integration tests
- ✅ Security scan (no vulnerabilities found)
