"""
Template Manager for Custom Engine Variants

Manages 10 template slots for custom engine variant configurations.
Each template is stored as a separate JSON file.
"""

import json
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from .custom_variant_config import CustomVariantConfig

logger = logging.getLogger(__name__)

# Template directory
TEMPLATE_DIR = Path("custom_templates")
MAX_TEMPLATES = 10


class Template:
    """Represents a single template"""
    def __init__(self, slot_id: int, name: str, config: CustomVariantConfig):
        self.slot_id = slot_id
        self.name = name
        self.config = config
    
    def to_dict(self):
        return {
            "slot_id": self.slot_id,
            "name": self.name,
            "config": self.config.dict()
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            slot_id=data["slot_id"],
            name=data["name"],
            config=CustomVariantConfig(**data["config"])
        )


def ensure_template_directory():
    """Ensure the template directory exists"""
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)


def get_template_path(slot_id: int) -> Path:
    """Get the path for a template file"""
    if slot_id < 1 or slot_id > MAX_TEMPLATES:
        raise ValueError(f"Template slot_id must be between 1 and {MAX_TEMPLATES}")
    return TEMPLATE_DIR / f"template_{slot_id}.json"


def list_templates() -> List[Dict[str, Any]]:
    """
    List all available templates.
    
    Returns:
        List of template metadata (slot_id, name, exists)
    """
    ensure_template_directory()
    
    templates = []
    for slot_id in range(1, MAX_TEMPLATES + 1):
        template_path = get_template_path(slot_id)
        if template_path.exists():
            try:
                with open(template_path, 'r') as f:
                    data = json.load(f)
                templates.append({
                    "slot_id": slot_id,
                    "name": data.get("name", f"Template {slot_id}"),
                    "exists": True
                })
            except Exception as e:
                logger.error(f"Failed to read template {slot_id}: {e}")
                templates.append({
                    "slot_id": slot_id,
                    "name": f"Template {slot_id}",
                    "exists": False
                })
        else:
            templates.append({
                "slot_id": slot_id,
                "name": f"Template {slot_id}",
                "exists": False
            })
    
    return templates


def get_template(slot_id: int) -> Optional[Template]:
    """
    Load a template from a specific slot.
    
    Args:
        slot_id: Template slot number (1-10)
    
    Returns:
        Template object or None if not found
    """
    template_path = get_template_path(slot_id)
    
    if not template_path.exists():
        logger.info(f"Template {slot_id} does not exist")
        return None
    
    try:
        with open(template_path, 'r') as f:
            data = json.load(f)
        
        template = Template.from_dict(data)
        logger.debug(f"Loaded template {slot_id}: {template.name}")
        return template
    except Exception as e:
        logger.error(f"Failed to load template {slot_id}: {e}")
        return None


def save_template(slot_id: int, name: str, config: CustomVariantConfig) -> bool:
    """
    Save a template to a specific slot.
    
    Args:
        slot_id: Template slot number (1-10)
        name: Template name
        config: Custom variant configuration
    
    Returns:
        True if successful, False otherwise
    """
    try:
        ensure_template_directory()
        template_path = get_template_path(slot_id)
        
        template = Template(slot_id, name, config)
        
        with open(template_path, 'w') as f:
            json.dump(template.to_dict(), f, indent=2)
        
        logger.info(f"Saved template {slot_id}: {name}")
        return True
    except Exception as e:
        logger.error(f"Failed to save template {slot_id}: {e}")
        return False


def delete_template(slot_id: int) -> bool:
    """
    Delete a template from a specific slot.
    
    Args:
        slot_id: Template slot number (1-10)
    
    Returns:
        True if successful, False otherwise
    """
    try:
        template_path = get_template_path(slot_id)
        
        if template_path.exists():
            template_path.unlink()
            logger.info(f"Deleted template {slot_id}")
            return True
        else:
            logger.info(f"Template {slot_id} does not exist")
            return False
    except Exception as e:
        logger.error(f"Failed to delete template {slot_id}: {e}")
        return False


def export_template(slot_id: int) -> Optional[str]:
    """
    Export a template as JSON string.
    
    Args:
        slot_id: Template slot number (1-10)
    
    Returns:
        JSON string or None if template doesn't exist
    """
    template = get_template(slot_id)
    if template:
        return json.dumps(template.to_dict(), indent=2)
    return None


def import_template(slot_id: int, json_data: str) -> tuple[bool, Optional[str]]:
    """
    Import a template from JSON string.
    
    Args:
        slot_id: Template slot number (1-10)
        json_data: JSON string containing template data
    
    Returns:
        Tuple of (success, error_message)
    """
    try:
        data = json.loads(json_data)
        
        # Validate the data structure
        if "name" not in data or "config" not in data:
            return False, "Invalid template format: missing 'name' or 'config'"
        
        # Create template object to validate config
        template = Template.from_dict({
            "slot_id": slot_id,
            "name": data["name"],
            "config": data["config"]
        })
        
        # Save the template
        success = save_template(slot_id, template.name, template.config)
        if success:
            return True, None
        else:
            return False, "Failed to save template"
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {str(e)}"
    except Exception as e:
        return False, f"Failed to import template: {str(e)}"


# Track which template is currently active
_active_template_id: Optional[int] = None


def _load_active_template_from_db() -> Optional[int]:
    """Load active template ID from database"""
    try:
        from .db import SessionLocal
        from ..models.db_models import ConfigRow
        
        with SessionLocal() as session:
            row = session.query(ConfigRow).filter_by(key="active_template_id").first()
            if row and row.value:
                return int(row.value)
    except Exception as e:
        logger.error(f"Failed to load active template ID from database: {e}")
    return None


def _save_active_template_to_db(slot_id: Optional[int]):
    """Save active template ID to database"""
    try:
        from .db import SessionLocal
        from ..models.db_models import ConfigRow
        from datetime import datetime, timezone
        
        with SessionLocal() as session:
            row = session.query(ConfigRow).filter_by(key="active_template_id").first()
            if row:
                row.value = str(slot_id) if slot_id is not None else None
                row.updated_at = datetime.now(timezone.utc)
            else:
                row = ConfigRow(
                    key="active_template_id",
                    value=str(slot_id) if slot_id is not None else None
                )
                session.add(row)
            session.commit()
    except Exception as e:
        logger.error(f"Failed to save active template ID to database: {e}")


def set_active_template(slot_id: Optional[int]):
    """Set the currently active template and persist to database"""
    global _active_template_id
    _active_template_id = slot_id
    _save_active_template_to_db(slot_id)


def get_active_template_id() -> Optional[int]:
    """Get the currently active template ID"""
    global _active_template_id
    
    # Load from database if not in memory
    if _active_template_id is None:
        _active_template_id = _load_active_template_from_db()
    
    return _active_template_id


def get_active_template_name() -> Optional[str]:
    """Get the name of the currently active template"""
    if _active_template_id is None:
        return None
    
    template = get_template(_active_template_id)
    return template.name if template else None


def rename_template(slot_id: int, new_name: str) -> bool:
    """
    Rename a template.
    
    Args:
        slot_id: Template slot number (1-10)
        new_name: New name for the template
    
    Returns:
        True if successful, False otherwise
    """
    try:
        # Load existing template
        template = get_template(slot_id)
        if not template:
            logger.error(f"Cannot rename: template {slot_id} does not exist")
            return False
        
        # Save with new name but same config
        success = save_template(slot_id, new_name, template.config)
        if success:
            logger.info(f"Renamed template {slot_id} to '{new_name}'")
        return success
    except Exception as e:
        logger.error(f"Failed to rename template {slot_id}: {e}")
        return False
