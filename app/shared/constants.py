"""
Shared constants for AceStream orchestrator.
"""

class EventType:
    STREAM_SWITCH = "stream_switch"
    STREAM_SWITCHED = "stream_switched"
    STREAM_STOP = "stream_stop"
    STREAM_STOPPED = "stream_stopped"
    CLIENT_CONNECTED = "client_connected"
    CLIENT_DISCONNECTED = "client_disconnected"
    CLIENT_STOP = "client_stop"

class StreamState:
    INITIALIZING = "initializing"
    CONNECTING = "connecting"
    WAITING_FOR_CLIENTS = "waiting_for_clients"
    ACTIVE = "active"
    ERROR = "error"
    STOPPING = "stopping"
    STOPPED = "stopped"
    BUFFERING = "buffering"

VLC_USER_AGENT = "VLC/3.0.21 LibVLC/3.0.21"
