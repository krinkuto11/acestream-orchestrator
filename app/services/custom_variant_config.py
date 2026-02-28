"""
Custom Engine Variant Configuration Management

Manages user-defined custom engine variants with configurable parameters for AceServe.
"""

import json
import logging
import platform
import re
from pathlib import Path
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)

# Default config file path - use relative path from this file
DEFAULT_CONFIG_PATH = Path(__file__).parent.parent / "config" / "custom_engine_variant.json"

class CustomVariantConfig(BaseModel):
    """Simplified Custom Engine Configuration"""
    enabled: bool = False
    name: str = "Custom Engine"
    icon: str = "server"
    
    # User facing parameters
    p2p_port: Optional[int] = 8621
    http_port: Optional[int] = 6878
    download_limit: int = 0
    upload_limit: int = 0
    live_cache_type: str = "disk"  # "memory" or "disk"
    buffer_time: int = 10
    stats_interval: int = 1
    
    @validator('live_cache_type')
    def validate_cache_type(cls, v):
        if v not in ['memory', 'disk']:
            raise ValueError('live_cache_type must be memory or disk')
        return v


def detect_platform() -> str:
    """
    Detect the current platform architecture.
    
    Returns:
        str: "amd64", "arm32", or "arm64"
    """
    machine = platform.machine().lower()
    if machine in ['x86_64', 'amd64', 'x64'] or 'x86_64' in machine:
        return 'amd64'
    if machine in ['aarch64', 'arm64', 'armv8', 'armv8l'] or 'aarch64' in machine or 'arm64' in machine:
        return 'arm64'
    if machine.startswith('arm') or 'arm' in machine:
        if '64' in machine or 'v8' in machine:
            return 'arm64'
        else:
            return 'arm32'
    return 'amd64'


def create_default_config(config_path: Path = DEFAULT_CONFIG_PATH) -> CustomVariantConfig:
    config = CustomVariantConfig()
    save_config(config, config_path)
    return config


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> Optional[CustomVariantConfig]:
    if not config_path.exists():
        return create_default_config(config_path)
    
    try:
        with open(config_path, 'r') as f:
            data = json.load(f)
        config = CustomVariantConfig(**data)
        return config
    except Exception as e:
        logger.error(f"Failed to load custom variant config: {e}")
        return None


def save_config(config: CustomVariantConfig, config_path: Path = DEFAULT_CONFIG_PATH) -> bool:
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        with open(config_path, 'w') as f:
            json.dump(config.dict(), f, indent=2)
            
        global _config_instance
        _config_instance = config
        return True
    except Exception as e:
        logger.error(f"Failed to save custom variant config: {e}")
        return False


def build_variant_config_from_custom(config: CustomVariantConfig) -> Dict[str, Any]:
    """
    Build a variant configuration dict compatible with provisioner.py
    from custom variant config.
    """
    platform_arch = detect_platform()
    
    # Always use AceServe Base Images depending on platform
    if platform_arch == 'amd64':
        image = "ghcr.io/krinkuto11/acestream:latest-amd64"
    elif platform_arch == 'arm32':
        image = "jopsis/acestream:arm32-v3.2.14"
    elif platform_arch == 'arm64':
        image = "jopsis/acestream:arm64-v3.2.14"
    else:
        image = "ghcr.io/krinkuto11/acestream:latest-amd64"
        
    result = {
        "image": image,
        "config_type": "cmd",
        "is_custom": True
    }
    
    # Base command depending on architecture
    if platform_arch == 'amd64':
        cmd = ["/acestream/acestreamengine"]
    else:
        cmd = ["python", "main.py"]
        
    # Append requested parameters
    if config.p2p_port:
        cmd.extend(["--port", str(config.p2p_port)])
    if config.http_port:
        cmd.extend(["--http-port", str(config.http_port)])
        cmd.extend(["--https-port", str(config.http_port + 1)])  # Ensure https pairs nicely if used
        
    cmd.extend(["--download-limit", str(config.download_limit)])
    cmd.extend(["--upload-limit", str(config.upload_limit)])
    
    if config.live_cache_type == "disk":
        cmd.extend(["--live-cache-type", "disk"])
        # Disk specific optimizations the user wants in default jopsis
        cmd.extend(["--cache-dir", "/root/.ACEStream"])
    else:
        cmd.extend(["--live-cache-type", "memory"])
        
    cmd.extend(["--live-buffer-time", str(config.buffer_time)])
    cmd.extend(["--stats-report-interval", str(config.stats_interval)])
    
    result["base_cmd"] = cmd
    return result


# Global config instance
_config_instance: Optional[CustomVariantConfig] = None

def get_config() -> Optional[CustomVariantConfig]:
    global _config_instance
    if _config_instance is None:
        _config_instance = load_config()
    return _config_instance

def reload_config() -> Optional[CustomVariantConfig]:
    global _config_instance
    _config_instance = load_config()
    return _config_instance

def is_custom_variant_enabled() -> bool:
    config = get_config()
    return config is not None and config.enabled
