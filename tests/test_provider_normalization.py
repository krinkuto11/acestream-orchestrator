"""
Test VPN Provider Normalization

Tests the normalize_provider_name function to ensure it correctly maps
lowercase provider names from docker env to proper capitalization.
"""

import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.services.gluetun import normalize_provider_name


def test_provider_normalization():
    """Test that provider names are correctly normalized."""
    
    test_cases = [
        # (input, expected_output)
        ("protonvpn", "ProtonVPN"),
        ("nordvpn", "NordVPN"),
        ("expressvpn", "ExpressVPN"),
        ("mullvad", "Mullvad"),
        ("surfshark", "Surfshark"),
        ("cyberghost", "Cyberghost"),
        ("privatevpn", "PrivateVPN"),
        ("ipvanish", "IPVanish"),
        ("ivpn", "IVPN"),
        ("hidemyass", "HideMyAss"),
        ("windscribe", "Windscribe"),
        ("purevpn", "PureVPN"),
        ("vyprvpn", "Vyprvpn"),
        ("torguard", "TorGuard"),
        ("private internet access", "Private Internet Access"),
        ("pia", "Private Internet Access"),
        ("vpnsecure.me", "VPNSecure.me"),
        ("vpnsecure", "VPNSecure.me"),
        ("vpnunlimited", "VPNUnlimited"),
        ("perfect privacy", "Perfect Privacy"),
        ("perfectprivacy", "Perfect Privacy"),
        ("wevpn", "WeVPN"),
        ("privado", "Privado"),
        ("airvpn", "AirVPN"),
        ("fastestvpn", "FastestVPN"),
        ("giganews", "Giganews"),
        ("slickvpn", "SlickVPN"),
        # Test uppercase and mixed case
        ("PROTONVPN", "ProtonVPN"),
        ("ProtonVPN", "ProtonVPN"),
        ("NordVPN", "NordVPN"),
        # Test with extra whitespace
        ("  protonvpn  ", "ProtonVPN"),
        # Test unknown provider (should return title case)
        ("unknownprovider", "Unknownprovider"),
    ]
    
    passed = 0
    failed = 0
    
    for input_name, expected in test_cases:
        result = normalize_provider_name(input_name)
        if result == expected:
            print(f"✓ '{input_name}' -> '{result}'")
            passed += 1
        else:
            print(f"✗ '{input_name}' -> '{result}' (expected '{expected}')")
            failed += 1
    
    print(f"\nResults: {passed} passed, {failed} failed")
    
    if failed > 0:
        print("\n❌ Some tests failed!")
        return False
    else:
        print("\n✅ All tests passed!")
        return True


if __name__ == "__main__":
    success = test_provider_normalization()
    sys.exit(0 if success else 1)
