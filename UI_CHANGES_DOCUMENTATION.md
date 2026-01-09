# UI Changes Screenshots

## 1. Proxy Settings Page - New Fields

The Proxy Settings page now includes two new configurable fields for stream data tolerance:

### New Fields Added:
1. **No Data Timeout Checks** 
   - Number of consecutive empty buffer checks before declaring stream ended
   - Range: 5-600 checks
   - Default: 30 checks
   - Description: "Total timeout = checks × interval. Example: 30 checks × 0.1s = 3s timeout"

2. **No Data Check Interval**
   - Seconds between buffer checks when no data is available
   - Range: 0.01-1.0 seconds
   - Default: 0.1 seconds
   - Description: "For unstable streams, increase timeout checks or interval. Example: 100 checks × 0.1s = 10s tolerance"

### Location:
Settings → Proxy → Stream Buffer Settings card (between "Initial Data Check Interval" and "Connection Timeout")

### Visual Layout:
```
┌─ Stream Buffer Settings ─────────────────────────────────────┐
│                                                               │
│ Initial Data Wait Timeout (seconds)                          │
│ [10                          ]                                │
│ Maximum time to wait for initial data...                     │
│                                                               │
│ Initial Data Check Interval (seconds)                        │
│ [0.2                         ]                                │
│ How often to check if initial data has arrived...            │
│                                                               │
│ No Data Timeout Checks                    ← NEW              │
│ [30                          ]                                │
│ Number of consecutive empty buffer checks...                 │
│ Total timeout = checks × interval. Example: 30 × 0.1s = 3s  │
│                                                               │
│ No Data Check Interval (seconds)          ← NEW              │
│ [0.1                         ]                                │
│ Seconds between buffer checks when no data is available...   │
│ For unstable streams, increase timeout checks or interval... │
│                                                               │
└───────────────────────────────────────────────────────────────┘
```

## 2. Streams Table - Client Details in Expanded Row

When expanding a stream row in the Streams table, a new "Connected Clients" section now appears showing details for each connected client.

### New Section:
**Connected Clients (N)** - Shows count and list of clients

### Client Information Displayed:
- Client ID (truncated with hover for full ID)
- IP Address
- Connection time
- Bytes sent (formatted)
- User Agent (truncated with hover)

### Location:
Streams → Click expand button on any active stream → New section appears between "Extended Stats" and "Links"

### Visual Layout:
```
┌─ Stream Details (Expanded Row) ───────────────────────────────┐
│                                                                │
│ [Stream ID, Engine, Started At, LivePos Data, etc...]        │
│                                                                │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                                │
│ 👥 Connected Clients (2)                     ← NEW SECTION    │
│                                                                │
│ ┌─────────────────────────┐  ┌─────────────────────────┐    │
│ │ Client ID               │  │ Client ID               │    │
│ │ abc123def456...         │  │ xyz789ghi012...         │    │
│ │                         │  │                         │    │
│ │ IP Address              │  │ IP Address              │    │
│ │ 192.168.1.100          │  │ 10.0.0.50              │    │
│ │                         │  │                         │    │
│ │ Connected               │  │ Connected               │    │
│ │ 10:30:45 PM            │  │ 10:32:12 PM            │    │
│ │                         │  │                         │    │
│ │ Bytes Sent              │  │ Bytes Sent              │    │
│ │ 45.2 MB                │  │ 32.8 MB                │    │
│ │                         │  │                         │    │
│ │ User Agent              │  │ User Agent              │    │
│ │ VLC/3.0.21 LibVLC...   │  │ ffmpeg/4.4.2           │    │
│ └─────────────────────────┘  └─────────────────────────┘    │
│                                                                │
│ ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━  │
│                                                                │
│ [Statistics URL] [Command URL]                                │
│                                                                │
│ [Chart with Download/Upload/Peers stats]                     │
│                                                                │
└────────────────────────────────────────────────────────────────┘
```

### Auto-Refresh:
The client list automatically refreshes every 10 seconds for active streams

### Empty State:
When no clients are connected, shows: "No clients connected"

### Loading State:
While fetching clients, shows: "Loading clients..."

## Implementation Notes:

1. **Backend Endpoint**: New `/proxy/streams/{stream_key}/clients` endpoint retrieves client data from Redis
2. **Real-time Updates**: Client data refreshes automatically every 10 seconds alongside stream stats
3. **Responsive Design**: Client cards display in a grid (1 column on mobile, 2 columns on desktop)
4. **Data Formatting**: Bytes are formatted using the existing `formatBytes` utility
5. **Graceful Degradation**: If proxy is not active or no clients exist, shows appropriate message

## Benefits:

### Proxy Settings:
- Operators can now tune stream tolerance for their specific network conditions
- No need to restart services - changes apply to new streams
- Clear documentation and examples in the UI
- Input validation prevents invalid values

### Client Details:
- Visibility into who is consuming streams
- Monitor bandwidth usage per client
- Identify problematic clients or user agents
- Debug connectivity issues
