from .ports import alloc
from .health import list_managed
from .provisioner import (
    ACESTREAM_LABEL_HTTP,
    ACESTREAM_LABEL_HTTPS,
    ACESTREAM_LABEL_API,
    HOST_LABEL_HTTP,
    HOST_LABEL_HTTPS,
    HOST_LABEL_API,
    FORWARDED_LABEL,
    ENGINE_VARIANT_LABEL,
)
from .state import state
from .inspect import get_container_name
from ..models.schemas import EngineState
import logging

logger = logging.getLogger(__name__)


def _is_engine_marked_provisioning(engine: EngineState) -> bool:
    """Best-effort detection for engines still in provisioning transition."""
    labels = engine.labels or {}
    lifecycle = str(
        labels.get("acestream.lifecycle")
        or labels.get("engine.lifecycle")
        or ""
    ).strip().lower()
    if lifecycle == "provisioning":
        return True

    provisioning_flag = str(labels.get("acestream.provisioning") or "").strip().lower()
    return provisioning_flag in {"1", "true", "yes", "on", "pending"}


def run_reindex():
    """Perform a full state reconciliation against Docker, then reindex running containers."""
    running_container_ids = {c.id for c in list_managed() if c.status == 'running'}

    for engine in state.list_engines():
        container_id = engine.container_id
        if container_id in running_container_ids:
            continue

        is_draining = state.is_engine_draining(container_id)
        is_provisioning = _is_engine_marked_provisioning(engine)

        if is_draining or is_provisioning:
            transition_state = "draining" if is_draining else "provisioning"
            logger.info(
                f"Reindex removing {transition_state} engine {container_id[:12]} "
                "because container is no longer running"
            )
        else:
            logger.warning(
                f"Reindex detected lost engine {container_id[:12]} (missing from Docker). "
                "Removing stale state entry."
            )

        state.remove_engine(container_id)

    reindex_existing()

def reindex_existing():
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
            if HOST_LABEL_API in lbl: alloc.reserve_host(int(lbl[HOST_LABEL_API]))
        except Exception: pass
        
        # Extract VPN container assignment from labels
        vpn_container = lbl.get("acestream.vpn_container")
        
        # Reserve dynamic VPN host-mapped ports when a VPN assignment exists.
        # Only reserve one port per container (HOST_LABEL_HTTP) to avoid double-counting.
        if vpn_container:
            try:
                if HOST_LABEL_HTTP in lbl: 
                    alloc.reserve_gluetun_port(int(lbl[HOST_LABEL_HTTP]), vpn_container)
            except Exception: pass
        key = c.id
        if key not in state.engines:
            port = int(lbl.get(HOST_LABEL_HTTP) or 0)
            
            # Get container name from Docker first
            container_name = get_container_name(key)
            # If we can't get the name from Docker, use a truncated version of the container_id as fallback
            if not container_name:
                container_name = f"container-{key[:12]}"
            
            # Determine host based on VPN assignment.
            if vpn_container:
                host = vpn_container
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

            api_port = int(lbl.get(HOST_LABEL_API) or lbl.get(ACESTREAM_LABEL_API) or 62062)
            
            now = state.now()
            
            # Check if this container is marked as forwarded
            is_forwarded_label = lbl.get(FORWARDED_LABEL, "false").lower() == "true"
            
            # If VPN assigned, enforce per-VPN forwarded uniqueness.
            if vpn_container:
                should_be_forwarded = is_forwarded_label and not state.has_forwarded_engine_for_vpn(vpn_container)
            else:
                # No VPN assignment: enforce global forwarded uniqueness.
                should_be_forwarded = is_forwarded_label and not state.has_forwarded_engine()
            
            # Get engine variant from labels
            engine_variant = lbl.get(ENGINE_VARIANT_LABEL)
            
            state.engines[key] = EngineState(container_id=key, container_name=container_name, host=host, port=port, 
                                            api_port=api_port, labels=lbl, forwarded=should_be_forwarded, first_seen=now, last_seen=now, 
                                            streams=[], vpn_container=vpn_container, engine_variant=engine_variant)
            
            # Set VPN container assignment in state if present
            if vpn_container:
                state.set_engine_vpn(key, vpn_container)
                logger.debug(f"Restored VPN assignment for engine {key[:12]}: {vpn_container}")
            
            # If this is a forwarded engine, make sure it's marked in state
            if should_be_forwarded:
                state.set_forwarded_engine(key)
                logger.info(f"Reindexed forwarded engine: {key[:12]}")
