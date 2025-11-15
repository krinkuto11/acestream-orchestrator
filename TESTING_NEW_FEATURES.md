# Testing Guide for New Features

This guide describes the changes made and how to test them.

## 1. Fixed async/await Error

### What was fixed
- Fixed error: `object NoneType can't be used in 'await' expression` in reprovisioning
- Changed line 776 in `app/main.py` from `await ensure_minimum()` to `ensure_minimum()`

### How to test
1. Start the orchestrator
2. Go to Advanced Engine Settings page
3. Click "Reprovision All Engines" button
4. Check logs - should see:
   ```
   Starting reprovision of X engines with new custom variant settings
   Successfully reprovisioned engines with new settings
   ```
5. No error should appear in logs

## 2. UI Improvements for Reprovisioning

### Changes made
- Added status tracking and polling for reprovision operations
- Disabled button during operation
- Show success/failure messages
- Button clickable even when custom variant is disabled
- Added "Custom Variant" badge in engine list

### How to test

#### Test 1: Reprovisioning Status
1. Go to Advanced Engine Settings page
2. Make sure you have some engines running
3. Click "Reprovision All Engines" button and confirm
4. Observe:
   - Button changes to "Reprovisioning..." with spinning icon
   - Button is disabled (grayed out)
5. Wait for operation to complete
6. Observe:
   - Button returns to "Reprovision All Engines"
   - Toast notification appears with success message

#### Test 2: Button Always Clickable
1. Go to Advanced Engine Settings page
2. Turn OFF "Enable Custom Engine Variant" toggle
3. Verify "Reprovision All Engines" button is still clickable (not grayed out)
4. This allows reverting to default settings

#### Test 3: Custom Variant Badge
1. Enable custom variant in Advanced Engine Settings
2. Save settings
3. Reprovision engines
4. Go to Engines page
5. Each engine should show "Custom Variant" badge

## 3. Template Management System

### New features
- 10 template slots for saving/loading custom configurations
- Import/export templates
- Template naming
- Active template indication

### How to test

#### Test 1: Save Template
1. Go to Advanced Engine Settings page
2. Enable custom variant
3. Configure some parameters
4. Scroll to "Template Management" section
5. Click "Save Here" on any empty slot (1-10)
6. Enter a name like "Test Template 1"
7. Click Save
8. Slot should now show the template name with Load/Export/Delete buttons

#### Test 2: Load Template
1. With at least one saved template
2. Change some settings in the configuration
3. Click "Load" on the saved template
4. Verify settings are restored to the saved template values
5. Check that the slot now shows "Active" badge

#### Test 3: Template Name in Engine List
1. Load a template and ensure it's active
2. Reprovision engines
3. Go to Engines page
4. Each engine should show badge like "Custom Variant: Test Template 1"

#### Test 4: Export Template
1. Click Export button (download icon) on a saved template
2. File named `template_X.json` should download
3. Open the file - it should contain JSON with template data

#### Test 5: Import Template
1. Delete a template or use an empty slot
2. Click Import button (upload icon)
3. Select a previously exported JSON file
4. Template should be imported and visible in the slot

#### Test 6: Delete Template
1. Click Delete button (trash icon) on a saved template
2. Template should be removed
3. Slot should show "Save Here" button again

## Expected Results

All tests should pass without errors. The UI should be responsive and show clear feedback for all actions.

## Screenshots

### Advanced Engine Settings - Template Management
The template management section shows:
- 10 slots in a grid layout
- Empty slots with "Save Here" button
- Filled slots with Load/Export/Delete buttons
- Active template marked with "Active" badge

### Engines Page - Custom Variant Badge
Each engine shows:
- "Custom Variant: [Template Name]" badge when active
- Or just "Custom Variant" badge if no template is active

### Reprovisioning Operation
During reprovisioning:
- Button shows "Reprovisioning..." with spinning icon
- Button is disabled
- Status polling happens in background
- Success/failure message appears when complete
