# Stats Caching Feature

## Overview

The stats caching feature improves UI responsiveness by caching expensive API endpoint responses. The UI panel polls several endpoints every 5 seconds, and some of these endpoints perform expensive operations like Docker API calls and health checks. Caching reduces load and improves response times.

## Cached Endpoints

The following endpoints are cached:

| Endpoint | TTL | Why Cached | Invalidation |
|----------|-----|------------|--------------|
| `/engines/stats/total` | 3s | Expensive Docker stats API calls for all containers | On engine provision/deletion |
| `/orchestrator/status` | 2s | Aggregates data from multiple sources (Docker, VPN, health) | On engine provision/deletion |
| `/vpn/status` | 3s | VPN health checks and status queries | N/A (expires naturally) |

## How It Works

1. **First Request (Cache Miss)**
   - Endpoint is called
   - Cache is checked but empty
   - Function executes normally (expensive operation)
   - Result is stored in cache with TTL
   - Response returned to client

2. **Subsequent Requests (Cache Hit)**
   - Endpoint is called within TTL window
   - Cache returns stored result immediately
   - No expensive operations performed
   - Fast response to client

3. **After TTL Expires**
   - Cache entry automatically expires
   - Next request becomes a cache miss
   - Fresh data is fetched and cached again

## Cache Invalidation

Cache is automatically invalidated when state changes:

- **Engine Provisioned**: Invalidates `orchestrator:status` and `stats:total`
- **Engine Deleted**: Invalidates `orchestrator:status` and `stats:total`
- **TTL Expiration**: All entries expire after their TTL (2-3 seconds)

## Monitoring Cache Performance

### View Cache Statistics

```bash
curl http://localhost:8000/cache/stats
```

Response:
```json
{
  "size": 3,
  "hits": 125,
  "misses": 15,
  "hit_rate": 89.29,
  "entries": [
    {
      "key": "stats:total",
      "age": 1.5,
      "ttl_remaining": 1.5
    },
    ...
  ]
}
```

### Manually Clear Cache

```bash
curl -X POST http://localhost:8000/cache/clear \
  -H "X-API-KEY: your_api_key"
```

## Performance Impact

### Before Caching
- UI polls every 5 seconds
- Each poll makes multiple expensive API calls
- Docker stats calls for all containers
- VPN health checks
- Aggregation of multiple data sources
- **Total load**: N requests × expensive operations

### After Caching
- UI polls every 5 seconds
- First request in TTL window is expensive
- Subsequent requests (within TTL) are fast
- **Typical scenario**: 1 expensive call + 1-2 fast cached responses per TTL window
- **Load reduction**: ~60-70% (depends on polling interval vs TTL)

## Example Timeline

```
Time: 0s   - UI polls → Cache MISS → Expensive operation → Cache SET
Time: 5s   - UI polls → Cache HIT  → Fast response (from cache)
Time: 10s  - UI polls → Cache HIT  → Fast response (from cache)  
Time: 15s  - UI polls → Cache MISS → Expensive operation → Cache SET (TTL expired)
```

## Configuration

Cache behavior is controlled by TTL values in the code:

```python
# app/main.py

# Stats endpoint - 3 second cache
cache.set(cache_key, total_stats, ttl=3.0)

# Orchestrator status - 2 second cache  
cache.set(cache_key, result, ttl=2.0)

# VPN status - 3 second cache
cache.set(cache_key, vpn_status, ttl=3.0)
```

To adjust caching behavior, modify these TTL values:
- **Lower TTL**: More frequent data refresh, higher load
- **Higher TTL**: Less frequent data refresh, lower load, more "stale" data

## Implementation Details

### Cache Service (`app/services/cache.py`)

- **Thread-safe**: Uses locks to ensure safe concurrent access
- **Automatic cleanup**: Background task removes expired entries every 60 seconds
- **Statistics tracking**: Monitors hits, misses, and hit rate
- **Pattern-based invalidation**: Can invalidate groups of keys by pattern

### Cache Key Format

```
<prefix>:<function_name>[:<args_hash>]
```

Examples:
- `stats:total` - Total stats endpoint
- `orchestrator:status` - Orchestrator status endpoint
- `vpn:status` - VPN status endpoint

### Non-Breaking Design

- Cache is transparent to API consumers
- If cache fails, falls back to direct execution
- No changes required to existing API clients
- Maintains backward compatibility

## Testing

Run cache tests:

```bash
# Unit tests for cache service
pytest tests/test_cache_service.py -v

# Integration tests for cache behavior
pytest tests/test_cache_integration.py -v

# All cache tests
pytest tests/test_cache*.py -v
```

All tests should pass (18 tests total).

## Troubleshooting

### Cache Not Working

Check cache statistics:
```bash
curl http://localhost:8000/cache/stats
```

If hit_rate is 0%, cache may not be enabled or TTL is too short.

### Stale Data

If data appears stale:
1. Check TTL values (may be too high)
2. Verify cache invalidation is working
3. Manually clear cache: `POST /cache/clear`

### High Memory Usage

Cache entries are stored in memory. If memory usage is high:
1. Check cache size: `GET /cache/stats`
2. Clear cache if needed: `POST /cache/clear`
3. Reduce TTL values to expire entries faster

### Debug Logging

Enable debug logging to see cache operations:

```python
import logging
logging.getLogger('app.services.cache').setLevel(logging.DEBUG)
```

Logs will show:
- `Cache HIT` - When cached value is used
- `Cache MISS` - When cache is empty or expired
- `Cache SET` - When value is stored in cache
- `Cache DELETE` - When entry is removed

## Future Improvements

Potential enhancements for the cache system:

1. **Configurable TTL**: Make TTL values configurable via environment variables
2. **Cache warming**: Pre-populate cache on startup
3. **More endpoints**: Add caching to other expensive endpoints
5. **Metrics export**: Export cache metrics to Prometheus
6. **Adaptive TTL**: Adjust TTL based on load and hit rate
