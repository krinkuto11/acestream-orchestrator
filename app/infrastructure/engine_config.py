"""
Global Engine Configuration Management.

Provides a single engine customization model that is applied on top of
orchestrator-managed port bindings and runtime networking.
"""

import json
import logging
import platform
from pathlib import Path
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator

logger = logging.getLogger(__name__)

# Legacy file constants retained only for one-time fallback migration compatibility.
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "engine_config.json"
LEGACY_CONFIG_PATH = Path(__file__).parent.parent / "config" / "custom_engine_variant.json"
DEFAULT_TORRENT_FOLDER_PATH = "/dev/shm/.ACEStream/collected_torrent_files"

RESTRICTED_FLAGS = {
    "--port",
    "--http-port",
    "--https-port",
    "--api-port",
    "--bind-all",
}

MANDATORY_ENGINE_FLAGS = (
    "--disable-sentry",
    "--log-stdout",
    "--disable-upnp",
)

CONTROLLED_ENGINE_FLAGS = {
    "--live-cache-type",
    "--live-mem-cache-size",
    *MANDATORY_ENGINE_FLAGS,
}


class EngineParameter(BaseModel):
    """Advanced CLI parameter for AceStream engine startup."""

    name: str
    type: str = "flag"  # flag|int|float|bytes|str
    value: Any = True
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        normalized = str(value or "").strip()
        if not normalized.startswith("--"):
            raise ValueError("parameter name must start with '--'")
        if normalized in RESTRICTED_FLAGS:
            raise ValueError(f"parameter '{normalized}' is restricted")
        return normalized


class EngineConfig(BaseModel):
    """Single global engine customization model."""

    total_max_download_rate: int = 0
    total_max_upload_rate: int = 0
    live_cache_type: str = "memory"  # memory|disk
    buffer_time: int = 10

    memory_limit: Optional[str] = None
    parameters: List[EngineParameter] = Field(default_factory=list)

    torrent_folder_mount_enabled: bool = False
    torrent_folder_host_path: Optional[str] = None
    torrent_folder_container_path: Optional[str] = None

    disk_cache_mount_enabled: bool = False
    disk_cache_prune_enabled: bool = False
    disk_cache_prune_interval: int = 1440

    @field_validator("live_cache_type")
    @classmethod
    def validate_cache_type(cls, value: str) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in {"memory", "disk"}:
            raise ValueError("live_cache_type must be memory or disk")
        return normalized


def detect_platform() -> str:
    """Detect current architecture as amd64/arm32/arm64."""
    machine = platform.machine().lower()
    if machine in {"x86_64", "amd64", "x64"} or "x86_64" in machine:
        return "amd64"
    if machine in {"aarch64", "arm64", "armv8", "armv8l"} or "aarch64" in machine or "arm64" in machine:
        return "arm64"
    if machine.startswith("arm") or "arm" in machine:
        if "64" in machine or "v8" in machine:
            return "arm64"
        return "arm32"
    return "amd64"


def resolve_engine_image(platform_arch: Optional[str] = None) -> str:
    """Resolve image tag from architecture."""
    arch = platform_arch or detect_platform()
    if arch == "arm32":
        return "ghcr.io/krinkuto11/acestream:latest-arm32"
    if arch == "arm64":
        return "ghcr.io/krinkuto11/acestream:latest-arm64"
    return "ghcr.io/krinkuto11/acestream:latest-amd64"


def create_default_config(config_path: Path = DEFAULT_CONFIG_PATH) -> EngineConfig:
    del config_path
    config = EngineConfig()
    save_config(config)
    return config


