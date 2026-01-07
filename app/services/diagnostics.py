"""
Diagnostic service for debugging AceStream proxy connection issues.
Provides comprehensive tests to identify why proxy connections fail.
"""

import asyncio
import logging
import httpx
import time
from typing import Dict, List, Any, Optional
from datetime import datetime, timezone
from uuid import uuid4

from .state import state
from .proxy.engine_selector import EngineSelector
from ..core.config import cfg

logger = logging.getLogger(__name__)


class DiagnosticTest:
    """Represents a single diagnostic test result"""
    
    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description
        self.status: str = "pending"  # pending, running, passed, failed, warning
        self.message: Optional[str] = None
        self.details: Dict[str, Any] = {}
        self.duration: Optional[float] = None
        self.timestamp: Optional[str] = None
        self.error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "message": self.message,
            "details": self.details,
            "duration": self.duration,
            "timestamp": self.timestamp,
            "error": self.error
        }


class AceStreamDiagnostics:
    """
    Comprehensive diagnostic suite for AceStream proxy issues.
    Tests engine connectivity, API endpoints, and stream initialization.
    """
    
    def __init__(self):
        self.tests: List[DiagnosticTest] = []
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
    
    async def run_full_diagnostics(self, test_ace_ids: List[str]) -> Dict[str, Any]:
        """
        Run complete diagnostic suite.
        
        Args:
            test_ace_ids: List of AceStream IDs to test (infohashes or content IDs)
            
        Returns:
            Dictionary with comprehensive diagnostic results
        """
        self.tests = []
        self.start_time = time.time()
        
        logger.info(f"Starting diagnostics with test IDs: {test_ace_ids}")
        
        # Test 1: Check available engines
        await self._test_available_engines()
        
        # Test 2: Check engine health
        await self._test_engine_health()
        
        # Test 3: Check Docker network connectivity
        await self._test_docker_network()
        
        # Test 4: Check VPN configuration (if enabled)
        if cfg.GLUETUN_CONTAINER_NAME:
            await self._test_vpn_configuration()
        
        # Test 5: Test engine HTTP endpoints
        await self._test_engine_http_endpoints()
        
        # Test 6: Test AceStream getstream with provided IDs
        for ace_id in test_ace_ids:
            await self._test_acestream_getstream(ace_id)
        
        # Test 7: Test stream manager connection
        for ace_id in test_ace_ids:
            await self._test_stream_manager_connection(ace_id)
        
        self.end_time = time.time()
        
        return self._generate_report()
    
    async def _test_available_engines(self):
        """Test if any engines are available"""
        test = DiagnosticTest(
            "available_engines",
            "Check if AceStream engines are available"
        )
        test.status = "running"
        test.timestamp = datetime.now(timezone.utc).isoformat()
        start = time.time()
        
        try:
            engine_count = len(state.engines)
            healthy_count = sum(
                1 for e in state.engines.values() 
                if e.health_status == "healthy"
            )
            
            test.details = {
                "total_engines": engine_count,
                "healthy_engines": healthy_count,
                "engines": [
                    {
                        "container_id": cid[:12],
                        "host": e.host,
                        "port": e.port,
                        "health_status": e.health_status,
                        "is_forwarded": e.labels.get("acestream.forwarded") == "true"
                    }
                    for cid, e in state.engines.items()
                ]
            }
            
            if engine_count == 0:
                test.status = "failed"
                test.message = "No engines found in orchestrator state"
                test.error = "No engines available - provision engines first"
            elif healthy_count == 0:
                test.status = "failed"
                test.message = f"Found {engine_count} engines but none are healthy"
                test.error = "All engines are unhealthy"
            else:
                test.status = "passed"
                test.message = f"Found {healthy_count} healthy engines out of {engine_count} total"
                
        except Exception as e:
            test.status = "failed"
            test.error = str(e)
            test.message = f"Error checking engines: {e}"
            logger.error(f"Engine check failed: {e}", exc_info=True)
        
        test.duration = time.time() - start
        self.tests.append(test)
    
    async def _test_engine_health(self):
        """Test engine health status"""
        test = DiagnosticTest(
            "engine_health",
            "Verify engine health monitoring is working"
        )
        test.status = "running"
        test.timestamp = datetime.now(timezone.utc).isoformat()
        start = time.time()
        
        try:
            from .health_manager import health_manager
            
            health_issues = []
            for container_id, engine in state.engines.items():
                if engine.health_status != "healthy":
                    health_issues.append({
                        "container_id": container_id[:12],
                        "status": engine.health_status,
                        "last_check": engine.last_health_check.isoformat() if engine.last_health_check else None
                    })
            
            test.details = {
                "health_issues": health_issues,
                "total_unhealthy": len(health_issues)
            }
            
            if health_issues:
                test.status = "warning"
                test.message = f"{len(health_issues)} engines have health issues"
            else:
                test.status = "passed"
                test.message = "All engines are healthy"
                
        except Exception as e:
            test.status = "failed"
            test.error = str(e)
            test.message = f"Error checking engine health: {e}"
            logger.error(f"Health check failed: {e}", exc_info=True)
        
        test.duration = time.time() - start
        self.tests.append(test)
    
    async def _test_docker_network(self):
        """Test Docker network configuration"""
        test = DiagnosticTest(
            "docker_network",
            "Verify Docker network is properly configured"
        )
        test.status = "running"
        test.timestamp = datetime.now(timezone.utc).isoformat()
        start = time.time()
        
        try:
            test.details = {
                "network_name": cfg.DOCKER_NETWORK,
                "host_mode": cfg.DOCKER_NETWORK.lower() == "host"
            }
            
            # Check if engines are on the same network
            if state.engines:
                sample_engine = next(iter(state.engines.values()))
                networks = sample_engine.labels.get("networks", "").split(",")
                test.details["engine_networks"] = networks
                
                if cfg.DOCKER_NETWORK.lower() != "host" and cfg.DOCKER_NETWORK not in networks:
                    test.status = "warning"
                    test.message = f"Engine may not be on expected network {cfg.DOCKER_NETWORK}"
                else:
                    test.status = "passed"
                    test.message = "Docker network configuration looks correct"
            else:
                test.status = "warning"
                test.message = "No engines available to verify network"
                
        except Exception as e:
            test.status = "failed"
            test.error = str(e)
            test.message = f"Error checking Docker network: {e}"
            logger.error(f"Docker network check failed: {e}", exc_info=True)
        
        test.duration = time.time() - start
        self.tests.append(test)
    
    async def _test_vpn_configuration(self):
        """Test VPN configuration if enabled"""
        test = DiagnosticTest(
            "vpn_configuration",
            "Check VPN configuration and connectivity"
        )
        test.status = "running"
        test.timestamp = datetime.now(timezone.utc).isoformat()
        start = time.time()
        
        try:
            from .gluetun import gluetun_monitor
            
            is_healthy = gluetun_monitor.is_healthy()
            vpn_info = gluetun_monitor.get_vpn_info()
            
            test.details = {
                "vpn_mode": cfg.VPN_MODE,
                "container_name": cfg.GLUETUN_CONTAINER_NAME,
                "is_healthy": is_healthy,
                "vpn_info": vpn_info
            }
            
            if is_healthy:
                test.status = "passed"
                test.message = "VPN is healthy and connected"
            else:
                test.status = "failed"
                test.message = "VPN is not healthy or not connected"
                test.error = "VPN connectivity issue detected"
                
        except Exception as e:
            test.status = "failed"
            test.error = str(e)
            test.message = f"Error checking VPN: {e}"
            logger.error(f"VPN check failed: {e}", exc_info=True)
        
        test.duration = time.time() - start
        self.tests.append(test)
    
    async def _test_engine_http_endpoints(self):
        """Test basic HTTP connectivity to engines"""
        test = DiagnosticTest(
            "engine_http",
            "Test HTTP connectivity to engine API endpoints"
        )
        test.status = "running"
        test.timestamp = datetime.now(timezone.utc).isoformat()
        start = time.time()
        
        try:
            results = []
            
            # Test first 3 healthy engines
            healthy_engines = [
                (cid, e) for cid, e in state.engines.items()
                if e.health_status == "healthy"
            ][:3]
            
            if not healthy_engines:
                test.status = "failed"
                test.message = "No healthy engines to test"
                test.error = "Cannot test HTTP without healthy engines"
            else:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    for container_id, engine in healthy_engines:
                        engine_result = {
                            "container_id": container_id[:12],
                            "host": engine.host,
                            "port": engine.port,
                            "status": "unknown"
                        }
                        
                        try:
                            # Test /webui/api/service endpoint
                            url = f"http://{engine.host}:{engine.port}/webui/api/service?method=get_version"
                            response = await client.get(url)
                            response.raise_for_status()
                            data = response.json()
                            
                            engine_result["status"] = "success"
                            engine_result["version"] = data.get("result", {}).get("version", "unknown")
                            
                        except Exception as e:
                            engine_result["status"] = "failed"
                            engine_result["error"] = str(e)
                        
                        results.append(engine_result)
                
                test.details["engines_tested"] = results
                
                failed_count = sum(1 for r in results if r["status"] == "failed")
                if failed_count == 0:
                    test.status = "passed"
                    test.message = f"All {len(results)} engines responded successfully"
                elif failed_count == len(results):
                    test.status = "failed"
                    test.message = "All engines failed to respond"
                    test.error = "HTTP connectivity issue to all engines"
                else:
                    test.status = "warning"
                    test.message = f"{failed_count}/{len(results)} engines failed to respond"
                    
        except Exception as e:
            test.status = "failed"
            test.error = str(e)
            test.message = f"Error testing engine HTTP: {e}"
            logger.error(f"Engine HTTP test failed: {e}", exc_info=True)
        
        test.duration = time.time() - start
        self.tests.append(test)
    
    async def _test_acestream_getstream(self, ace_id: str):
        """Test AceStream getstream API with specific ID"""
        test = DiagnosticTest(
            f"acestream_getstream_{ace_id[:12]}",
            f"Test getstream API with AceStream ID {ace_id[:12]}..."
        )
        test.status = "running"
        test.timestamp = datetime.now(timezone.utc).isoformat()
        start = time.time()
        
        try:
            # Select an engine
            selector = EngineSelector()
            engine = await selector.select_best_engine()
            
            if not engine:
                test.status = "failed"
                test.message = "No engine available for testing"
                test.error = "Cannot select engine"
                test.duration = time.time() - start
                self.tests.append(test)
                return
            
            test.details["engine"] = {
                "container_id": engine["container_id"][:12],
                "host": engine["host"],
                "port": engine["port"],
                "is_forwarded": engine["is_forwarded"]
            }
            
            # Test getstream endpoint
            async with httpx.AsyncClient(timeout=30.0) as client:
                pid = str(uuid4())
                getstream_url = (
                    f"http://{engine['host']}:{engine['port']}/ace/getstream"
                    f"?id={ace_id}&format=json&pid={pid}"
                )
                
                test.details["request_url"] = getstream_url
                test.details["ace_id"] = ace_id
                
                try:
                    response = await client.get(getstream_url)
                    test.details["response_status"] = response.status_code
                    
                    response.raise_for_status()
                    data = response.json()
                    
                    test.details["response"] = data
                    
                    # Check for errors in response
                    if "error" in data and data["error"]:
                        test.status = "failed"
                        test.message = f"AceStream returned error: {data['error']}"
                        test.error = data["error"]
                    elif "response" in data:
                        resp = data["response"]
                        playback_url = resp.get("playback_url")
                        stat_url = resp.get("stat_url")
                        
                        test.details["playback_url"] = playback_url
                        test.details["stat_url"] = stat_url
                        test.details["playback_session_id"] = resp.get("playback_session_id")
                        test.details["is_live"] = resp.get("is_live")
                        
                        if playback_url:
                            test.status = "passed"
                            test.message = "Successfully got stream URLs from AceStream"
                        else:
                            test.status = "failed"
                            test.message = "No playback URL in response"
                            test.error = "Missing playback URL"
                    else:
                        test.status = "failed"
                        test.message = "Invalid response format from AceStream"
                        test.error = "Missing 'response' field in JSON"
                        
                except httpx.HTTPError as e:
                    test.status = "failed"
                    test.message = f"HTTP error calling getstream: {e}"
                    test.error = str(e)
                    
        except Exception as e:
            test.status = "failed"
            test.error = str(e)
            test.message = f"Error testing getstream: {e}"
            logger.error(f"Getstream test failed for {ace_id}: {e}", exc_info=True)
        
        test.duration = time.time() - start
        self.tests.append(test)
    
    async def _test_stream_manager_connection(self, ace_id: str):
        """Test full stream manager connection flow"""
        test = DiagnosticTest(
            f"stream_connection_{ace_id[:12]}",
            f"Test full stream connection for {ace_id[:12]}..."
        )
        test.status = "running"
        test.timestamp = datetime.now(timezone.utc).isoformat()
        start = time.time()
        
        try:
            # Select an engine
            selector = EngineSelector()
            engine = await selector.select_best_engine()
            
            if not engine:
                test.status = "failed"
                test.message = "No engine available for stream test"
                test.error = "Cannot select engine"
                test.duration = time.time() - start
                self.tests.append(test)
                return
            
            test.details["engine"] = {
                "container_id": engine["container_id"][:12],
                "host": engine["host"],
                "port": engine["port"]
            }
            
            # Import stream session components
            from .proxy.stream_session import StreamSession
            
            # Create a test session
            session = StreamSession(
                stream_id=f"diag_{ace_id}",
                ace_id=ace_id,
                engine_host=engine["host"],
                engine_port=engine["port"],
                container_id=engine["container_id"]
            )
            
            # Try to initialize
            init_start = time.time()
            success = await session.initialize()
            init_duration = time.time() - init_start
            
            test.details["initialization_time"] = init_duration
            test.details["session_initialized"] = success
            
            if success:
                test.details["playback_url"] = session.playback_url
                test.details["playback_session_id"] = session.playback_session_id
                test.details["is_active"] = session.is_active
                
                # Clean up the test session
                await session.cleanup()
                
                test.status = "passed"
                test.message = f"Stream connection successful (took {init_duration:.2f}s)"
            else:
                test.status = "failed"
                test.message = f"Stream initialization failed: {session.error}"
                test.error = session.error or "Unknown initialization error"
                test.details["error"] = session.error
                
                # Clean up anyway
                await session.cleanup()
                
        except Exception as e:
            test.status = "failed"
            test.error = str(e)
            test.message = f"Error testing stream connection: {e}"
            logger.error(f"Stream connection test failed for {ace_id}: {e}", exc_info=True)
        
        test.duration = time.time() - start
        self.tests.append(test)
    
    def _generate_report(self) -> Dict[str, Any]:
        """Generate final diagnostic report"""
        total_tests = len(self.tests)
        passed = sum(1 for t in self.tests if t.status == "passed")
        failed = sum(1 for t in self.tests if t.status == "failed")
        warnings = sum(1 for t in self.tests if t.status == "warning")
        
        return {
            "summary": {
                "total_tests": total_tests,
                "passed": passed,
                "failed": failed,
                "warnings": warnings,
                "duration": self.end_time - self.start_time if self.end_time else None,
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            "tests": [test.to_dict() for test in self.tests],
            "overall_status": "passed" if failed == 0 else "failed" if failed > 0 else "warning"
        }


# Singleton instance
_diagnostics_instance = None


def get_diagnostics() -> AceStreamDiagnostics:
    """Get singleton diagnostics instance"""
    global _diagnostics_instance
    if _diagnostics_instance is None:
        _diagnostics_instance = AceStreamDiagnostics()
    return _diagnostics_instance
