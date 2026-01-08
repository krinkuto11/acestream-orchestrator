import logging
import sys
import os


def setup(level=None):
    """
    Setup logging with support for DEBUG_MODE environment variable.
    
    Args:
        level: Optional logging level. If not provided, will check DEBUG_MODE env var.
               DEBUG_MODE=true enables DEBUG level, otherwise INFO level is used.
    """
    # Determine logging level
    if level is None:
        debug_mode = os.getenv("DEBUG_MODE", "false").lower() == "true"
        level = logging.DEBUG if debug_mode else logging.INFO
    
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    
    # Suppress verbose httpx logs unless in debug mode
    if level != logging.DEBUG:
        logging.getLogger("httpx").setLevel(logging.WARNING)
    
    # Log the current logging level
    if level == logging.DEBUG:
        logging.getLogger(__name__).info("Debug logging enabled (DEBUG_MODE=true)")
