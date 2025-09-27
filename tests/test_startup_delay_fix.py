#!/usr/bin/env python3
"""
Test to validate the fix for slow orchestrator startup due to Gluetun health check delays.

The issue was:
1. Orchestrator starts -> cleanup -> start Gluetun monitor -> immediately call ensure_minimum()
2. ensure_minimum() tries to create 3 engines immediately
3. Each engine creation waits 30 seconds for Gluetun to become healthy
4. Total delay: 3 * 30 = 90 seconds of blocking waits during startup

The fix:
1. Wait for Gluetun to become healthy during startup (max 60s, async)
2. Only then call ensure_minimum() to provision engines
3. Reduce individual engine timeout from 30s to 5s since Gluetun should already be healthy
"""

import sys
import os
import asyncio
import time
from unittest.mock import Mock, patch, AsyncMock

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

async def test_startup_waits_for_gluetun():
    """Test that startup waits for Gluetun to become healthy before provisioning."""
    
    with patch('app.main.cfg') as mock_cfg:
        mock_cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        
        # Mock gluetun monitor that returns healthy after a few calls
        with patch('app.main.gluetun_monitor') as mock_monitor:
            health_call_count = 0
            def mock_health_check():
                nonlocal health_call_count
                health_call_count += 1
                return health_call_count >= 3  # Healthy on 3rd call
            
            mock_monitor.is_healthy.side_effect = mock_health_check
            mock_monitor.start = AsyncMock()
            
            # Mock other startup functions
            ensure_minimum_called = False
            def mock_ensure_minimum():
                nonlocal ensure_minimum_called
                ensure_minimum_called = True
            
            with patch('app.main.cleanup_on_shutdown'), \
                 patch('app.main.load_state_from_db'), \
                 patch('app.main.ensure_minimum', side_effect=mock_ensure_minimum), \
                 patch('app.main.reindex_existing'), \
                 patch('app.main.Base'), \
                 patch('app.main.asyncio.create_task'):
                
                from app.main import lifespan
                from fastapi import FastAPI
                
                app = FastAPI()
                start_time = time.time()
                
                # Run startup
                async with lifespan(app):
                    pass
                
                end_time = time.time()
                startup_duration = end_time - start_time
                
                # Verify behavior
                assert health_call_count >= 3, f"Should have checked Gluetun health multiple times, got {health_call_count}"
                assert ensure_minimum_called, "Should have called ensure_minimum after Gluetun became healthy"
                assert startup_duration < 10, f"Startup should be fast when Gluetun becomes healthy quickly, took {startup_duration}s"
                
                print(f"✅ Startup waited for Gluetun health ({health_call_count} checks) and completed in {startup_duration:.2f}s")

async def test_startup_timeout_if_gluetun_never_healthy():
    """Test that startup continues even if Gluetun never becomes healthy."""
    
    with patch('app.main.cfg') as mock_cfg:
        mock_cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        
        # Mock gluetun monitor that never becomes healthy
        with patch('app.main.gluetun_monitor') as mock_monitor:
            mock_monitor.is_healthy.return_value = False
            mock_monitor.start = AsyncMock()
            
            # Mock other startup functions  
            ensure_minimum_called = False
            def mock_ensure_minimum():
                nonlocal ensure_minimum_called
                ensure_minimum_called = True
            
            with patch('app.main.cleanup_on_shutdown'), \
                 patch('app.main.load_state_from_db'), \
                 patch('app.main.ensure_minimum', side_effect=mock_ensure_minimum), \
                 patch('app.main.reindex_existing'), \
                 patch('app.main.Base'), \
                 patch('app.main.asyncio.create_task'):
                
                from app.main import lifespan
                from fastapi import FastAPI
                
                app = FastAPI()
                start_time = time.time()
                
                # Use shorter timeout for test
                with patch('app.main.max_wait_time', 2):  # 2 second timeout
                    async with lifespan(app):
                        pass
                
                end_time = time.time()
                startup_duration = end_time - start_time
                
                # Should timeout after ~2 seconds but still proceed
                assert ensure_minimum_called, "Should still call ensure_minimum even after timeout"
                assert 1.5 <= startup_duration <= 4, f"Should timeout after ~2s, took {startup_duration:.2f}s"
                
                print(f"✅ Startup timed out gracefully and continued after {startup_duration:.2f}s")

