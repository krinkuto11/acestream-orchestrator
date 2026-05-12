"""Minimal FastAPI sidecar for Proton VPN server updates.

This is the only Python code kept after the Go migration. It exposes a single
endpoint that triggers a ProtonServerUpdater.update() call.  All other
management logic lives in the Go services.
"""
from __future__ import annotations

import logging
import os
import sys

# Ensure the repo root is on the path so app.vpn.proton_updater is importable.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app.vpn.proton_updater import ProtonServerUpdater, ProtonFilterConfig

logger = logging.getLogger(__name__)

app = FastAPI(title="Proton Sidecar", docs_url=None, redoc_url=None)

_updater = ProtonServerUpdater(
    storage_path=os.getenv("PROTON_STORAGE_PATH", "/data/proton")
)


class RefreshRequest(BaseModel):
    proton_username: Optional[str] = None
    proton_password: Optional[str] = None
    proton_totp_secret: Optional[str] = None
    gluetun_json_mode: str = "update"
    filters: Optional[Dict[str, Any]] = None


@app.post("/refresh")
async def refresh_servers(body: RefreshRequest = RefreshRequest()):
    """Fetch Proton servers and update local Gluetun JSON files."""
    try:
        filters = None
        if body.filters:
            filters = ProtonFilterConfig(**body.filters)

        result = await _updater.update(
            proton_username=body.proton_username,
            proton_password=body.proton_password,
            proton_totp_secret=body.proton_totp_secret,
            gluetun_json_mode=body.gluetun_json_mode,
            filters=filters,
        )
        return {"status": "ok", "result": result}
    except Exception as exc:
        logger.error("Proton refresh failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/health")
async def health():
    return {"status": "ok", "service": "proton-sidecar"}
