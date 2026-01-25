"""
Custom Engine Variant Configuration Management

Manages user-defined custom engine variants with configurable parameters.
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

# Default torrent folder path inside containers
DEFAULT_TORRENT_FOLDER_PATH = "/root/.ACEStream/collected_torrent_files"

# Memory limit constants
MIN_MEMORY_BYTES = 32 * 1024 * 1024  # 32MB minimum
MAX_MEMORY_BYTES = 128 * 1024 * 1024 * 1024  # 128GB maximum


def validate_memory_limit(memory_str: Optional[str]) -> tuple[bool, Optional[str]]:
    """
    Validate Docker memory limit format.
    
    Args:
        memory_str: Memory limit string (e.g., "512m", "2g", "1024m", "0" for unlimited)
    
    Returns:
        Tuple of (is_valid, error_message)
    
    Valid formats:
        - Empty string or None: No limit (unlimited)
        - "0": Unlimited
        - Number with suffix: b, k, m, g (case insensitive)
        - Examples: "512m", "2g", "1024m", "512M", "2G"
    """
    if not memory_str or memory_str.strip() == "":
        return True, None
    
    memory_str = memory_str.strip()
    
    # Allow "0" for unlimited
    if memory_str == "0":
        return True, None
    
    # Validate format: number followed by optional suffix (b, k, m, g)
    pattern = r'^(\d{1,15})([bkmg]?)$'  # Limit to 15 digits to prevent overflow
    match = re.match(pattern, memory_str.lower())
    
    if not match:
        return False, "Invalid format. Expected: number with optional suffix (b, k, m, g). Examples: '512m', '2g', '1024m'"
    
    try:
        value_str, suffix = match.groups()
        value = int(value_str)
    except (ValueError, OverflowError):
        return False, "Value too large or invalid"
    
    # Calculate actual bytes based on suffix
    if suffix == 'g':
        actual_bytes = value * 1024 * 1024 * 1024
    elif suffix == 'm':
        actual_bytes = value * 1024 * 1024
    elif suffix == 'k':
        actual_bytes = value * 1024
    else:  # 'b' or empty suffix (both represent bytes)
        actual_bytes = value
    
    # Validate maximum
    if actual_bytes > MAX_MEMORY_BYTES:
        return False, "Memory limit too high. Maximum is 128g"
    
    # Validate minimum
    if actual_bytes < MIN_MEMORY_BYTES:
        return False, "Memory limit too low. Minimum is 32m"
    
    return True, None


class CustomVariantParameter(BaseModel):
    """Represents a single engine parameter configuration"""
    name: str
    type: str  # "flag", "string", "int", "bytes", "path"
    value: Any
    enabled: bool = True
    
    @validator('type')
    def validate_type(cls, v):
        valid_types = ['flag', 'string', 'int', 'bytes', 'path']
        if v not in valid_types:
            raise ValueError(f'type must be one of: {", ".join(valid_types)}')
        return v


class CustomVariantConfig(BaseModel):
    """Complete custom variant configuration"""
    enabled: bool = False
    platform: str  # "amd64", "arm32", "arm64"
    amd64_version: str = "3.2.11-py3.10"  # For AMD64 platform: "3.2.11-py3.10", "3.2.11-py3.8", "3.1.75rc4-py3.7", "3.1.74"
    arm_version: str = "3.2.13"  # For ARM platforms: "3.2.13" or "3.2.14"
    memory_limit: Optional[str] = None  # Docker memory limit (e.g., "512m", "2g")
    parameters: List[CustomVariantParameter] = []
    
    # Torrent folder mount configuration
    torrent_folder_mount_enabled: bool = False
    torrent_folder_host_path: Optional[str] = None  # Host path to mount (e.g., "/mnt/torrents")
    torrent_folder_container_path: str = DEFAULT_TORRENT_FOLDER_PATH  # Default container path
    
    # Disk cache configuration
    disk_cache_mount_enabled: bool = False
    disk_cache_prune_enabled: bool = False
    disk_cache_prune_interval: int = 60  # Minutes
    disk_cache_file_max_age: int = 1440  # Minutes (24 hours)
    
    @validator('platform')
    def validate_platform(cls, v):
        valid_platforms = ['amd64', 'arm32', 'arm64']
        if v not in valid_platforms:
            raise ValueError(f'platform must be one of: {", ".join(valid_platforms)}')
        return v
    
    @validator('amd64_version')
    def validate_amd64_version(cls, v):
        valid_versions = ['3.2.11-py3.10', '3.2.11-py3.8', '3.1.75rc4-py3.7', '3.1.74']
        if v not in valid_versions:
            raise ValueError(f'amd64_version must be one of: {", ".join(valid_versions)}')
        return v
    
    @validator('arm_version')
    def validate_arm_version(cls, v):
        valid_versions = ['3.2.13', '3.2.14']
        if v not in valid_versions:
            raise ValueError(f'arm_version must be one of: {", ".join(valid_versions)}')
        return v
    
    @validator('memory_limit')
    def validate_memory_limit_field(cls, v):
        if v is None or v == "":
            return None
        is_valid, error_msg = validate_memory_limit(v)
        if not is_valid:
            raise ValueError(error_msg)
        return v
    
    @validator('torrent_folder_host_path')
    def validate_torrent_folder_host_path(cls, v, values):
        """Validate torrent folder host path when mount is enabled."""
        # Strip whitespace if value provided
        if v:
            v = v.strip()
        
        # Only validate if mount is enabled
        if values.get('torrent_folder_mount_enabled', False):
            if not v:
                raise ValueError("torrent_folder_host_path is required when torrent_folder_mount_enabled is True")
            # Basic validation for path format (absolute path)
            if not v.startswith('/'):
                raise ValueError("torrent_folder_host_path must be an absolute path (start with /)")
        return v
    
    @validator('torrent_folder_container_path')
    def validate_torrent_folder_container_path(cls, v):
        """Validate container path format."""
        if v and not v.startswith('/'):
            raise ValueError("torrent_folder_container_path must be an absolute path (start with /)")
        return v


def detect_platform() -> str:
    """
    Detect the current platform architecture.
    
    Returns:
        str: "amd64", "arm32", or "arm64"
    """
    machine = platform.machine().lower()
    
    # Map common architecture names
    if machine in ['x86_64', 'amd64']:
        return 'amd64'
    elif machine in ['aarch64', 'arm64']:
        return 'arm64'
    elif machine.startswith('arm'):
        # Try to distinguish between arm32 and arm64
        if '64' in machine:
            return 'arm64'
        else:
            return 'arm32'
    else:
        # Default to amd64 if unknown
        logger.warning(f"Unknown architecture '{machine}', defaulting to amd64")
        return 'amd64'


def get_default_parameters(platform: str) -> List[CustomVariantParameter]:
    """
    Get default parameters based on platform.
    
    Args:
        platform: Platform type ("amd64", "arm32", "arm64")
    
    Returns:
        List of default parameters with sensible defaults
    """
    # Common parameters across all platforms
    common_params = [
        # --- 1. Core Connection & Network ---
        CustomVariantParameter(name="--client-console", type="flag", value=True, enabled=True),
        CustomVariantParameter(name="--bind-all", type="flag", value=True, enabled=True),
        CustomVariantParameter(name="--service-remote-access", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--allow-user-config", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--random-port", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--upnp-nat-access", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--nat-detect", type="flag", value=True, enabled=True),
        CustomVariantParameter(name="--ipv6-enabled", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--ipv6-binds-v4", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--max-socket-connects", type="int", value=1000, enabled=False),
        CustomVariantParameter(name="--timeout-check-interval", type="int", value=30, enabled=False),
        CustomVariantParameter(name="--keepalive-interval", type="int", value=15, enabled=False),
        
        # P2P port - special handling (VPN-aware)
        CustomVariantParameter(name="--port", type="int", value=8621, enabled=False),

        # --- 2. Bandwidth & Limits ---
        CustomVariantParameter(name="--download-limit", type="int", value=0, enabled=False),  # 0 = unlimited
        CustomVariantParameter(name="--upload-limit", type="int", value=0, enabled=False),  # 0 = unlimited
        CustomVariantParameter(name="--max-upload-slots", type="int", value=4, enabled=True),
        CustomVariantParameter(name="--max-connections", type="int", value=200, enabled=True),
        CustomVariantParameter(name="--max-peers", type="int", value=40, enabled=True),
        CustomVariantParameter(name="--max-peers-limit", type="int", value=100, enabled=False),
        CustomVariantParameter(name="--min-peers", type="int", value=0, enabled=False),
        CustomVariantParameter(name="--auto-slots", type="int", value=1, enabled=True),  # Boolean as int

        # --- 3. Cache & Storage ---
        CustomVariantParameter(name="--cache-dir", type="path", value="~/.ACEStream", enabled=False),
        CustomVariantParameter(name="--cache-limit", type="int", value=10, enabled=False), # GB
        CustomVariantParameter(name="--cache-max-bytes", type="bytes", value=10737418240, enabled=False), # 10GB
        CustomVariantParameter(name="--disk-cache-limit", type="bytes", value=10737418240, enabled=False),
        CustomVariantParameter(name="--memory-cache-limit", type="bytes", value=268435456, enabled=False),
        CustomVariantParameter(name="--max-file-size", type="bytes", value=2147483648, enabled=True),  # 2GB
        CustomVariantParameter(name="--buffer-reads", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--reserve-space", type="flag", value=False, enabled=False),

        # --- 4. Live Streaming ---
        CustomVariantParameter(name="--live-cache-type", type="string", value="disk", enabled=True),
        CustomVariantParameter(name="--live-cache-size", type="bytes", value=268435456, enabled=True),  # 256MB
        CustomVariantParameter(name="--live-mem-cache-size", type="bytes", value=268435456, enabled=False),
        CustomVariantParameter(name="--live-disk-cache-size", type="bytes", value=1073741824, enabled=False),
        # Removed redundant --live-buffer (Basic)
        CustomVariantParameter(name="--live-buffer-time", type="int", value=10, enabled=False),
        CustomVariantParameter(name="--live-max-buffer-time", type="int", value=60, enabled=False),
        CustomVariantParameter(name="--live-adjust-buffer-time", type="int", value=1, enabled=False), # 0/1
        CustomVariantParameter(name="--live-disable-multiple-read-threads", type="int", value=0, enabled=False), # 0/1
        CustomVariantParameter(name="--live-stop-main-read-thread", type="int", value=0, enabled=False), # 0/1
        CustomVariantParameter(name="--live-cache-auto-size", type="int", value=0, enabled=False), # 0/1
        CustomVariantParameter(name="--live-cache-auto-size-reserve", type="bytes", value=104857600, enabled=False), # 100MB
        CustomVariantParameter(name="--live-cache-max-memory-percent", type="int", value=50, enabled=False),
        CustomVariantParameter(name="--live-aux-seeders", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--check-live-pos-interval", type="int", value=10, enabled=True),

        # --- 5. VOD ---
        CustomVariantParameter(name="--vod-cache-type", type="string", value="disk", enabled=True),
        CustomVariantParameter(name="--vod-cache-size", type="bytes", value=536870912, enabled=True),  # 512MB
        CustomVariantParameter(name="--vod-buffer", type="int", value=5, enabled=True),
        CustomVariantParameter(name="--vod-drop-max-age", type="int", value=0, enabled=False),
        CustomVariantParameter(name="--preload-vod", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--refill-buffer-interval", type="int", value=5, enabled=True),

        # --- 6. Logging & Debugging ---
        CustomVariantParameter(name="--log-file", type="path", value="", enabled=False),
        CustomVariantParameter(name="--log-debug", type="int", value=0, enabled=False),
        CustomVariantParameter(name="--log-max-size", type="bytes", value=10485760, enabled=True),  # 10MB
        CustomVariantParameter(name="--log-backup-count", type="int", value=3, enabled=True),
        CustomVariantParameter(name="--log-stdout", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--log-stderr", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--debug-sentry", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--enable-profiler", type="int", value=0, enabled=False), # 0/1
        CustomVariantParameter(name="--stats-report-interval", type="int", value=1, enabled=True),
        CustomVariantParameter(name="--stats-report-peers", type="flag", value=False, enabled=False),

        # --- 7. Security & API ---
        CustomVariantParameter(name="--access-token", type="string", value="", enabled=False),
        CustomVariantParameter(name="--service-access-token", type="string", value="", enabled=False),

        # --- 8. WebRTC ---
        CustomVariantParameter(name="--webrtc-allow-outgoing-connections", type="int", value=0, enabled=False),
        CustomVariantParameter(name="--webrtc-allow-incoming-connections", type="int", value=0, enabled=False),

        # --- 9. Advanced/Internal ---
        CustomVariantParameter(name="--slots-manager-use-cpu-limit", type="int", value=0, enabled=False),
        CustomVariantParameter(name="--core-skip-have-before-playback-pos", type="int", value=0, enabled=False),
        CustomVariantParameter(name="--core-dlr-periodic-check-interval", type="int", value=10, enabled=True),
    ]
    
    return common_params


def create_default_config(config_path: Path = DEFAULT_CONFIG_PATH) -> CustomVariantConfig:
    """
    Create and save a default configuration file.
    
    Args:
        config_path: Path to the config file
    
    Returns:
        Default configuration
    """
    detected_platform = detect_platform()
    config = CustomVariantConfig(
        enabled=False,
        platform=detected_platform,
        amd64_version="3.2.11-py3.10",
        arm_version="3.2.13",
        parameters=get_default_parameters(detected_platform)
    )
    
    save_config(config, config_path)
    logger.info(f"Created default custom variant config at {config_path}")
    return config


def load_config(config_path: Path = DEFAULT_CONFIG_PATH) -> Optional[CustomVariantConfig]:
    """
    Load custom variant configuration from JSON file.
    
    Args:
        config_path: Path to the config file
    
    Returns:
        Configuration object or None if file doesn't exist or is invalid
    """
    if not config_path.exists():
        logger.info(f"Config file {config_path} does not exist, creating default")
        return create_default_config(config_path)
    
    try:
        with open(config_path, 'r') as f:
            data = json.load(f)
        
        config = CustomVariantConfig(**data)
        logger.info(f"Loaded custom variant config from {config_path}")
        return config
    except Exception as e:
        logger.error(f"Failed to load custom variant config: {e}")
        return None


def save_config(config: CustomVariantConfig, config_path: Path = DEFAULT_CONFIG_PATH) -> bool:
    """
    Save custom variant configuration to JSON file.
    
    Args:
        config: Configuration to save
        config_path: Path to the config file
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Ensure parent directory exists
        config_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(config_path, 'w') as f:
            json.dump(config.dict(), f, indent=2)
        
        logger.info(f"Saved custom variant config to {config_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to save custom variant config: {e}")
        return False


