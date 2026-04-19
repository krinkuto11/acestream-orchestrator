from typing import Optional

PROXY_MODE_HTTP = "http"
PROXY_MODE_API = "api"

_PROXY_MODE_ALIASES = {
    "legacy_http": PROXY_MODE_HTTP,
    "legacy-api": PROXY_MODE_API,
    "legacy_http_mode": PROXY_MODE_HTTP,
    "legacy_api": PROXY_MODE_API,
    "http": PROXY_MODE_HTTP,
    "api": PROXY_MODE_API,
    "legacyhttp": PROXY_MODE_HTTP,
    "legacyapi": PROXY_MODE_API,
}


def normalize_proxy_mode(value: Optional[str], default: Optional[str] = PROXY_MODE_HTTP) -> Optional[str]:
    """Normalize proxy control mode to canonical lowercase values."""
    text = str(value or "").strip().lower()
    if not text:
        return default
    compact = text.replace(" ", "_").replace("-", "_")
    mapped = _PROXY_MODE_ALIASES.get(compact)
    if mapped:
        return mapped
    return default


def proxy_mode_label(value: Optional[str]) -> str:
    """Human-readable control mode label for UI/event payloads."""
    mode = normalize_proxy_mode(value)
    if mode == PROXY_MODE_API:
        return "API"
    return "HTTP"
