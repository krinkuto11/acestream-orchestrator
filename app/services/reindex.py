from .ports import alloc
from .health import list_managed
from .provisioner import ACESTREAM_LABEL_HTTP, ACESTREAM_LABEL_HTTPS, HOST_LABEL_HTTP, HOST_LABEL_HTTPS
from .state import state
from ..models.schemas import EngineState

def reindex_existing():
    for c in list_managed():
        lbl = c.labels or {}
        try:
            if ACESTREAM_LABEL_HTTP in lbl: alloc.reserve_http(int(lbl[ACESTREAM_LABEL_HTTP]))
            if ACESTREAM_LABEL_HTTPS in lbl: alloc.reserve_https(int(lbl[ACESTREAM_LABEL_HTTPS]))
        except Exception: pass
        try:
            if HOST_LABEL_HTTP in lbl: alloc.reserve_host(int(lbl[HOST_LABEL_HTTP]))
            if HOST_LABEL_HTTPS in lbl: alloc.reserve_host(int(lbl[HOST_LABEL_HTTPS]))
        except Exception: pass
        key = c.id
        if key not in state.engines:
            host = "127.0.0.1"
            port = int(lbl.get(HOST_LABEL_HTTP) or 0)
            
            # If port is 0 (missing or empty label), try to extract from Docker port mappings
            if port == 0:
                try:
                    # Only try to extract ports from running containers
                    if c.status == 'running':
                        # Get port mappings from Docker
                        ports = c.attrs.get('NetworkSettings', {}).get('Ports', {})
                        # Look for the AceStream HTTP port mapping
                        ace_http_port = lbl.get(ACESTREAM_LABEL_HTTP)
                        if ace_http_port:
                            port_key = f"{ace_http_port}/tcp"
                            if port_key in ports and ports[port_key]:
                                host_binding = ports[port_key][0]  # Take first binding
                                port = int(host_binding.get('HostPort', 0))
                except Exception:
                    # If extraction fails, keep port as 0
                    pass
            
            now = state.now()
            state.engines[key] = EngineState(container_id=key, host=host, port=port, labels=lbl, first_seen=now, last_seen=now, streams=[])
