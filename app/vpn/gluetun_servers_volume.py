"""
gluetun_servers_volume.py
~~~~~~~~~~~~~~~~~~~~~~~~~
Maintains a named Docker volume (``acestream-gluetun-servers``) whose sole
purpose is sharing the orchestrator's refreshed ``servers.json`` catalog with
Gluetun sibling containers.

Why a named volume instead of a host bind-mount
------------------------------------------------
The orchestrator runs *inside* Docker and creates Gluetun containers via the
Docker socket (sibling containers).  Bind-mounting a file from inside the
orchestrator container would require knowing the *host* path of that file —
which is unknowable without privileged ``docker inspect`` tricks.

Named volumes are managed entirely by the Docker daemon: both the orchestrator
helper container and every Gluetun container can mount them by name, with no
host path involved.

Usage
-----
Call ``gluetun_servers_volume.sync()`` after every servers.json write.
The function is idempotent and safe to call from any async context via
``asyncio.to_thread``.

Gluetun containers should be given::

    volumes={"acestream-gluetun-servers": {"bind": "/gluetun", "mode": "ro"}}

so Gluetun finds ``/gluetun/servers.json`` on startup.
"""

from __future__ import annotations

import io
import logging
import tarfile
from contextlib import suppress
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VOLUME_NAME = "acestream-gluetun-servers"

# Using the core Gluetun image as our helper.  We know it's already on the host
# because the orchestrator is currently managing Gluetun containers.
# This avoids dependencies on external images (like busybox) that might
# not be present in restricted or offline environments.
# We only use it to host the mount while calling put_archive.
_HELPER_IMAGE = "qmcgaw/gluetun"


def _get_client():
    from ..infrastructure.docker_client import get_client
    return get_client(timeout=30)


import docker

def _ensure_volume() -> None:
    """Create the named volume if it does not already exist."""
    cli = _get_client()
    try:
        cli.volumes.get(VOLUME_NAME)
    except docker.errors.NotFound:
        cli.volumes.create(VOLUME_NAME, driver="local")
        logger.info("Created named Docker volume '%s'", VOLUME_NAME)
    finally:
        with suppress(Exception):
            cli.close()


def _make_tar(filename: str, content: bytes) -> bytes:
    """Pack *content* into an in-memory tar archive as *filename*."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo(name=filename)
        info.size = len(content)
        info.mode = 0o644  # rw-r--r--
        tf.addfile(info, io.BytesIO(content))
    return buf.getvalue()


def sync(servers_json_path: Optional[Path] = None) -> bool:
    """Copy servers.json into the shared Gluetun volume.

    Creates the volume if needed, then uses a one-shot helper container +
    ``put_archive`` to write the file — no host paths required.

    Returns True on success, False on any error (non-fatal; Gluetun will fall
    back to its own bundled catalog).
    """
    from .vpn_reputation import vpn_reputation_manager

    path = servers_json_path or vpn_reputation_manager._servers_json_path()
    try:
        content = path.read_bytes()
    except OSError as exc:
        logger.warning("gluetun_servers_volume: cannot read %s: %s", path, exc)
        return False

    tar_data = _make_tar("servers.json", content)

    cli = _get_client()
    container = None
    try:
        _ensure_volume()

        # Ensure the helper image is available locally.
        try:
            cli.images.get(_HELPER_IMAGE)
        except docker.errors.ImageNotFound:
            logger.info("Helper image '%s' not found locally; pulling...", _HELPER_IMAGE)
            cli.images.pull(_HELPER_IMAGE)

        # Create a dormant helper container that mounts the volume.
        # We never start it — put_archive works on created (not running) containers.
        container = cli.containers.create(
            _HELPER_IMAGE,
            volumes={VOLUME_NAME: {"bind": "/gluetun-data", "mode": "rw"}},
        )
        ok = container.put_archive("/gluetun-data", tar_data)
        if ok:
            logger.info(
                "Synced servers.json (%d bytes) into Docker volume '%s'",
                len(content),
                VOLUME_NAME,
            )
            return True
        else:
            logger.warning("gluetun_servers_volume: put_archive reported failure")
            return False

    except Exception as exc:
        logger.warning("Failed to sync servers.json to Docker volume '%s': %s", VOLUME_NAME, exc)
        return False
    finally:
        if container is not None:
            with suppress(Exception):
                container.remove(force=True)
        with suppress(Exception):
            cli.close()
