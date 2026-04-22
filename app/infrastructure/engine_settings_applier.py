"""
Push EngineConfig settings to running AceStream containers via their REST API.

Token is retrieved via docker exec reading /acestream/engine_runtime.json.
Only fields that the engine accepts without a restart are pushed.
"""

import json
import logging
import threading
import time
from typing import Dict, Optional

import httpx

logger = logging.getLogger(__name__)

# Fields in EngineConfig that the AceStream REST API can apply without restarting.
LIVE_SETTABLE_FIELDS = frozenset({
    "total_max_upload_rate",
    "total_max_download_rate",
    "buffer_time",
    "live_cache_type",
})

# Fields that require a container restart (CLI args or Docker-level config).
RESTART_REQUIRED_FIELDS = frozenset({
    "memory_limit",
    "parameters",
    "torrent_folder_mount_enabled",
    "torrent_folder_host_path",
    "torrent_folder_container_path",
    "disk_cache_mount_enabled",
    "disk_cache_prune_enabled",
    "disk_cache_prune_interval",
})


def get_engine_token(container_name: str) -> Optional[str]:
    """Read access_token from /acestream/engine_runtime.json via docker exec."""
    from .docker_client import get_client

    try:
        cli = get_client()
        container = cli.containers.get(container_name)
        result = container.exec_run(
            ["cat", "/acestream/engine_runtime.json"],
            stdout=True,
            stderr=False,
        )
        if result.exit_code != 0:
            logger.debug("exec_run exit %d for %s", result.exit_code, container_name)
            return None
        data = json.loads(result.output.decode("utf-8"))
        return data.get("access_token")
    except Exception as exc:
        logger.debug("Token read failed for %s: %s", container_name, exc)
        return None


def _build_payload(engine_config) -> dict:
    """Map EngineConfig → AceStream REST API fields (live-settable only)."""
    return {
        "upload_limit": int(engine_config.total_max_upload_rate),
        "download_limit": int(engine_config.total_max_download_rate),
        "live_buffer": int(engine_config.buffer_time),
        "live_cache_type": str(engine_config.live_cache_type),
    }


def apply_settings_to_engine(
    container_name: str,
    host: str,
    port: int,
    engine_config,
    quiet: bool = False,
) -> bool:
    """PATCH /api/v1/settings on a single running engine. Returns True on success."""
    token = get_engine_token(container_name)
    if not token:
        logger.warning("settings push skipped for %s: no token", container_name)
        return False

    payload = _build_payload(engine_config)
    url = f"http://{host}:{port}/api/v1/settings"
    try:
        resp = httpx.patch(url, json=payload, headers={"x-api-key": token}, timeout=5.0)
        if resp.status_code in (200, 204):
            log_msg = "settings applied to %s (upload=%s download=%s buffer=%s cache=%s)"
            log_args = (
                container_name,
                payload["upload_limit"],
                payload["download_limit"],
                payload["live_buffer"],
                payload["live_cache_type"],
            )
            if quiet:
                logger.debug(log_msg, *log_args)
            else:
                logger.info(log_msg, *log_args)
            return True
        logger.warning(
            "settings push to %s returned HTTP %d: %s",
            container_name, resp.status_code, resp.text[:200],
        )
        return False
    except Exception as exc:
        logger.warning("settings push to %s failed: %s", container_name, exc)
        return False


def apply_settings_to_all_engines(engine_config) -> Dict[str, bool]:
    """Push settings to every healthy managed engine. Returns {container_name: success}."""
    from ..services.state import state

    results: Dict[str, bool] = {}
    for engine in state.list_engines():
        if engine.health_status != "healthy":
            continue
        labels = engine.labels or {}
        if str(labels.get("manual", "")).lower() == "true":
            continue
        name = engine.container_name or engine.container_id
        results[name] = apply_settings_to_engine(
            container_name=name,
            host=engine.host,
            port=engine.port,
            engine_config=engine_config,
            quiet=True,
        )

    # Log consolidated summary
    successes = [n for n, ok in results.items() if ok]
    failures = [n for n, ok in results.items() if not ok]

    if successes or failures:
        payload = _build_payload(engine_config)
        summary = f"settings applied to {len(successes)}/{len(results)} engines"
        if failures:
            summary += f" (failures: {', '.join(failures)})"

        logger.info(
            "%s (upload=%s download=%s buffer=%s cache=%s)",
            summary,
            payload["upload_limit"],
            payload["download_limit"],
            payload["live_buffer"],
            payload["live_cache_type"],
        )

    return results


def schedule_post_start_settings(
    container_name: str,
    engine_config,
    *,
    poll_interval: float = 2.0,
    timeout: float = 120.0,
) -> None:
    """
    Spawn a daemon thread that waits for the engine to become healthy in state
    then pushes the current settings once.
    """
    def _worker():
        from ..services.state import state

        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for engine in state.list_engines():
                name = engine.container_name or engine.container_id
                if name != container_name:
                    continue
                if engine.health_status == "healthy":
                    apply_settings_to_engine(
                        container_name=container_name,
                        host=engine.host,
                        port=engine.port,
                        engine_config=engine_config,
                    )
                    return
                break  # found engine but not healthy yet
            time.sleep(poll_interval)
        logger.warning("post-start settings push timed out for %s", container_name)

    threading.Thread(
        target=_worker,
        name=f"settings-push-{container_name}",
        daemon=True,
    ).start()
