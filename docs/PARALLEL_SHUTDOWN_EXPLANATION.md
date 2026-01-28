# Parallel Container Shutdown Implementation

## Problem Statement

The startup and shutdown sequences were taking too long due to sequential container stopping:

```
2026-01-14 19:49:52,263 INFO app.services.state: Stopping container c43669fd7f8a
2026-01-14 19:50:02,420 INFO app.services.state: Successfully stopped container c43669fd7f8a
2026-01-14 19:50:02,420 INFO app.services.state: Stopping container d35d84890cef
2026-01-14 19:50:12,553 INFO app.services.state: Successfully stopped container d35d84890cef
...
```

**Total time for 6 containers**: ~60 seconds (6 × 10 seconds each)

## Solution

The cleanup process now stops all containers in parallel using Python's `ThreadPoolExecutor`:

### Key Changes

1. **Import ThreadPoolExecutor** (`app/services/state.py`):
```python
from concurrent.futures import ThreadPoolExecutor, as_completed
```

2. **Parallel Execution Pattern** (matches existing pattern in `docker_stats.py`):
```python
max_workers = min(len(managed_containers), 10)
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    # Submit all stop tasks
    futures = {executor.submit(stop_single_container, container): container 
              for container in managed_containers}
    
    # Wait for all tasks to complete
    for future in as_completed(futures):
        if future.result():
            containers_stopped += 1
```

### Benefits

- **83% faster**: Shutdown time reduced from ~60s to ~10s for 6 containers
- **Scalable**: Works with any number of containers
- **Safe**: Limited to max 10 workers to avoid overwhelming Docker daemon
- **Robust**: Error handling preserved - failures in one container don't block others
- **Consistent**: Uses same pattern as existing `docker_stats.py` module

### Performance Comparison

| Containers | Sequential (Old) | Parallel (New) | Improvement |
|-----------|------------------|----------------|-------------|
| 1         | 10s              | 10s            | 0%          |
| 6         | 60s              | 10s            | 83%         |
| 10        | 100s             | 10s            | 90%         |
| 20        | 200s             | 20s            | 90%         |

### Expected Log Output (After Fix)

```
2026-01-14 XX:XX:XX,XXX INFO app.services.state: Found 6 managed containers to stop
2026-01-14 XX:XX:XX,XXX INFO app.services.state: Stopping container c43669fd7f8a
2026-01-14 XX:XX:XX,XXX INFO app.services.state: Stopping container d35d84890cef
2026-01-14 XX:XX:XX,XXX INFO app.services.state: Stopping container 3affb8f2d3ed
2026-01-14 XX:XX:XX,XXX INFO app.services.state: Stopping container 21f4b5c31e08
2026-01-14 XX:XX:XX,XXX INFO app.services.state: Stopping container 91373ff104f7
2026-01-14 XX:XX:XX,XXX INFO app.services.state: Stopping container 4bdf6e7bd9d2
2026-01-14 XX:XX:XX,YYY INFO app.services.state: Successfully stopped container c43669fd7f8a
2026-01-14 XX:XX:XX,YYY INFO app.services.state: Successfully stopped container d35d84890cef
2026-01-14 XX:XX:XX,YYY INFO app.services.state: Successfully stopped container 3affb8f2d3ed
...
2026-01-14 XX:XX:XX,ZZZ INFO app.services.state: Stopped 6 containers during cleanup
```

Notice all "Stopping container" logs appear almost simultaneously (within milliseconds), and all complete around the same time (~10 seconds later).

## Testing

Comprehensive tests added in `tests/test_parallel_shutdown.py`:

- ✅ Parallel execution (verifies timing)
- ✅ Error handling (continues on failures)
- ✅ Empty container list handling
- ✅ Max workers limit (prevents Docker daemon overload)

Run tests with:
```bash
python -m pytest tests/test_parallel_shutdown.py -v
```

## Technical Details

- Uses `concurrent.futures.ThreadPoolExecutor` (Python standard library)
- Max 10 concurrent workers to balance speed vs Docker daemon load
- Each container gets its own thread for `docker stop` operation
- Errors in individual containers don't block others
- Follows existing codebase patterns (see `app/services/docker_stats.py`)
