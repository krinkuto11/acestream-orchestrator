"""
Backward-compatible compatibility layer.

The codebase has moved to a single global engine customization model in
app.services.engine_config. This module remains as a thin shim so older
imports continue to work while callers migrate.
"""

from typing import Optional

from .engine_config import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_TORRENT_FOLDER_PATH,
    EngineConfig,
    EngineParameter,
    build_engine_customization_args,
    detect_platform,
    get_config,
    load_config,
    reload_config,
    resolve_engine_image,
    save_config,
)

CustomVariantConfig = EngineConfig
CustomVariantParameter = EngineParameter


def validate_config(config: CustomVariantConfig) -> tuple[bool, Optional[str]]:
    try:
        CustomVariantConfig(**config.model_dump(mode="json"))
        return True, None
    except Exception as exc:
        return False, str(exc)


def build_variant_config_from_custom(config: CustomVariantConfig):
    return {
        "image": resolve_engine_image(detect_platform()),
        "config_type": "cmd",
        "is_custom": True,
        "base_cmd": ["python", "main.py", *build_engine_customization_args(config)],
    }


def is_custom_variant_enabled() -> bool:
    # Global engine customization is always active.
    return bool(get_config())
