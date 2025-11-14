"""
Custom Engine Variant Configuration Management

Manages user-defined custom engine variants with configurable parameters.
"""

import json
import logging
import platform
from pathlib import Path
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator

logger = logging.getLogger(__name__)

# Default config file path
DEFAULT_CONFIG_PATH = Path("custom_engine_variant.json")


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
    arm_version: str = "3.2.13"  # For ARM platforms: "3.2.13" or "3.2.14"
    parameters: List[CustomVariantParameter] = []
    
    @validator('platform')
    def validate_platform(cls, v):
        valid_platforms = ['amd64', 'arm32', 'arm64']
        if v not in valid_platforms:
            raise ValueError(f'platform must be one of: {", ".join(valid_platforms)}')
        return v
    
    @validator('arm_version')
    def validate_arm_version(cls, v):
        valid_versions = ['3.2.13', '3.2.14']
        if v not in valid_versions:
            raise ValueError(f'arm_version must be one of: {", ".join(valid_versions)}')
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
        # Flags - default enabled
        CustomVariantParameter(name="--client-console", type="flag", value=True, enabled=True),
        CustomVariantParameter(name="--bind-all", type="flag", value=True, enabled=True),
        CustomVariantParameter(name="--service-remote-access", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--allow-user-config", type="flag", value=False, enabled=False),
        CustomVariantParameter(name="--stats-report-peers", type="flag", value=False, enabled=False),
        
        # String parameters
        CustomVariantParameter(name="--access-token", type="string", value="", enabled=False),
        CustomVariantParameter(name="--service-access-token", type="string", value="", enabled=False),
        CustomVariantParameter(name="--cache-dir", type="path", value="~/.ACEStream", enabled=False),
        CustomVariantParameter(name="--log-file", type="path", value="", enabled=False),
        
        # Cache configuration
        CustomVariantParameter(name="--live-cache-type", type="string", value="memory", enabled=True),
        CustomVariantParameter(name="--live-cache-size", type="bytes", value=268435456, enabled=True),  # 256MB
        CustomVariantParameter(name="--vod-cache-type", type="string", value="disk", enabled=True),
        CustomVariantParameter(name="--vod-cache-size", type="bytes", value=536870912, enabled=True),  # 512MB
        CustomVariantParameter(name="--vod-drop-max-age", type="int", value=0, enabled=False),
        CustomVariantParameter(name="--max-file-size", type="bytes", value=2147483648, enabled=True),  # 2GB
        
        # Buffer settings
        CustomVariantParameter(name="--live-buffer", type="int", value=10, enabled=True),
        CustomVariantParameter(name="--vod-buffer", type="int", value=5, enabled=True),
        CustomVariantParameter(name="--refill-buffer-interval", type="int", value=5, enabled=True),
        
        # Connection settings
        CustomVariantParameter(name="--max-connections", type="int", value=200, enabled=True),
        CustomVariantParameter(name="--max-peers", type="int", value=40, enabled=True),
        CustomVariantParameter(name="--max-upload-slots", type="int", value=4, enabled=True),
        CustomVariantParameter(name="--auto-slots", type="int", value=1, enabled=True),  # Boolean as int
        CustomVariantParameter(name="--download-limit", type="int", value=0, enabled=False),  # 0 = unlimited
        CustomVariantParameter(name="--upload-limit", type="int", value=0, enabled=False),  # 0 = unlimited
        
        # P2P port - special handling (VPN-aware)
        CustomVariantParameter(name="--port", type="int", value=8621, enabled=False),
        
        # WebRTC settings - boolean as int
        CustomVariantParameter(name="--webrtc-allow-outgoing-connections", type="int", value=0, enabled=False),
        CustomVariantParameter(name="--webrtc-allow-incoming-connections", type="int", value=0, enabled=False),
        
        # Stats settings
        CustomVariantParameter(name="--stats-report-interval", type="int", value=60, enabled=True),
        
        # Advanced settings
        CustomVariantParameter(name="--slots-manager-use-cpu-limit", type="int", value=0, enabled=False),
        CustomVariantParameter(name="--core-skip-have-before-playback-pos", type="int", value=0, enabled=False),
        CustomVariantParameter(name="--core-dlr-periodic-check-interval", type="int", value=10, enabled=True),
        CustomVariantParameter(name="--check-live-pos-interval", type="int", value=10, enabled=True),
        
        # Logging
        CustomVariantParameter(name="--log-debug", type="int", value=0, enabled=False),
        CustomVariantParameter(name="--log-max-size", type="bytes", value=10485760, enabled=True),  # 10MB
        CustomVariantParameter(name="--log-backup-count", type="int", value=3, enabled=True),
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
        image = "jopsis/acestream:x64"
        config_type = "env"
    elif config.platform == 'arm32':
        image = f"jopsis/acestream:arm32-v{config.arm_version}"
        config_type = "cmd"
    elif config.platform == 'arm64':
        image = f"jopsis/acestream:arm64-v{config.arm_version}"
        config_type = "cmd"
    else:
        # Fallback
        image = "jopsis/acestream:x64"
        config_type = "env"
    
    result = {
        "image": image,
        "config_type": config_type,
        "is_custom": True
    }
    
    # Build parameter string or command
    if config_type == "env":
        # For amd64, build ACESTREAM_ARGS string
        args = []
        for param in config.parameters:
            if not param.enabled:
                continue
            
            if param.type == 'flag':
                if param.value:  # Only add if True
                    args.append(param.name)
            else:
                # Add parameter with value
                args.append(f"{param.name} {param.value}")
        
        result["base_args"] = " ".join(args)
    else:
        # For ARM, build command list
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