def test_provisioner_reduced_timeout():
    """Test that the provisioner now uses reduced timeout for Gluetun health checks."""
    
    with patch('app.services.provisioner.cfg') as mock_cfg, \
         patch('app.services.provisioner.time') as mock_time:
        
        mock_cfg.GLUETUN_CONTAINER_NAME = "gluetun"
        
        # Mock time progression to test timeout
        start_time = 1000.0
        mock_time.time.side_effect = [
            start_time,        # Initial time
            start_time + 1,    # 1st check
            start_time + 2,    # 2nd check  
            start_time + 3,    # 3rd check
            start_time + 4,    # 4th check
            start_time + 6,    # Should exceed 5s timeout
        ]
        mock_time.sleep = Mock()  # Mock sleep to avoid actual delays
        
        # Mock gluetun monitor that never becomes healthy
        with patch('app.services.provisioner.gluetun_monitor') as mock_monitor:
            mock_monitor.is_healthy.return_value = False
            
            with patch('app.services.provisioner._check_gluetun_health_sync') as mock_sync:
                mock_sync.return_value = False
                
                # Mock other provisioning dependencies
                with patch('app.services.provisioner.generate_container_name'), \
                     patch('app.services.provisioner.alloc'):
                    
                    from app.services.provisioner import AceProvisionRequest, start_acestream
                    
                    # This should timeout after 5 seconds, not 30
                    try:
                        start_acestream(AceProvisionRequest())
                        assert False, "Should have raised timeout exception"
                    except RuntimeError as e:
                        assert "not healthy" in str(e), f"Should get health error, got: {e}"
                        
                        # Verify sleep was called with 0.5s intervals, not 1s
                        sleep_calls = [call[0][0] for call in mock_time.sleep.call_args_list]
                        assert all(interval == 0.5 for interval in sleep_calls), f"Should sleep 0.5s intervals, got {sleep_calls}"
    
    print("✅ Provisioner uses reduced timeout (5s) and faster polling (0.5s)")

async def test_startup_without_gluetun():
    """Test that startup works normally when Gluetun is not configured."""
    
    with patch('app.main.cfg') as mock_cfg:
        mock_cfg.GLUETUN_CONTAINER_NAME = None  # No Gluetun configured
        
        ensure_minimum_called = False
        def mock_ensure_minimum():
            nonlocal ensure_minimum_called
            ensure_minimum_called = True
        
        with patch('app.main.cleanup_on_shutdown'), \
             patch('app.main.load_state_from_db'), \
             patch('app.main.ensure_minimum', side_effect=mock_ensure_minimum), \
             patch('app.main.reindex_existing'), \
             patch('app.main.Base'), \
             patch('app.main.asyncio.create_task'), \
             patch('app.main.gluetun_monitor') as mock_monitor:
            
            mock_monitor.start = AsyncMock()
            
            from app.main import lifespan
            from fastapi import FastAPI
            
            app = FastAPI()
            start_time = time.time()
            
            # Run startup
            async with lifespan(app):
                pass
            
            end_time = time.time()
            startup_duration = end_time - start_time
            
            # Should be very fast without Gluetun waiting
            assert ensure_minimum_called, "Should call ensure_minimum"
            assert startup_duration < 1, f"Should be very fast without Gluetun, took {startup_duration:.2f}s"
            
            # Should not call gluetun health checks
            assert not hasattr(mock_monitor, 'is_healthy') or not mock_monitor.is_healthy.called, "Should not check Gluetun health when not configured"
    
    print("✅ Startup works normally without Gluetun configuration")

if __name__ == "__main__":
    print("🧪 Running startup delay fix tests...")
    
    # Test the async startup logic
    asyncio.run(test_startup_waits_for_gluetun())
    asyncio.run(test_startup_timeout_if_gluetun_never_healthy())
    asyncio.run(test_startup_without_gluetun())
    
    # Test the provisioner timeout reduction
    test_provisioner_reduced_timeout()
    
    print("🎉 All startup delay fix tests passed!")