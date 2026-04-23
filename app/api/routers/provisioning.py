"""Provisioning and scaling endpoints."""
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException

from ...core.config import cfg
from ...api.auth import require_api_key
from ...services.state import state
from ...control_plane.autoscaler import ensure_minimum, scale_to
from ...control_plane.health import sweep_idle
from ...control_plane.provisioner import (
    StartRequest,
    start_container,
    stop_container,
    AceProvisionRequest,
    AceProvisionResponse,
    start_acestream,
    compute_current_engine_config_hash,
)
from ...observability.event_logger import event_logger
from ...persistence.cache import invalidate_cache
from ...persistence.reindex import reindex_existing
from ...control_plane.autoscaler import engine_controller

logger = logging.getLogger(__name__)

router = APIRouter()


def _trigger_engine_generation_rollout(reason: str) -> Dict[str, Any]:
    """Update target engine config generation and request reconciliation."""
    target_hash = compute_current_engine_config_hash()
    result = state.set_target_engine_config(target_hash)
    if result.get("changed"):
        logger.info(
            f"Engine target config updated ({reason}): hash={result['config_hash']} generation={result['generation']}"
        )
        engine_controller.request_reconcile(reason=f"config_rollout:{reason}")
    return result


def _mark_engines_draining_for_reprovision(reason: str = "engine_settings_reprovision") -> int:
    """Mark managed engines as draining so they are replaced during reconcile."""
    marked = 0

    for engine_state in state.list_engines():
        labels = getattr(engine_state, "labels", None)
        if isinstance(labels, dict) and str(labels.get("manual") or "").strip().lower() == "true":
            continue

        container_id = str(getattr(engine_state, "container_id", "") or "").strip()
        if not container_id:
            continue

        if state.mark_engine_draining(container_id, reason=reason):
            marked += 1

    return marked


@router.post("/provision", dependencies=[Depends(require_api_key)])
def provision(req: StartRequest):
    result = start_container(req)
    event_logger.log_event(
        event_type="engine",
        category="created",
        message=f"Engine provisioned: {result.get('container_id', 'unknown')[:12]}",
        details={"image": req.image, "labels": req.labels or {}},
        container_id=result.get("container_id"),
    )
    return result


@router.post("/provision/acestream", response_model=AceProvisionResponse, dependencies=[Depends(require_api_key)])
def provision_acestream(req: AceProvisionRequest):
    from ...control_plane.circuit_breaker import circuit_breaker_manager
    from ...vpn.gluetun import get_vpn_status

    vpn_status_check = get_vpn_status()
    circuit_breaker_status = circuit_breaker_manager.get_status()

    if vpn_status_check.get("enabled", False) and not vpn_status_check.get("connected", False):
        logger.error("Provisioning blocked: VPN not connected")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "provisioning_blocked",
                "code": "vpn_disconnected",
                "message": "VPN connection is required but currently disconnected",
                "recovery_eta_seconds": 60,
                "can_retry": True,
                "should_wait": True,
            },
        )

    if circuit_breaker_status.get("general", {}).get("state") != "closed":
        cb_state = circuit_breaker_status.get("general", {}).get("state")
        recovery_timeout = circuit_breaker_status.get("general", {}).get("recovery_timeout", 300)
        last_failure = circuit_breaker_status.get("general", {}).get("last_failure_time")

        recovery_eta = recovery_timeout
        if last_failure:
            try:
                last_failure_dt = datetime.fromisoformat(last_failure.replace("Z", "+00:00"))
                elapsed = (datetime.now(timezone.utc) - last_failure_dt).total_seconds()
                recovery_eta = max(0, int(recovery_timeout - elapsed))
            except Exception:
                pass

        logger.error(f"Provisioning blocked: Circuit breaker is {cb_state}")
        raise HTTPException(
            status_code=503,
            detail={
                "error": "provisioning_blocked",
                "code": "circuit_breaker",
                "message": f"Circuit breaker is {cb_state} due to repeated failures",
                "recovery_eta_seconds": recovery_eta,
                "can_retry": cb_state == "half_open",
                "should_wait": True,
            },
        )

    try:
        response = start_acestream(req)
    except RuntimeError as e:
        error_msg = str(e)
        if "Gluetun" in error_msg or "VPN" in error_msg:
            logger.error(f"Provisioning failed due to VPN issue: {error_msg}")
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "provisioning_failed",
                    "code": "vpn_error",
                    "message": f"VPN error during provisioning: {error_msg}",
                    "recovery_eta_seconds": 60,
                    "can_retry": True,
                    "should_wait": True,
                },
            )
        elif "circuit breaker" in error_msg.lower():
            logger.error(f"Provisioning failed due to circuit breaker: {error_msg}")
            raise HTTPException(
                status_code=503,
                detail={
                    "error": "provisioning_blocked",
                    "code": "circuit_breaker",
                    "message": error_msg,
                    "recovery_eta_seconds": 300,
                    "can_retry": False,
                    "should_wait": True,
                },
            )
        else:
            logger.error(f"Provisioning failed: {error_msg}")
            raise HTTPException(
                status_code=500,
                detail={
                    "error": "provisioning_failed",
                    "code": "general_error",
                    "message": f"Failed to provision engine: {error_msg}",
                    "recovery_eta_seconds": None,
                    "can_retry": False,
                    "should_wait": False,
                },
            )

    try:
        reindex_existing()
        logger.info(f"Reindexed after provisioning engine {response.container_id[:12]}")
    except Exception as e:
        logger.error(f"Failed to reindex after provisioning: {e}")

    invalidate_cache("orchestrator:status")

    event_logger.log_event(
        event_type="engine",
        category="created",
        message=f"AceStream engine provisioned on port {response.host_http_port}",
        details={
            "image": req.image or "default",
            "host_http_port": response.host_http_port,
            "container_http_port": response.container_http_port,
            "labels": req.labels or {},
        },
        container_id=response.container_id,
    )

    return response


