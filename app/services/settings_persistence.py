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


class SettingsPersistence:
    """Handles persistence of runtime settings to JSON files."""
    
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
            
            logger.info(f"Proxy configuration saved to {PROXY_CONFIG_FILE}")
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
            
            logger.info(f"Proxy configuration loaded from {PROXY_CONFIG_FILE}")
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
            
            logger.info(f"Loop detection configuration saved to {LOOP_DETECTION_CONFIG_FILE}")
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
            
            logger.info(f"Loop detection configuration loaded from {LOOP_DETECTION_CONFIG_FILE}")
            return config
        except Exception as e:
            logger.error(f"Failed to load loop detection configuration: {e}")
            return None
