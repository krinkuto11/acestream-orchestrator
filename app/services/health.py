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

def check_engine_network_connection(host: str, port: int) -> bool:
    """
    Check if the engine has a working network connection by querying the network status endpoint.
    This is used to double-check VPN connectivity when Gluetun container health appears unhealthy.
    
    Returns True if the engine reports connected=true, False otherwise.
    """
    network_endpoint = "/server/api?api_version=3&method=get_network_connection_status"
    try:
        url = f"http://{host}:{port}{network_endpoint}"
        response = httpx.get(url, timeout=5)
        if response.status_code == 200:
            try:
                data = response.json()
                result = data.get("result", {})
                return result.get("connected", False) is True
            except:
                return False
        else:
            return False
    except (httpx.RequestError, httpx.TimeoutException):
        return False
    except Exception:
        return False

def sweep_idle():
    return {"ok": True}
