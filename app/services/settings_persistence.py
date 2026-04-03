"""
Settings persistence service for storing runtime configuration to JSON files.
"""

import json
import os
import logging
from pathlib import Path
from typing import Dict, Optional, Any

logger = logging.getLogger(__name__)

# Default config directory - use relative path from this file
CONFIG_DIR = Path(__file__).parent.parent / "config"

# Config file paths
PROXY_CONFIG_FILE = CONFIG_DIR / "proxy_settings.json"
LOOP_DETECTION_CONFIG_FILE = CONFIG_DIR / "loop_detection_settings.json"
ENGINE_SETTINGS_FILE = CONFIG_DIR / "engine_settings.json"
ORCHESTRATOR_CONFIG_FILE = CONFIG_DIR / "orchestrator_settings.json"
VPN_CONFIG_FILE = CONFIG_DIR / "vpn_settings.json"


class SettingsPersistence:
    """Handles persistence of runtime settings to JSON files."""

    @staticmethod
    def normalize_vpn_config(config: Dict[str, Any]) -> Dict[str, Any]:
        """Backfill missing VPN settings keys for schema evolution compatibility."""
        normalized = dict(config or {})
        normalized.setdefault("enabled", False)
        # Dynamic VPN controller mode is always used when VPN is enabled.
        normalized["dynamic_vpn_management"] = True
        normalized.setdefault("preferred_engines_per_vpn", 10)
        normalized.setdefault("provider", "")
        normalized.setdefault("protocol", "wireguard")
        normalized.setdefault("regions", [])
        normalized.setdefault("credentials", [])

        legacy_providers = normalized.get("providers")
        if not normalized.get("provider") and isinstance(legacy_providers, list) and legacy_providers:
            normalized["provider"] = str(legacy_providers[0]).strip()

        if "providers" in normalized:
            normalized.pop("providers", None)

        # Remove legacy static VPN keys from persisted payloads.
        for legacy_key in ("vpn_mode", "container_name", "container_name_2", "port_range_1", "port_range_2"):
            normalized.pop(legacy_key, None)

        if not isinstance(normalized.get("provider"), str):
            normalized["provider"] = ""
        if not isinstance(normalized.get("regions"), list):
            normalized["regions"] = []
        if not isinstance(normalized.get("credentials"), list):
            normalized["credentials"] = []

        try:
            normalized["preferred_engines_per_vpn"] = max(1, int(normalized.get("preferred_engines_per_vpn", 10)))
        except Exception:
            normalized["preferred_engines_per_vpn"] = 10

        return normalized
    
    @staticmethod
    def ensure_config_dir():
        """Ensure the config directory exists."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    
    @staticmethod
    def save_proxy_config(config: Dict[str, Any]) -> bool:
        """
        Save proxy configuration to JSON file.
        
        Args:
            config: Dictionary containing proxy configuration
            
        Returns:
            True if successful, False otherwise
        """
        try:
            SettingsPersistence.ensure_config_dir()
            
            with open(PROXY_CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            
            logger.debug(f"Proxy configuration saved to {PROXY_CONFIG_FILE}")
            return True
        except Exception as e:
            logger.error(f"Failed to save proxy configuration: {e}")
            return False
    
    @staticmethod
    def load_proxy_config() -> Optional[Dict[str, Any]]:
        """
        Load proxy configuration from JSON file.
        
        Returns:
            Dictionary containing proxy configuration, or None if file doesn't exist or error occurs
        """
        try:
            if not PROXY_CONFIG_FILE.exists():
                logger.debug(f"Proxy config file not found: {PROXY_CONFIG_FILE}")
                return None
            
            with open(PROXY_CONFIG_FILE, 'r') as f:
                config = json.load(f)
            
            logger.debug(f"Proxy configuration loaded from {PROXY_CONFIG_FILE}")
            return config
        except Exception as e:
            logger.error(f"Failed to load proxy configuration: {e}")
            return None
    
    @staticmethod
    def save_loop_detection_config(config: Dict[str, Any]) -> bool:
        """
        Save loop detection configuration to JSON file.
        
        Args:
            config: Dictionary containing loop detection configuration
            
        Returns:
            True if successful, False otherwise
        """
        try:
            SettingsPersistence.ensure_config_dir()
            
            with open(LOOP_DETECTION_CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            
            logger.debug(f"Loop detection configuration saved to {LOOP_DETECTION_CONFIG_FILE}")
            return True
        except Exception as e:
            logger.error(f"Failed to save loop detection configuration: {e}")
            return False
    
    @staticmethod
    def load_loop_detection_config() -> Optional[Dict[str, Any]]:
        """
        Load loop detection configuration from JSON file.
        
        Returns:
            Dictionary containing loop detection configuration, or None if file doesn't exist or error occurs
        """
        try:
            if not LOOP_DETECTION_CONFIG_FILE.exists():
                logger.debug(f"Loop detection config file not found: {LOOP_DETECTION_CONFIG_FILE}")
                return None
            
            with open(LOOP_DETECTION_CONFIG_FILE, 'r') as f:
                config = json.load(f)
            
            logger.debug(f"Loop detection configuration loaded from {LOOP_DETECTION_CONFIG_FILE}")
            return config
        except Exception as e:
            logger.error(f"Failed to load loop detection configuration: {e}")
            return None
    
    @staticmethod
    def save_engine_settings(config: Dict[str, Any]) -> bool:
        """
        Save engine settings configuration to JSON file.
        
        Args:
            config: Dictionary containing engine settings configuration
            
        Returns:
            True if successful, False otherwise
        """
        try:
            SettingsPersistence.ensure_config_dir()
            
            with open(ENGINE_SETTINGS_FILE, 'w') as f:
                json.dump(config, f, indent=2)
            
            logger.debug(f"Engine settings configuration saved to {ENGINE_SETTINGS_FILE}")
            return True
        except Exception as e:
            logger.error(f"Failed to save engine settings configuration: {e}")
            return False
    
    @staticmethod
    def load_engine_settings() -> Optional[Dict[str, Any]]:
        """
        Load engine settings configuration from JSON file.
        
        Returns:
            Dictionary containing engine settings configuration, or None if file doesn't exist or error occurs
        """
        try:
            if not ENGINE_SETTINGS_FILE.exists():
                logger.debug(f"Engine settings config file not found: {ENGINE_SETTINGS_FILE}")
                return None
            
            with open(ENGINE_SETTINGS_FILE, 'r') as f:
                config = json.load(f)
            
            logger.debug(f"Engine settings configuration loaded from {ENGINE_SETTINGS_FILE}")
            return config
        except Exception as e:
            logger.error(f"Failed to load engine settings configuration: {e}")
            return None

    @staticmethod
    def save_orchestrator_config(config: Dict[str, Any]) -> bool:
        """
        Save orchestrator core configuration to JSON file.

        Args:
            config: Dictionary containing orchestrator configuration

        Returns:
            True if successful, False otherwise
        """
        try:
            SettingsPersistence.ensure_config_dir()

            with open(ORCHESTRATOR_CONFIG_FILE, 'w') as f:
                json.dump(config, f, indent=2)

            logger.debug(f"Orchestrator configuration saved to {ORCHESTRATOR_CONFIG_FILE}")
            return True
        except Exception as e:
            logger.error(f"Failed to save orchestrator configuration: {e}")
            return False

    @staticmethod
    def load_orchestrator_config() -> Optional[Dict[str, Any]]:
        """
        Load orchestrator core configuration from JSON file.

        Returns:
            Dictionary containing orchestrator configuration, or None if file doesn't exist or error occurs
        """
        try:
            if not ORCHESTRATOR_CONFIG_FILE.exists():
                logger.debug(f"Orchestrator config file not found: {ORCHESTRATOR_CONFIG_FILE}")
                return None

            with open(ORCHESTRATOR_CONFIG_FILE, 'r') as f:
                config = json.load(f)

            logger.debug(f"Orchestrator configuration loaded from {ORCHESTRATOR_CONFIG_FILE}")
            return config
        except Exception as e:
            logger.error(f"Failed to load orchestrator configuration: {e}")
            return None

    @staticmethod
    def save_vpn_config(config: Dict[str, Any]) -> bool:
        """
        Save VPN (Gluetun) configuration to JSON file.

        Args:
            config: Dictionary containing VPN configuration

        Returns:
            True if successful, False otherwise
        """
        try:
            SettingsPersistence.ensure_config_dir()
            normalized = SettingsPersistence.normalize_vpn_config(config)

            with open(VPN_CONFIG_FILE, 'w') as f:
                json.dump(normalized, f, indent=2)

            logger.debug(f"VPN configuration saved to {VPN_CONFIG_FILE}")
            return True
        except Exception as e:
            logger.error(f"Failed to save VPN configuration: {e}")
            return False

    @staticmethod
    def load_vpn_config() -> Optional[Dict[str, Any]]:
        """
        Load VPN (Gluetun) configuration from JSON file.

        Returns:
            Dictionary containing VPN configuration, or None if file doesn't exist or error occurs
        """
        try:
            if not VPN_CONFIG_FILE.exists():
                logger.debug(f"VPN config file not found: {VPN_CONFIG_FILE}")
                return None

            with open(VPN_CONFIG_FILE, 'r') as f:
                config = json.load(f)

            config = SettingsPersistence.normalize_vpn_config(config)

            logger.debug(f"VPN configuration loaded from {VPN_CONFIG_FILE}")
            return config
        except Exception as e:
            logger.error(f"Failed to load VPN configuration: {e}")
            return None
