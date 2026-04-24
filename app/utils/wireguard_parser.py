import re
from typing import Dict, List, Optional


_PRIVATE_KEY_RE = re.compile(r"^\s*PrivateKey\s*=\s*(?P<value>[^\r\n#;]+)", re.IGNORECASE | re.MULTILINE)
_ADDRESS_RE = re.compile(r"^\s*Address\s*=\s*(?P<value>[^\r\n#;]+)", re.IGNORECASE | re.MULTILINE)
_ENDPOINT_RE = re.compile(r"^\s*Endpoint\s*=\s*(?P<value>[^\r\n#;]+)", re.IGNORECASE | re.MULTILINE)
_PUBLIC_KEY_RE = re.compile(r"^\s*PublicKey\s*=\s*(?P<value>[^\r\n#;]+)", re.IGNORECASE | re.MULTILINE)
_PRESHARED_KEY_RE = re.compile(r"^\s*PresharedKey\s*=\s*(?P<value>[^\r\n#;]+)", re.IGNORECASE | re.MULTILINE)
_MTU_RE = re.compile(r"^\s*MTU\s*=\s*(?P<value>[^\r\n#;]+)", re.IGNORECASE | re.MULTILINE)


def _extract_first(pattern: re.Pattern[str], text: str) -> Optional[str]:
    match = pattern.search(text)
    if not match:
        return None
    value = match.group("value").strip()
    return value or None


def _split_csv_values(value: Optional[str]) -> List[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def parse_wireguard_conf(file_content: str) -> Dict[str, object]:
    """Parse key Wireguard values from .conf text using regex extraction."""
    if not isinstance(file_content, str):
        raise ValueError("file_content must be a string")

    private_key = _extract_first(_PRIVATE_KEY_RE, file_content)
    address_value = _extract_first(_ADDRESS_RE, file_content)
    endpoint = _extract_first(_ENDPOINT_RE, file_content)
    public_key = _extract_first(_PUBLIC_KEY_RE, file_content)
    preshared_key = _extract_first(_PRESHARED_KEY_RE, file_content)
    mtu = _extract_first(_MTU_RE, file_content)

    addresses = _split_csv_values(address_value)

    return {
        "private_key": private_key,
        "address": address_value,
        "addresses": addresses,
        "endpoint": endpoint,
        "public_key": public_key,
        "preshared_key": preshared_key,
        "mtu": mtu,
        "port_forwarding": True,
        "PrivateKey": private_key,
        "Address": address_value,
        "Endpoint": endpoint,
        "PublicKey": public_key,
        "PresharedKey": preshared_key,
        "MTU": mtu,
        "is_valid": bool(private_key and address_value and endpoint and public_key),
    }
