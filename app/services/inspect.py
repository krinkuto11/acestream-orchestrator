from docker.errors import NotFound
from .docker_client import get_client

class ContainerNotFound(Exception):
    pass

def get_container_name(container_id: str) -> str | None:
    """Get container name from container ID. Returns None if not found."""
    try:
        cli = get_client()
        c = cli.containers.get(container_id)
        c.reload()
        attrs = c.attrs or {}
        return attrs.get("Name", "").lstrip("/") or None
    except (NotFound, Exception):
        return None

def inspect_container(container_id: str):
    cli = get_client()
    try:
        c = cli.containers.get(container_id)
    except NotFound as e:
        raise ContainerNotFound(str(e))
    c.reload()
    attrs = c.attrs or {}
    cfg = attrs.get("Config", {})
    net = attrs.get("NetworkSettings", {})

    ports = {}
    for key, arr in (net.get("Ports") or {}).items():
        if not arr: continue
        ports[key] = [{"HostIp": b.get("HostIp"), "HostPort": b.get("HostPort")} for b in arr]

    return {
        "id": c.id,
        "name": attrs.get("Name", "").lstrip("/"),
        "image": cfg.get("Image"),
        "created": attrs.get("Created"),
        "status": c.status,
        "labels": cfg.get("Labels") or {},
        "ports": ports,
    }
