"""Legacy JSON settings migrator into database-backed runtime settings."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .db import get_session
from .settings_persistence import SettingsPersistence
from ..models.db_models import RuntimeSettingsRow, VPNCredentialRow

logger = logging.getLogger(__name__)

APP_CONFIG_DIR = Path(__file__).parent.parent / "config"
LEGACY_ARCHIVE_DIR = Path(__file__).parent.parent.parent / "old_json"


def _select_existing_path(filename: str) -> Optional[Path]:
    candidates = [APP_CONFIG_DIR / filename, LEGACY_ARCHIVE_DIR / filename]
    existing = [path for path in candidates if path.exists() and path.is_file()]
    if not existing:
        return None

    # Prefer the most recently updated file when multiple copies exist.
    return max(existing, key=lambda item: item.stat().st_mtime)


def _rename_migrated(path: Path) -> None:
    target = path.with_name(f"{path.name}.migrated")
    if target.exists():
        target = path.with_name(f"{path.name}.migrated.{int(path.stat().st_mtime)}")
    path.rename(target)


def _load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def _is_db_effectively_empty() -> bool:
    with get_session() as session:
        row = session.get(RuntimeSettingsRow, SettingsPersistence.SETTINGS_ROW_ID)
        if row is None:
            return True

        defaults = {
            "engine_config": SettingsPersistence._default_engine_config(),
            "engine_settings": SettingsPersistence._default_engine_settings(),
            "orchestrator_settings": SettingsPersistence._default_orchestrator_settings(),
            "proxy_settings": SettingsPersistence._default_proxy_settings(),
            "vpn_settings": SettingsPersistence._default_vpn_settings(),
            "loop_detection_settings": SettingsPersistence._default_loop_detection_settings(),
        }

        row_vpn = SettingsPersistence.normalize_vpn_config(dict(row.vpn_settings or {}))
        default_vpn = SettingsPersistence.normalize_vpn_config(dict(defaults["vpn_settings"]))

        has_vpn_credentials = (
            session.query(VPNCredentialRow)
            .filter(VPNCredentialRow.settings_id == row.id)
            .first()
            is not None
        )

        is_default = (
            dict(row.engine_config or {}) == defaults["engine_config"]
            and dict(row.engine_settings or {}) == defaults["engine_settings"]
            and dict(row.orchestrator_settings or {}) == defaults["orchestrator_settings"]
            and dict(row.proxy_settings or {}) == defaults["proxy_settings"]
            and row_vpn == default_vpn
            and dict(row.loop_detection_settings or {}) == defaults["loop_detection_settings"]
            and not has_vpn_credentials
        )
        return is_default


def _runtime_settings_row_exists() -> bool:
    with get_session() as session:
        row = session.get(RuntimeSettingsRow, SettingsPersistence.SETTINGS_ROW_ID)
        return row is not None


def migrate_legacy_json_configs() -> Dict[str, Any]:
    """Migrate legacy settings JSON files into the runtime settings database."""
    mapping: Dict[str, Tuple[str, Callable[[Dict[str, Any]], bool]]] = {
        "engine_config": ("engine_config.json", SettingsPersistence.save_engine_config),
        "engine_settings": ("engine_settings.json", SettingsPersistence.save_engine_settings),
        "orchestrator_settings": ("orchestrator_settings.json", SettingsPersistence.save_orchestrator_config),
        "proxy_settings": ("proxy_settings.json", SettingsPersistence.save_proxy_config),
        "vpn_settings": ("vpn_settings.json", SettingsPersistence.save_vpn_config),
    }

    result: Dict[str, Any] = {
        "db_was_empty": False,
        "runtime_settings_row_existed": False,
        "migrated": {},
        "renamed_files": [],
        "seeded_defaults": False,
        "errors": [],
    }

    row_existed_before = _runtime_settings_row_exists()
    result["runtime_settings_row_existed"] = row_existed_before

    db_empty = _is_db_effectively_empty()
    result["db_was_empty"] = db_empty

    if not db_empty:
        logger.info("Runtime settings already populated in DB; skipping legacy JSON migration")
        SettingsPersistence.initialize_cache(force_reload=True)
        return result

    migrated_any = False

    for key, (filename, save_fn) in mapping.items():
        path = _select_existing_path(filename)
        if not path:
            result["migrated"][key] = False
            continue

        try:
            payload = _load_json(path)
            if not save_fn(payload):
                raise RuntimeError(f"failed persisting {key} payload")

            _rename_migrated(path)
            result["migrated"][key] = True
            result["renamed_files"].append(str(path))
            migrated_any = True
            logger.info("Migrated legacy settings file: %s", path)
        except Exception as exc:
            logger.error("Failed migrating %s from %s: %s", key, path, exc)
            result["migrated"][key] = False
            result["errors"].append({"file": str(path), "error": str(exc)})

    if not migrated_any:
        # DB is effectively empty and no files existed. Only treat this as
        # default seeding when the runtime settings row did not exist yet.
        SettingsPersistence.initialize_cache(force_reload=True)
        result["seeded_defaults"] = not row_existed_before
    else:
        SettingsPersistence.initialize_cache(force_reload=True)

    return result
