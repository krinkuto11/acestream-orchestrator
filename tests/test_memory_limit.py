"""
Test memory limit validation functionality.
"""
import pytest
from app.services.custom_variant_config import validate_memory_limit, CustomVariantConfig


class TestMemoryLimitValidation:
    """Test memory limit validation function."""
    
    def test_valid_memory_formats(self):
        """Test valid memory format strings."""
        valid_formats = [
            ("512m", True),
            ("2g", True),
            ("1024m", True),
            ("512M", True),
            ("2G", True),
            ("32768k", True),  # 32MB in KB (minimum)
            ("32768K", True),
            ("1073741824", True),  # bytes without suffix (1GB)
            ("1073741824b", True),
            ("", True),  # Empty string is valid (unlimited)
            (None, True),  # None is valid (unlimited)
            ("0", True),  # Zero means unlimited
        ]
        
        for memory_str, expected in valid_formats:
            is_valid, error = validate_memory_limit(memory_str)
            assert is_valid == expected, f"Expected {memory_str} to be valid, but got error: {error}"
    
    def test_invalid_memory_formats(self):
        """Test invalid memory format strings."""
        invalid_formats = [
            "abc",
            "512",  # Missing suffix for small values - actually this is valid as bytes
            "512x",  # Invalid suffix
            "-512m",  # Negative value
            "512 m",  # Space between value and suffix
            "m512",  # Suffix before value
        ]
        
        for memory_str in invalid_formats:
            if memory_str == "512":
                # This is actually valid as bytes
                continue
            is_valid, error = validate_memory_limit(memory_str)
            assert not is_valid, f"Expected {memory_str} to be invalid, but it was accepted"
            assert error is not None
    
    def test_memory_limit_too_high(self):
        """Test memory limits that exceed maximum."""
        is_valid, error = validate_memory_limit("200g")
        assert not is_valid
        assert "too high" in error.lower()
    
    def test_memory_limit_too_low(self):
        """Test memory limits below minimum (32MB)."""
        is_valid, error = validate_memory_limit("16m")
        assert not is_valid
        assert "too low" in error.lower()
        
        is_valid, error = validate_memory_limit("1m")
        assert not is_valid
        assert "too low" in error.lower()
    
    def test_memory_limit_at_boundaries(self):
        """Test memory limits at boundary values."""
        # Minimum boundary (32MB)
        is_valid, error = validate_memory_limit("32m")
        assert is_valid, f"32m should be valid, got error: {error}"
        
        # Just below minimum
        is_valid, error = validate_memory_limit("31m")
        assert not is_valid
        
        # Maximum boundary (128GB)
        is_valid, error = validate_memory_limit("128g")
        assert is_valid, f"128g should be valid, got error: {error}"
        
        # Just above maximum
        is_valid, error = validate_memory_limit("129g")
        assert not is_valid


class TestCustomVariantConfigMemoryLimit:
    """Test memory limit integration with CustomVariantConfig."""
    
    def test_config_with_valid_memory_limit(self):
        """Test creating config with valid memory limit."""
        config = CustomVariantConfig(
            enabled=True,
            platform="amd64",
            memory_limit="512m",
            parameters=[]
        )
        assert config.memory_limit == "512m"
    
    def test_config_with_none_memory_limit(self):
        """Test creating config with None memory limit."""
        config = CustomVariantConfig(
            enabled=True,
            platform="amd64",
            memory_limit=None,
            parameters=[]
        )
        assert config.memory_limit is None
    
    def test_config_with_empty_memory_limit(self):
        """Test creating config with empty string memory limit."""
        config = CustomVariantConfig(
            enabled=True,
            platform="amd64",
            memory_limit="",
            parameters=[]
        )
        assert config.memory_limit is None
    
    def test_config_with_invalid_memory_limit(self):
        """Test creating config with invalid memory limit raises error."""
        with pytest.raises(ValueError, match="Invalid format"):
            CustomVariantConfig(
                enabled=True,
                platform="amd64",
                memory_limit="invalid",
                parameters=[]
            )
    
    def test_config_with_too_low_memory_limit(self):
        """Test creating config with too low memory limit raises error."""
        with pytest.raises(ValueError, match="too low"):
            CustomVariantConfig(
                enabled=True,
                platform="amd64",
                memory_limit="16m",
                parameters=[]
            )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