def validate_config(config: CustomVariantConfig) -> tuple[bool, Optional[str]]:
    """
    Validate custom variant configuration.
    
    Args:
        config: Configuration to validate
    
    Returns:
        Tuple of (is_valid, error_message)
    """
    # Platform validation
    if config.platform not in ['amd64', 'arm32', 'arm64']:
        return False, f"Invalid platform: {config.platform}"
    
    # AMD64 version validation (only matters for AMD64 platform)
    if config.platform == 'amd64':
        if config.amd64_version not in ['3.2.11-py3.10', '3.2.11-py3.8', '3.1.75rc4-py3.7', '3.1.74']:
            return False, f"Invalid AMD64 version: {config.amd64_version}"
    
    # ARM version validation (only matters for ARM platforms)
    if config.platform in ['arm32', 'arm64']:
        if config.arm_version not in ['3.2.13', '3.2.14']:
            return False, f"Invalid ARM version: {config.arm_version}"
    
    # Parameter validation
    for param in config.parameters:
        # Check parameter type
        if param.type not in ['flag', 'string', 'int', 'bytes', 'path']:
            return False, f"Invalid parameter type for {param.name}: {param.type}"
        
        # Check value types match
        if param.enabled:
            if param.type == 'flag':
                if not isinstance(param.value, bool):
                    return False, f"Parameter {param.name} expects boolean value"
            elif param.type in ['int', 'bytes']:
                if not isinstance(param.value, int):
                    return False, f"Parameter {param.name} expects integer value"
            elif param.type in ['string', 'path']:
                if not isinstance(param.value, str):
                    return False, f"Parameter {param.name} expects string value"
    
    return True, None