@router.post("/scale/{demand}", dependencies=[Depends(require_api_key)])
def scale(demand: int):
    scale_to(demand)
    return {"scaled_to": demand}


@router.post("/gc", dependencies=[Depends(require_api_key)])
def garbage_collect():
    sweep_idle()
    return {"status": "ok"}


@router.get("/by-label", dependencies=[Depends(require_api_key)])
def by_label(key: str, value: str):
    from ...infrastructure.inspect import inspect_container
    from ...control_plane.health import list_managed

    res = []
    for c in list_managed():
        if (c.labels or {}).get(key) == value:
            try:
                res.append(inspect_container(c.id))
            except Exception:
                continue
    return res


@router.get("/custom-variant/platform")
def get_platform_info():
    from ...infrastructure.engine_config import detect_platform

    return {
        "platform": detect_platform(),
        "supported_platforms": ["amd64", "arm32", "arm64"],
    }


@router.get("/custom-variant/reprovision/status")
def get_reprovision_status():
    """Compute declarative rollout status from desired-vs-actual engine hashes."""
    target = state.get_target_engine_config()
    target_hash = str(target.get("config_hash") or "")
    desired = max(0, int(state.get_desired_replica_count()))
    engines = state.list_engines()

    engines_with_target_hash = sum(
        1
        for engine in engines
        if str((engine.labels or {}).get("acestream.config_hash") or "") == target_hash
    )

    actual = len(engines)
    outdated_running = max(0, actual - engines_with_target_hash)
    in_progress = engines_with_target_hash < desired or actual > desired

    if in_progress and actual > desired:
        current_phase = "stopping"
    elif in_progress:
        current_phase = "provisioning"
    else:
        current_phase = "complete"

    return {
        "in_progress": in_progress,
        "status": "in_progress" if in_progress else "idle",
        "message": "Rolling update in progress" if in_progress else "No rollout in progress",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "total_engines": desired,
        "engines_stopped": outdated_running,
        "engines_provisioned": engines_with_target_hash,
        "current_engine_id": None,
        "current_phase": current_phase,
        "target_generation": target.get("generation"),
        "target_hash": target_hash,
    }


@router.post("/custom-variant/reprovision", dependencies=[Depends(require_api_key)])
async def reprovision_all_engines():
    """Trigger a declarative rolling update by bumping target engine config generation."""
    marked = _mark_engines_draining_for_reprovision(reason="engine_settings_reprovision")
    if marked > 0:
        engine_controller.request_reconcile(reason="engine_settings_reprovision")

    rollout = _trigger_engine_generation_rollout(reason="custom_variant_reprovision")
    changed = bool(rollout.get("changed"))

    return {
        "message": "Rolling update scheduled" if changed else "No config change detected; rollout not required",
        "reprovision_marked_engines": marked,
        "rolling_update": {
            "changed": changed,
            "target_generation": rollout.get("generation"),
            "target_hash": rollout.get("config_hash"),
        },
    }


@router.get("/settings/engine/reprovision/status")
def get_engine_reprovision_status():
    return get_reprovision_status()


@router.post("/settings/engine/reprovision", dependencies=[Depends(require_api_key)])
async def reprovision_all_engines_v2():
    return await reprovision_all_engines()
