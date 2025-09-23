import httpx
from ..core.config import cfg
from .docker_client import get_client
from typing import Literal
from datetime import datetime, timezone

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

def check_acestream_health(host: str, port: int) -> Literal["healthy", "unhealthy", "unknown"]:
    """
    Check Acestream engine health using the API endpoint.
    When engine hangs, the API endpoint doesn't respond.
    """
    health_endpoint = "/server/api?api_version=3&method=get_status"
    try:
        url = f"http://{host}:{port}{health_endpoint}"
        response = httpx.get(url, timeout=5)
        if response.status_code == 200:
            # Try to parse response to ensure it's valid
            try:
                data = response.json()
                # If we get valid JSON response, consider it healthy
                return "healthy"
            except:
                # If response is not valid JSON, it might be hanging
                return "unhealthy"
        else:
            return "unhealthy"
    except (httpx.RequestError, httpx.TimeoutException):
        return "unhealthy"
    except Exception:
        return "unknown"

def sweep_idle():
    return {"ok": True}