def _normalize_legacy_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Translate legacy custom variant payload into global engine config schema."""
    if not isinstance(data, dict):
        return {}

    # Support both old names for migration and new names
    down_rate = data.get("total_max_download_rate")
    if down_rate is None:
        down_rate = data.get("download_limit")
    
    up_rate = data.get("total_max_upload_rate")
    if up_rate is None:
        up_rate = data.get("upload_limit")

    normalized: Dict[str, Any] = {
        "total_max_download_rate": int(down_rate or 0),
        "total_max_upload_rate": int(up_rate or 0),
        "live_cache_type": str(data.get("live_cache_type") or "memory"),
        "buffer_time": int(data.get("buffer_time") or 10),
        "memory_limit": data.get("memory_limit"),
        "parameters": data.get("parameters") or [],
        "torrent_folder_mount_enabled": bool(data.get("torrent_folder_mount_enabled", False)),
        "torrent_folder_host_path": data.get("torrent_folder_host_path"),
        "torrent_folder_container_path": data.get("torrent_folder_container_path"),
        "disk_cache_mount_enabled": bool(data.get("disk_cache_mount_enabled", False)),
        "disk_cache_prune_enabled": bool(data.get("disk_cache_prune_enabled", False)),
        "disk_cache_prune_interval": int(data.get("disk_cache_prune_interval") or 1440),
    }

    return normalized


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> Optional[EngineConfig]:
    """Load engine config from DB-backed runtime settings cache."""
    del config_path
    from ..persistence.settings_persistence import SettingsPersistence

    persisted = SettingsPersistence.load_engine_config()
    if persisted:
        try:
            # Re-normalize to handle field rename in persisted data
            normalized = _normalize_legacy_payload(persisted)
            return EngineConfig(**normalized)
        except Exception as exc:
            logger.error("Failed to parse persisted engine config: %s", exc)

    if LEGACY_CONFIG_PATH.exists():
        try:
            with open(LEGACY_CONFIG_PATH, "r", encoding="utf-8") as handle:
                legacy_data = json.load(handle)
            migrated = EngineConfig(**_normalize_legacy_payload(legacy_data))
            save_config(migrated)
            logger.info("Migrated legacy custom engine config into runtime settings database")
            return migrated
        except Exception as exc:
            logger.error("Failed to migrate legacy custom engine config: %s", exc)

    return create_default_config()


def save_config(config: EngineConfig, config_path: Path = DEFAULT_CONFIG_PATH) -> bool:
    del config_path
    from ..persistence.settings_persistence import SettingsPersistence

    try:
        if not SettingsPersistence.save_engine_config(config.model_dump(mode="json")):
            return False

        global _config_instance
        _config_instance = config
        return True
    except Exception as exc:
        logger.error("Failed to save engine config: %s", exc)
        return False


def _parameter_to_cli_tokens(parameter: EngineParameter) -> List[str]:
    if not parameter.enabled:
        return []

    if parameter.name in RESTRICTED_FLAGS:
        # Defensive fallback in case validation is bypassed.
        return []

    ptype = str(parameter.type or "flag").strip().lower()
    if ptype == "flag":
        return [parameter.name] if bool(parameter.value) else []

    if ptype in {"int", "bytes"}:
        return [parameter.name, str(int(parameter.value))]

    if ptype == "float":
        return [parameter.name, str(float(parameter.value))]

    # default string
    value = str(parameter.value or "").strip()
    if not value:
        return []
    return [parameter.name, value]


def build_engine_customization_args(config: EngineConfig) -> List[str]:
    """Build sanitized customization args (never includes orchestrator-owned ports)."""
    args: List[str] = [
        "--total-max-download-rate", str(int(config.total_max_download_rate)),
        "--total-max-upload-rate", str(int(config.total_max_upload_rate)),
        "--live-buffer", str(int(config.buffer_time)),
    ]

    if config.live_cache_type == "disk":
        args.extend(["--live-cache-type", "disk", "--cache-dir", "/dev/shm/.ACEStream"])
    else:
        args.extend(["--live-cache-type", "memory", "--live-mem-cache-size", "104857600"])

    for parameter in config.parameters:
        try:
            if parameter.name in CONTROLLED_ENGINE_FLAGS:
                logger.warning("Ignoring advanced parameter %s because it is orchestrator-controlled", parameter.name)
                continue
            args.extend(_parameter_to_cli_tokens(parameter))
        except Exception as exc:
            logger.warning("Skipping invalid advanced parameter %s: %s", parameter.name, exc)

    for flag in MANDATORY_ENGINE_FLAGS:
        args.append(flag)

    return args


_config_instance: Optional[EngineConfig] = None


def get_config() -> Optional[EngineConfig]:
    global _config_instance
    if _config_instance is None:
        _config_instance = load_config()
    return _config_instance


def reload_config() -> Optional[EngineConfig]:
    global _config_instance
    _config_instance = load_config()
    return _config_instance
