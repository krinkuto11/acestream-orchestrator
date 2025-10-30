from .ports import alloc
from .health import list_managed
from .provisioner import ACESTREAM_LABEL_HTTP, ACESTREAM_LABEL_HTTPS, HOST_LABEL_HTTP, HOST_LABEL_HTTPS, FORWARDED_LABEL
from .state import state
from .inspect import get_container_name
from ..models.schemas import EngineState
from ..core.config import cfg
import logging

logger = logging.getLogger(__name__)

def reindex_existing():
    # Clear all port allocations before reindexing to prevent double-counting
    # This ensures that we start fresh and only count actually running containers
    alloc.clear_all_allocations()
    
    for c in list_managed():
        # Only process running containers to avoid stale state
        if c.status != 'running':
            continue
            
        lbl = c.labels or {}
        try:
            if ACESTREAM_LABEL_HTTP in lbl: alloc.reserve_http(int(lbl[ACESTREAM_LABEL_HTTP]))
            if ACESTREAM_LABEL_HTTPS in lbl: alloc.reserve_https(int(lbl[ACESTREAM_LABEL_HTTPS]))
        except Exception: pass
        try:
            if HOST_LABEL_HTTP in lbl: alloc.reserve_host(int(lbl[HOST_LABEL_HTTP]))
            if HOST_LABEL_HTTPS in lbl: alloc.reserve_host(int(lbl[HOST_LABEL_HTTPS]))
        except Exception: pass
        
        # Reserve Gluetun ports if using Gluetun
        # Only reserve one port per container (use HOST_LABEL_HTTP as the primary port)
        # to avoid double-counting which would cause MAX_ACTIVE_REPLICAS limit to be hit prematurely
        if cfg.GLUETUN_CONTAINER_NAME:
            try:
                if HOST_LABEL_HTTP in lbl: 
                    alloc.reserve_gluetun_port(int(lbl[HOST_LABEL_HTTP]))
            except Exception: pass
        key = c.id
        if key not in state.engines:
            port = int(lbl.get(HOST_LABEL_HTTP) or 0)
            
            # Get container name from Docker first
            container_name = get_container_name(key)
            # If we can't get the name from Docker, use a truncated version of the container_id as fallback
            if not container_name:
                container_name = f"container-{key[:12]}"
            
            # Determine host based on Gluetun configuration
            # When using Gluetun VPN, use Gluetun container's name as host
            if cfg.GLUETUN_CONTAINER_NAME:
                host = cfg.GLUETUN_CONTAINER_NAME
            else:
                # Use container name as host for Docker containers, fallback to 127.0.0.1
                host = container_name or "127.0.0.1"
            
            # If port is 0 (missing or empty label), try to extract from Docker port mappings
            if port == 0:
                try:
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
            
            # Check if this container is marked as forwarded
            is_forwarded_label = lbl.get(FORWARDED_LABEL, "false").lower() == "true"
            
            # Only mark as forwarded if no other engine is already forwarded
            # This handles the case where multiple containers have the forwarded label (bug scenario)
            should_be_forwarded = is_forwarded_label and not state.has_forwarded_engine()
            
            state.engines[key] = EngineState(container_id=key, container_name=container_name, host=host, port=port, 
                                            labels=lbl, forwarded=should_be_forwarded, first_seen=now, last_seen=now, streams=[])
            
            # If this is a forwarded engine, make sure it's marked in state
            if should_be_forwarded:
                state.set_forwarded_engine(key)
                logger.info(f"Reindexed forwarded engine: {key[:12]}")
