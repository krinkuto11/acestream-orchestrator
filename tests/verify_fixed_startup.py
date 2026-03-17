#!/usr/bin/env python3
"""
Standalone diagnostic script to verify non-blocking startup.
"""
import sys
import os
import asyncio
import time
from unittest.mock import Mock, patch, AsyncMock

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

async def test_non_blocking_startup():
    """Verify that lifespan completes quickly even if Gluetun is unhealthy."""
    from app.main import lifespan
    from fastapi import FastAPI
    import app.main as main_mod

    # We need to mock things again because each process starts fresh
    with patch('app.main.cfg') as mock_cfg:
        mock_cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        
        # Mock gluetun monitor that is always unhealthy
        with patch('app.main.gluetun_monitor') as mock_monitor:
            mock_monitor.is_healthy.return_value = False
            mock_monitor.start = AsyncMock()
            
            # Mock other startup functions
            with patch('app.main.cleanup_on_shutdown'), \
                 patch('app.main.load_state_from_db'), \
                 patch('app.main.ensure_minimum'), \
                 patch('app.main.reindex_existing'), \
                 patch('app.main.Base'), \
                 patch('app.main.asyncio.create_task') as mock_create_task, \
                 patch('app.main.start_cleanup_task', new_callable=AsyncMock):
                
                app = FastAPI()
                start_time = time.time()
                
                # Run startup
                async with lifespan(app):
                    pass
                
                end_time = time.time()
                startup_duration = end_time - start_time
                
                # Check if _provision_worker was created as a task
                provision_worker_created = False
                for call in mock_create_task.call_args_list:
                    if call[0][0].__name__ == '_provision_worker':
                        provision_worker_created = True
                        break
                
                if startup_duration < 2.0 and provision_worker_created:
                    print(f"✅ Non-blocking startup verified: completed in {startup_duration:.2f}s")
                    print(f"✅ _provision_worker task creation verified")
                    return True
                else:
                    print(f"❌ Verification failed:")
                    print(f"   Startup duration: {startup_duration:.2f}s (expected < 2s)")
                    print(f"   Provision worker created: {provision_worker_created} (expected True)")
                    return False

if __name__ == "__main__":
    print("🧪 Verifying non-blocking startup...")
    try:
        success = asyncio.run(test_non_blocking_startup())
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"ERROR during verification: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
