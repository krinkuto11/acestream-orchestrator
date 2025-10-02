import httpx
from ..core.config import cfg
from .docker_client import get_client

def list_managed():
    cli = get_client()
    key, val = cfg.CONTAINER_LABEL.split("=")
    return [c for c in cli.containers.list(all=True) if (c.labels or {}).get(key) == val]

def ping(host: str, port: int, path: str) -> bool:
    url = f"http://{host}:{port}{path}"
    try:
        r = httpx.get(url, timeout=3)
        return r.status_code < 500
    except Exception:
        return False

def sweep_idle():
    return {"ok": True}