def build_variant_config_from_custom(config: CustomVariantConfig) -> Dict[str, Any]:
    """
    Build a variant configuration dict compatible with provisioner.py
    from custom variant config.
    
    Args:
        config: Custom variant configuration
    
    Returns:
        Dict with 'image', 'config_type', and platform-specific config
    """
    # Determine base image
    if config.platform == 'amd64':
        # Use Nano-Ace image with version tag
        # Map version to appropriate tag
        if config.amd64_version == "3.2.11-py3.10":
            image = "ghcr.io/krinkuto11/nano-ace:latest"  # or :3.2.11-py3.10
        else:
            image = f"ghcr.io/krinkuto11/nano-ace:{config.amd64_version}"
        config_type = "cmd"
    elif config.platform == 'arm32':
        image = f"jopsis/acestream:arm32-v{config.arm_version}"
        config_type = "cmd"
    elif config.platform == 'arm64':
        image = f"jopsis/acestream:arm64-v{config.arm_version}"
        config_type = "cmd"
    else:
        # Fallback for unknown platforms - use amd64 Nano-Ace
        # All supported platforms (amd64, arm32, arm64) now use CMD-based configuration
        image = "ghcr.io/krinkuto11/nano-ace:latest"
        config_type = "cmd"
    
    result = {
        "image": image,
        "config_type": config_type,
        "is_custom": True
    }
    
    # Build parameter string or command
    if config_type == "cmd":
        # For all platforms (amd64, arm32, arm64), build command list
        if config.platform == 'amd64':
            # Nano-Ace uses /acestream/acestreamengine as the base command
            cmd = ["/acestream/acestreamengine"]
        else:
            # ARM platforms use python main.py
            cmd = ["python", "main.py"]
        
        for param in config.parameters:
            if not param.enabled:
                continue
            
            if param.type == 'flag':
                if param.value:  # Only add if True
                    cmd.append(param.name)
            else:
                # Add parameter with value
                cmd.extend([param.name, str(param.value)])
        
        result["base_cmd"] = cmd
    
    return result


# Global config instance (loaded on import)
_config_instance: Optional[CustomVariantConfig] = None


def get_config() -> Optional[CustomVariantConfig]:
    """Get the current custom variant configuration (singleton)"""
    global _config_instance
    if _config_instance is None:
        _config_instance = load_config()
    return _config_instance


def reload_config() -> Optional[CustomVariantConfig]:
    """Reload the custom variant configuration from disk"""
    global _config_instance
    _config_instance = load_config()
    return _config_instance


def is_custom_variant_enabled() -> bool:
    """Check if custom variant is enabled"""
    config = get_config()
    return config is not None and config.enabled
