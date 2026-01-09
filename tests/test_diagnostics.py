"""
Test the diagnostics endpoint functionality.
"""

import pytest
import asyncio
from app.services.diagnostics import AceStreamDiagnostics, DiagnosticTest


@pytest.mark.asyncio
async def test_diagnostic_test_to_dict():
    """Test DiagnosticTest serialization."""
    test = DiagnosticTest("test_name", "Test Description")
    test.status = "passed"
    test.message = "Test passed successfully"
    test.details = {"key": "value"}
    test.duration = 1.5
    
    result = test.to_dict()
    
    assert result["name"] == "test_name"
    assert result["description"] == "Test Description"
    assert result["status"] == "passed"
    assert result["message"] == "Test passed successfully"
    assert result["details"]["key"] == "value"
    assert result["duration"] == 1.5


@pytest.mark.asyncio
async def test_diagnostics_initialization():
    """Test diagnostics service initialization."""
    diagnostics = AceStreamDiagnostics()
    
    assert diagnostics.tests == []
    assert diagnostics.start_time is None
    assert diagnostics.end_time is None


@pytest.mark.asyncio
async def test_diagnostics_report_generation():
    """Test diagnostic report generation."""
    diagnostics = AceStreamDiagnostics()
    diagnostics.start_time = 1000.0
    diagnostics.end_time = 1005.0
    
    # Add some test results
    test1 = DiagnosticTest("test1", "Test 1")
    test1.status = "passed"
    diagnostics.tests.append(test1)
    
    test2 = DiagnosticTest("test2", "Test 2")
    test2.status = "failed"
    diagnostics.tests.append(test2)
    
    test3 = DiagnosticTest("test3", "Test 3")
    test3.status = "warning"
    diagnostics.tests.append(test3)
    
    report = diagnostics._generate_report()
    
    assert report["summary"]["total_tests"] == 3
    assert report["summary"]["passed"] == 1
    assert report["summary"]["failed"] == 1
    assert report["summary"]["warnings"] == 1
    assert report["summary"]["duration"] == 5.0
    assert report["overall_status"] == "failed"  # Because there's at least one failure
    assert len(report["tests"]) == 3


@pytest.mark.asyncio
async def test_diagnostics_available_engines_no_engines():
    """Test available engines diagnostic when no engines exist."""
    from app.services.state import state
    
    # Clear engines
    state.engines.clear()
    
    diagnostics = AceStreamDiagnostics()
    await diagnostics._test_available_engines()
    
    assert len(diagnostics.tests) == 1
    test = diagnostics.tests[0]
    assert test.name == "available_engines"
    assert test.status == "failed"
    assert "No engines found" in test.message


@pytest.mark.asyncio 
async def test_diagnostics_report_structure():
    """Test that full diagnostic report has correct structure."""
    diagnostics = AceStreamDiagnostics()
    
    # Run with empty test IDs (will use placeholder)
    report = await diagnostics.run_full_diagnostics([])
    
    # Check report structure
    assert "summary" in report
    assert "tests" in report
    assert "overall_status" in report
    
    # Check summary structure
    assert "total_tests" in report["summary"]
    assert "passed" in report["summary"]
    assert "failed" in report["summary"]
    assert "warnings" in report["summary"]
    assert "duration" in report["summary"]
    assert "timestamp" in report["summary"]
    
    # Check that at least basic tests ran
    assert report["summary"]["total_tests"] > 0
    assert isinstance(report["tests"], list)
    
    # Verify test structure
    for test in report["tests"]:
        assert "name" in test
        assert "description" in test
        assert "status" in test
        assert test["status"] in ["passed", "failed", "warning", "pending", "running"]


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
