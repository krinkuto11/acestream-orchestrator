# Bug Fixes: Custom Variant State and Stream Stop Error

This document describes the fixes for two related issues in the AceStream Orchestrator.

## Issue 1: Custom Engine Variant Disabled After Restart

### Problem
When the orchestrator was restarted with:
- "Enable Custom Engine Variant" toggled ON
- An active template loaded

After restart:
- The template would still show as "loaded/active"
- But "Enable Custom Engine Variant" would be OFF
- Engines would use the env-configured image instead of the custom variant

### Root Cause
When a template was loaded during orchestrator startup (or activated via API), the entire template configuration (including its `enabled` state) would overwrite the current configuration. Since templates could be saved with `enabled=false`, this would inadvertently disable the custom variant feature.

### Solution
Modified two locations in `app/main.py` to preserve the current `enabled` state when loading or activating templates:

1. **Startup template loading** (`lifespan` function):
   ```python
   # Before: overwrites enabled state
   save_custom_config(template.config)
   
   # After: preserves enabled state
   template_config = template.config.copy(deep=True)
   template_config.enabled = custom_config.enabled
   save_custom_config(template_config)
   ```

2. **Template activation endpoint** (`activate_template` function):
   ```python
   # Before: overwrites enabled state
   success = save_custom_config(template.config)
   
   # After: preserves enabled state
   current_config = get_custom_config()
   current_enabled = current_config.enabled if current_config else False
   template_config = template.config.copy(deep=True)
   template_config.enabled = current_enabled
   success = save_custom_config(template_config)
   ```

### Testing
Created comprehensive tests in `tests/test_template_enabled_state_preservation.py` that verify:
- When `enabled=True`, loading a template (saved with `enabled=False`) keeps it `True`
- When `enabled=False`, loading a template (saved with `enabled=True`) keeps it `False`

All tests pass successfully.

## Issue 2: "setSelectedStream is not defined" Error When Stopping Stream

### Problem
When stopping a stream in the UI, users would see the error:
```
setSelectedStream is not defined
```

Even though the stream would actually stop successfully, the error was confusing and indicated a bug in the code.

### Root Cause
In `app/static/panel-react/src/App.jsx`, the `handleStopStream` function contained a call to `setSelectedStream(null)`, but:
- `setSelectedStream` state setter was never defined in the component
- There was no `selectedStream` state variable
- This line was likely leftover from a previous refactoring

### Solution
Removed the erroneous line from `handleStopStream`:
```javascript
// Before
setSelectedStream(null)  // ← Error: setSelectedStream not defined
toast.success('Stream stopped successfully')

// After
toast.success('Stream stopped successfully')
```

The stream stopping functionality works correctly without this line. The UI refreshes via `fetchData()` which updates all state including the streams list.

### Testing
- Verified JavaScript syntax is correct
- The change is minimal and isolated
- Stream stopping functionality remains intact (the actual DELETE API call is unchanged)

## Impact Assessment

### What Changed
- Two functions in backend (`app/main.py`)
- One function in frontend (`app/static/panel-react/src/App.jsx`)
- Added one new test file

### What Didn't Change
- Template saving/loading logic
- Custom variant parameter configuration
- API endpoints (except internal behavior preservation)
- Stream management APIs
- UI appearance or user flows

### Backward Compatibility
These are bug fixes that restore intended behavior. No breaking changes:
- Templates saved before this fix will work correctly
- The enabled state toggle will now persist as expected
- Stream stopping will work without errors

## Verification Steps

To verify these fixes work correctly:

1. **Test Custom Variant Persistence:**
   ```bash
   # Enable custom variant in UI
   # Load a template
   # Restart orchestrator
   docker-compose restart
   # Verify "Enable Custom Engine Variant" is still ON
   # Verify template is still loaded
   # Verify engines use custom variant
   ```

2. **Test Stream Stopping:**
   ```bash
   # Start a stream via the UI or API
   # Go to Streams page
   # Click "Stop Stream" 
   # Verify no JavaScript errors in browser console
   # Verify success toast appears
   # Verify stream is stopped
   ```

3. **Run Tests:**
   ```bash
   cd /path/to/acestream-orchestrator
   PYTHONPATH=. python tests/test_template_enabled_state_preservation.py
   PYTHONPATH=. python tests/test_template_manager.py
   ```

## Files Modified

1. `app/main.py` - Preserve enabled state when loading/activating templates
2. `app/static/panel-react/src/App.jsx` - Remove undefined setSelectedStream call
3. `tests/test_template_enabled_state_preservation.py` - New test file

## Related Issues

This fix addresses the user-reported issue:
> "Before restarting the orchestrator, the Enable Custom Engine Variant was on and a template was loaded. Thus the engines were of that template. After restarting the orchestrator the template shows as loaded but the Enable Custom Engine Variant is off and thus the engines are the env-configured image."

And:
> "When stopping a stream in the UI, the following error shows even if the stream actually stops: setSelectedStream is not defined."
