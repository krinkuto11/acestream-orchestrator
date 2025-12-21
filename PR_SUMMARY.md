# Pull Request Summary

## Title
Fix custom variant enabled state persistence and stream stop error

## Description
This PR fixes two critical bugs in the AceStream Orchestrator:

### 1. Custom Engine Variant Disabled After Restart ✅
**Problem**: After restarting the orchestrator with "Enable Custom Engine Variant" ON and a template loaded, the toggle would be OFF, causing engines to use env-configured images instead of the custom variant.

**Root Cause**: When loading templates (during startup or via API), the entire template config including its `enabled` state would overwrite the current configuration.

**Solution**: Modified template loading logic to preserve the current `enabled` state when applying template configurations.

### 2. JavaScript Error When Stopping Stream ✅
**Problem**: When stopping a stream in the UI, users saw "setSelectedStream is not defined" error (though the stream did stop).

**Root Cause**: `handleStopStream` called `setSelectedStream(null)` but this state setter was never defined.

**Solution**: Removed the erroneous line - the UI refreshes correctly via `fetchData()`.

## Changes Made

### Backend Changes
- **app/main.py**:
  - Modified `lifespan` function to preserve enabled state when loading active template on startup
  - Modified `activate_template` endpoint to preserve enabled state when activating templates
  - Total: 16 lines changed (11 additions, 5 deletions)

### Frontend Changes
- **app/static/panel-react/src/App.jsx**:
  - Removed undefined `setSelectedStream(null)` call from `handleStopStream`
  - Total: 1 line removed

### Tests Added
- **tests/test_template_enabled_state_preservation.py**:
  - Comprehensive test suite for enabled state preservation
  - Tests both enabled=True and enabled=False scenarios
  - Total: 177 new lines

### Documentation Added
- **docs/BUG_FIXES_CUSTOM_VARIANT_AND_STREAM_STOP.md**:
  - Detailed explanation of both issues
  - Root cause analysis
  - Solution description with code examples
  - Testing and verification steps
  - Total: 152 new lines

## Testing

### Unit Tests
✅ All existing tests pass:
- `test_template_manager.py` - All 8 tests pass

✅ New tests pass:
- `test_template_enabled_state_preservation.py` - Both tests pass
  - `test_template_activation_preserves_enabled_state`
  - `test_template_activation_preserves_disabled_state`

### Code Review
✅ Code review completed with no issues (after addressing bare except clause feedback)

### Security Scan
✅ CodeQL security scan completed with 0 alerts

## Impact

### What Users Will Notice
1. **Custom Variant Toggle Persistence**: The "Enable Custom Engine Variant" toggle will now remain in the correct state after restart
2. **No More Stream Stop Errors**: Stopping streams in the UI will no longer show JavaScript errors
3. **Correct Engine Provisioning**: Engines will use the correct image (custom variant or env-configured) based on the actual toggle state

### Backward Compatibility
✅ No breaking changes
- Existing templates continue to work
- API contracts unchanged
- Only bug fixes that restore intended behavior

### Risk Assessment
🟢 **Low Risk**
- Changes are surgical and focused
- Only 3 files modified (excluding tests and docs)
- All existing functionality preserved
- Comprehensive test coverage added

## Files Changed
```
app/config/custom_engine_variant.json             |   1 +
app/main.py                                       |  16 +++--
app/static/panel-react/src/App.jsx                |   1 -
docs/BUG_FIXES_CUSTOM_VARIANT_AND_STREAM_STOP.md  | 152 ++++++++++++++++++
tests/test_template_enabled_state_preservation.py | 177 ++++++++++++++++++++
5 files changed, 342 insertions(+), 5 deletions(-)
```

## Verification Steps

To verify these fixes:

1. **Test Custom Variant Persistence**:
   - Enable custom variant in UI
   - Load a template
   - Restart orchestrator with `docker-compose restart`
   - Verify "Enable Custom Engine Variant" is still ON ✓
   - Verify template is still loaded ✓
   - Verify engines use custom variant ✓

2. **Test Stream Stopping**:
   - Start a stream
   - Go to Streams page
   - Click "Stop Stream"
   - Verify no JavaScript errors in console ✓
   - Verify success toast appears ✓
   - Verify stream is stopped ✓

3. **Run Tests**:
   ```bash
   PYTHONPATH=. python tests/test_template_enabled_state_preservation.py
   PYTHONPATH=. python tests/test_template_manager.py
   ```

## Related Issues

Fixes the user-reported issues:
1. Custom Engine Variant being disabled after restart despite template being loaded
2. JavaScript error "setSelectedStream is not defined" when stopping streams

## Checklist
- [x] Code changes are minimal and surgical
- [x] Tests added for new functionality
- [x] All existing tests pass
- [x] Code review completed
- [x] Security scan passed (0 alerts)
- [x] Documentation updated
- [x] Changes are backward compatible
