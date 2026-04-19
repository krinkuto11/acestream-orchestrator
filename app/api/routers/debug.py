"""Debug / meta endpoints: version, auth status, API docs, root redirect, favicons."""
import os

from fastapi import APIRouter, HTTPException
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import FileResponse, RedirectResponse

router = APIRouter()

# Version is set at app creation time and injected via app state,
# but for simplicity we duplicate the string here.  Phase 7 can
# centralise it.
_APP_VERSION = "1.7.3"

_PANEL_DIR = "app/static/panel"


def _serve_favicon(filename: str):
    """Helper function to serve favicon files from panel directory or fallback to source."""
    if os.path.exists(_PANEL_DIR) and os.path.isdir(_PANEL_DIR):
        favicon_path = os.path.join(_PANEL_DIR, filename)
        if os.path.exists(favicon_path):
            return FileResponse(favicon_path)

    panel_react_public = "app/static/panel-react/public"
    if os.path.exists(panel_react_public):
        favicon_path = os.path.join(panel_react_public, filename)
        if os.path.exists(favicon_path):
            return FileResponse(favicon_path)

    raise HTTPException(status_code=404, detail="Favicon not found")


@router.get("/version")
def get_version():
    """Get the current version of the orchestrator."""
    return {
        "version": _APP_VERSION,
        "title": "AceStream Orchestrator",
    }


@router.get("/auth/status")
def get_auth_status():
    """Return whether API-key authentication is currently enforced by the server."""
    from ...core.config import cfg

    return {
        "required": bool(cfg.API_KEY),
        "mode": "bearer" if cfg.API_KEY else "none",
    }


@router.get("/api/v1/docs", include_in_schema=False)
def get_v1_swagger_docs():
    """Serve Swagger UI under the versioned API namespace."""
    # app title is not available here; use a static string.
    return get_swagger_ui_html(
        openapi_url="/api/v1/openapi.json",
        title="On-Demand Orchestrator - Swagger UI (API v1)",
    )


@router.get("/api/v1/redoc", include_in_schema=False)
def get_v1_redoc_docs():
    """Serve ReDoc under the versioned API namespace."""
    return get_redoc_html(
        openapi_url="/api/v1/openapi.json",
        title="On-Demand Orchestrator - ReDoc (API v1)",
    )


@router.get("/favicon.ico")
async def get_favicon_ico():
    """Serve favicon.ico at root level."""
    return _serve_favicon("favicon.ico")


@router.get("/favicon.svg")
async def get_favicon_svg():
    """Serve favicon.svg at root level."""
    return _serve_favicon("favicon.svg")


@router.get("/favicon-96x96.png")
async def get_favicon_96():
    """Serve favicon-96x96.png at root level."""
    return _serve_favicon("favicon-96x96.png")


@router.get("/favicon-96x96-dark.png")
async def get_favicon_96_dark():
    """Serve favicon-96x96-dark.png at root level."""
    return _serve_favicon("favicon-96x96-dark.png")


@router.get("/apple-touch-icon.png")
async def get_apple_touch_icon():
    """Serve apple-touch-icon.png at root level."""
    return _serve_favicon("apple-touch-icon.png")
