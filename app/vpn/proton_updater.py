from __future__ import annotations

import base64
import hashlib
import hmac
import importlib
import json
import logging
import os
import re
import shutil
import struct
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

APP_VERSION = "linux-vpn-cli@4.15.2"
USER_AGENT = "ProtonVPN/4.15.2 (Linux)"
LOGICALS_ENDPOINT = "/vpn/v1/logicals?SecureCoreFilter=all&WithIpV6=1"

# ISO-3166-1 alpha-2 → full country name mapping (mirrors Gluetun official servers.json)
_COUNTRY_CODES: Dict[str, str] = {
    "AD": "Andorra", "AE": "United Arab Emirates", "AF": "Afghanistan",
    "AG": "Antigua and Barbuda", "AL": "Albania", "AM": "Armenia",
    "AO": "Angola", "AR": "Argentina", "AT": "Austria", "AU": "Australia",
    "AZ": "Azerbaijan", "BA": "Bosnia and Herzegovina", "BB": "Barbados",
    "BD": "Bangladesh", "BE": "Belgium", "BF": "Burkina Faso",
    "BG": "Bulgaria", "BH": "Bahrain", "BI": "Burundi", "BJ": "Benin",
    "BN": "Brunei", "BO": "Bolivia", "BR": "Brazil", "BS": "Bahamas",
    "BT": "Bhutan", "BW": "Botswana", "BY": "Belarus", "BZ": "Belize",
    "CA": "Canada", "CD": "Democratic Republic of the Congo", "CF": "Central African Republic",
    "CG": "Republic of the Congo", "CH": "Switzerland", "CI": "Ivory Coast",
    "CL": "Chile", "CM": "Cameroon", "CN": "China", "CO": "Colombia",
    "CR": "Costa Rica", "CU": "Cuba", "CV": "Cape Verde", "CY": "Cyprus",
    "CZ": "Czech Republic", "DE": "Germany", "DJ": "Djibouti", "DK": "Denmark",
    "DM": "Dominica", "DO": "Dominican Republic", "DZ": "Algeria",
    "EC": "Ecuador", "EE": "Estonia", "EG": "Egypt", "ER": "Eritrea",
    "ES": "Spain", "ET": "Ethiopia", "FI": "Finland", "FJ": "Fiji",
    "FM": "Micronesia", "FR": "France", "GA": "Gabon", "GB": "United Kingdom",
    "GD": "Grenada", "GE": "Georgia", "GH": "Ghana", "GM": "Gambia",
    "GN": "Guinea", "GQ": "Equatorial Guinea", "GR": "Greece", "GT": "Guatemala",
    "GW": "Guinea-Bissau", "GY": "Guyana", "HN": "Honduras", "HR": "Croatia",
    "HT": "Haiti", "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland",
    "IL": "Israel", "IN": "India", "IQ": "Iraq", "IR": "Iran",
    "IS": "Iceland", "IT": "Italy", "JM": "Jamaica", "JO": "Jordan",
    "JP": "Japan", "KE": "Kenya", "KG": "Kyrgyzstan", "KH": "Cambodia",
    "KI": "Kiribati", "KM": "Comoros", "KN": "Saint Kitts and Nevis",
    "KP": "North Korea", "KR": "South Korea", "KW": "Kuwait",
    "KZ": "Kazakhstan", "LA": "Laos", "LB": "Lebanon", "LC": "Saint Lucia",
    "LI": "Liechtenstein", "LK": "Sri Lanka", "LR": "Liberia", "LS": "Lesotho",
    "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia", "LY": "Libya",
    "MA": "Morocco", "MC": "Monaco", "MD": "Moldova", "ME": "Montenegro",
    "MG": "Madagascar", "MH": "Marshall Islands", "MK": "North Macedonia",
    "ML": "Mali", "MM": "Myanmar", "MN": "Mongolia", "MR": "Mauritania",
    "MT": "Malta", "MU": "Mauritius", "MV": "Maldives", "MW": "Malawi",
    "MX": "Mexico", "MY": "Malaysia", "MZ": "Mozambique", "NA": "Namibia",
    "NE": "Niger", "NG": "Nigeria", "NI": "Nicaragua", "NL": "Netherlands",
    "NO": "Norway", "NP": "Nepal", "NR": "Nauru", "NZ": "New Zealand",
    "OM": "Oman", "PA": "Panama", "PE": "Peru", "PG": "Papua New Guinea",
    "PH": "Philippines", "PK": "Pakistan", "PL": "Poland", "PT": "Portugal",
    "PW": "Palau", "PY": "Paraguay", "QA": "Qatar", "RO": "Romania",
    "RS": "Serbia", "RU": "Russia", "RW": "Rwanda", "SA": "Saudi Arabia",
    "SB": "Solomon Islands", "SC": "Seychelles", "SD": "Sudan",
    "SE": "Sweden", "SG": "Singapore", "SI": "Slovenia", "SK": "Slovakia",
    "SL": "Sierra Leone", "SM": "San Marino", "SN": "Senegal", "SO": "Somalia",
    "SR": "Suriname", "SS": "South Sudan", "ST": "Sao Tome and Principe",
    "SV": "El Salvador", "SY": "Syria", "SZ": "Eswatini",
    "TD": "Chad", "TG": "Togo", "TH": "Thailand", "TJ": "Tajikistan",
    "TL": "Timor-Leste", "TM": "Turkmenistan", "TN": "Tunisia", "TO": "Tonga",
    "TR": "Turkey", "TT": "Trinidad and Tobago", "TV": "Tuvalu",
    "TZ": "Tanzania", "UA": "Ukraine", "UG": "Uganda",
    "US": "United States", "UY": "Uruguay", "UZ": "Uzbekistan",
    "VA": "Vatican City", "VC": "Saint Vincent and the Grenadines",
    "VE": "Venezuela", "VN": "Vietnam", "VU": "Vanuatu",
    "WS": "Samoa", "YE": "Yemen", "ZA": "South Africa",
    "ZM": "Zambia", "ZW": "Zimbabwe",
}


def _country_code_to_name(code: str) -> str:
    """Convert an ISO-3166-1 alpha-2 code to its full English name.

    Falls back to the original value when the code is not in the table so that
    unknown codes are stored verbatim rather than silently dropped.
    """
    if not code:
        return "Unknown"
    return _COUNTRY_CODES.get(code.strip().upper(), code.strip())


SECURE_CORE = 1 << 0
TOR = 1 << 1
P2P = 1 << 2
STREAMING = 1 << 3

_FILTER_VALUES = {"include", "exclude", "only"}
_JSON_MODE_VALUES = {"none", "replace", "update"}


@dataclass
class ProtonFilterConfig:
    ipv6: str = "exclude"
    secure_core: str = "include"
    tor: str = "include"
    free_tier: str = "include"

    def validate(self) -> None:
        for key, value in (
            ("ipv6", self.ipv6),
            ("secure_core", self.secure_core),
            ("tor", self.tor),
            ("free_tier", self.free_tier),
        ):
            if value not in _FILTER_VALUES:
                raise ValueError(f"Invalid filter '{key}': {value}. Allowed values: include|exclude|only")


class ProtonServerUpdater:
    """Fetch Proton paid server data and write Gluetun-compatible servers JSON."""

    def __init__(self, storage_path: Optional[str] = None):
        self._storage_path = self._resolve_storage_path(storage_path)

    @staticmethod
    def _resolve_storage_path(storage_path: Optional[str]) -> Path:
        configured = str(storage_path or "").strip()
        if not configured:
            configured = str(os.getenv("GLUETUN_SERVERS_JSON_PATH", "")).strip()

        if configured:
            path = Path(configured)
            if path.suffix:
                return path.parent
            return path

        # Repo default: write beside the project's servers.json file.
        return Path(__file__).resolve().parents[2]

    @staticmethod
    def _read_secret(name: str) -> Optional[str]:
        try:
            value = Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()
            return value or None
        except OSError:
            return None

    @staticmethod
    def _import_proton_types() -> Tuple[Any, Any]:
        try:
            session_mod = importlib.import_module("proton.session")
            exceptions_mod = importlib.import_module("proton.session.exceptions")
        except Exception as exc:
            raise RuntimeError(
                "Missing proton-core dependency. Install it with: "
                "pip install 'proton-core @ git+https://github.com/ProtonVPN/python-proton-core.git'"
            ) from exc

        session_cls = getattr(session_mod, "Session", None)
        two_fa_exc = getattr(exceptions_mod, "ProtonAPI2FANeeded", None)
        auth_exc = getattr(exceptions_mod, "ProtonAPIAuthenticationNeeded", Exception)

        if session_cls is None or two_fa_exc is None:
            raise RuntimeError("proton-core is installed but expected Session/ProtonAPI2FANeeded symbols were not found")

        return session_cls, (two_fa_exc, auth_exc)

    @staticmethod
    def _check_gpg_available() -> bool:
        """Check if the 'gpg' (GnuPG) binary is present in the system PATH."""
        return bool(shutil.which("gpg"))

    def _ensure_gpg_available(self) -> None:
        """Verify that gnupg is available, raising an error with guidance if not."""
        if not self._check_gpg_available():
            raise RuntimeError(
                "The 'gpg' (GnuPG) binary was not found in the system PATH. "
                "The Proton API integration requires gpg for secure authentication. "
                "Please install gnupg (gpg) in the container/host environment."
            )

    @staticmethod
    def _country_name(value: Any) -> str:
        """Convert a country code or name to a full English country name."""
        text = str(value or "").strip()
        if not text:
            return "Unknown"
        # If it looks like an ISO-2 code, expand it; otherwise keep as-is.
        if len(text) == 2 and text.isalpha():
            return _country_code_to_name(text)
        return text

    @staticmethod
    def _coerce_int(value: Any, default: int = 0) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_token(token: Optional[str]) -> Optional[str]:
        if token is None:
            return None
        normalized = "".join(str(token).split())
        if not normalized:
            return None
        if not re.fullmatch(r"\d{6,8}", normalized):
            raise ValueError("2FA token/code must be 6-8 digits")
        return normalized

    @staticmethod
    def _generate_totp_from_secret(secret: str, digits: int = 6, period: int = 30) -> str:
        normalized = str(secret or "").strip().replace(" ", "")
        if not normalized:
            raise ValueError("2FA secret is empty")

        try:
            key = base64.b32decode(normalized.upper(), casefold=True)
        except Exception as exc:
            raise ValueError("2FA secret is not valid base32") from exc

        counter = int(time.time()) // period
        counter_bytes = struct.pack(">Q", counter)
        digest = hmac.new(key, counter_bytes, hashlib.sha1).digest()
        offset = digest[-1] & 0x0F
        code = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
        return str(code % (10 ** digits)).zfill(digits)

    @staticmethod
    def _matches_filter(flag: bool, mode: str) -> bool:
        if mode == "include":
            return True
        if mode == "only":
            return flag
        if mode == "exclude":
            return not flag
        return True

    def _transform_to_gluetun(
        self,
        api_data: Dict[str, Any],
        filters: ProtonFilterConfig,
    ) -> Tuple[Dict[str, Any], Dict[str, int]]:
        logicals = api_data.get("LogicalServers")
        if not isinstance(logicals, list):
            raise ValueError("Unexpected Proton API payload: missing LogicalServers list")

        servers: List[Dict[str, Any]] = []
        seen_ips: set[str] = set()
        physical_total = 0

        for logical in logicals:
            if not isinstance(logical, dict):
                continue

            features = self._coerce_int(logical.get("Features"), 0)
            tier = self._coerce_int(logical.get("Tier"), 1)

            is_secure_core = bool(features & SECURE_CORE)
            is_tor = bool(features & TOR)
            is_p2p = bool(features & P2P)
            is_streaming = bool(features & STREAMING)
            is_free = tier == 0

            if not self._matches_filter(is_secure_core, filters.secure_core):
                continue
            if not self._matches_filter(is_tor, filters.tor):
                continue
            if not self._matches_filter(is_free, filters.free_tier):
                continue

            physical_nodes = logical.get("Servers")
            if not isinstance(physical_nodes, list):
                continue

            for physical in physical_nodes:
                if not isinstance(physical, dict):
                    continue

                domain = str(physical.get("Domain") or "").strip().lower()
                entry_ip = str(physical.get("EntryIP") or "").strip()
                if not domain or not entry_ip:
                    continue

                physical_total += 1

                # Match gluetun updater behavior: dedupe non-secure-core by entry IP.
                if not is_secure_core and entry_ip in seen_ips:
                    continue
                if not is_secure_core:
                    seen_ips.add(entry_ip)

                entry_ipv6 = str(physical.get("EntryIPv6") or "").strip()
                has_ipv6 = bool(entry_ipv6)
                if filters.ipv6 == "only" and not has_ipv6:
                    continue

                country = self._country_name(
                    logical.get("ExitCountry")
                    or logical.get("EntryCountry")
                    or logical.get("Country")
                )
                city = str(logical.get("City") or "").strip()
                server_name = str(logical.get("Name") or domain).strip()

                ips: List[str] = [entry_ip]
                if has_ipv6 and filters.ipv6 in {"include", "only"}:
                    ips.append(entry_ipv6)

                common: Dict[str, Any] = {
                    "country": country,
                    "city": city,
                    "server_name": server_name,
                    "hostname": domain,
                    "ips": ips,
                }

                if is_free:
                    common["free"] = True
                if is_streaming:
                    common["stream"] = True
                if is_secure_core:
                    common["secure_core"] = True
                if is_tor:
                    common["tor"] = True
                if is_p2p:
                    common["port_forward"] = True

                openvpn_entry = {
                    "vpn": "openvpn",
                    "tcp": True,
                    "udp": True,
                    **common,
                }
                servers.append(openvpn_entry)

                wg_pubkey = str(physical.get("X25519PublicKey") or "").strip()
                if wg_pubkey:
                    wireguard_entry = {
                        "vpn": "wireguard",
                        "wgpubkey": wg_pubkey,
                        **common,
                    }
                    servers.append(wireguard_entry)

        payload = {
            "version": 1,
            "protonvpn": {
                "version": 4,
                "timestamp": int(time.time()),
                "servers": servers,
            },
        }

        stats = {
            "logical_total": len(logicals),
            "physical_total": physical_total,
            "output_servers": len(servers),
        }
        return payload, stats

    @staticmethod
    def _atomic_write_json(path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(payload, indent=2)

        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(path.parent), delete=False) as temp_file:
            temp_file.write(data)
            temp_name = temp_file.name

        os.replace(temp_name, path)

    @staticmethod
    def _load_existing_servers_json(path: Path) -> Dict[str, Any]:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except FileNotFoundError:
            pass
        except json.JSONDecodeError:
            logger.warning("Failed to parse existing servers file at %s; creating fresh structure", path)
        return {"version": 1}

    async def update(
        self,
        *,
        proton_username: Optional[str] = None,
        proton_password: Optional[str] = None,
        proton_totp_code: Optional[str] = None,
        proton_totp_secret: Optional[str] = None,
        filters: Optional[ProtonFilterConfig] = None,
        gluetun_json_mode: str = "update",
    ) -> Dict[str, Any]:
        """
        Fetch Proton servers and update local cache files.

        - Always writes servers-proton.json.
        - Writes servers.json when mode is update or replace.
        """
        mode = str(gluetun_json_mode or "").strip().lower()
        if mode not in _JSON_MODE_VALUES:
            raise ValueError("gluetun_json_mode must be one of: none|replace|update")

        applied_filters = filters or ProtonFilterConfig()
        applied_filters.validate()

        username = (
            str(proton_username or "").strip()
            or str(os.getenv("PROTON_USERNAME", "")).strip()
            or str(self._read_secret("proton_username") or "").strip()
        )
        password = (
            str(proton_password or "").strip()
            or str(os.getenv("PROTON_PASSWORD", "")).strip()
            or str(self._read_secret("proton_password") or "").strip()
        )

        if not username or not password:
            raise ValueError("Missing Proton credentials: set proton_username/proton_password or env/secret values")

        one_time_code = proton_totp_code or os.getenv("PROTON_TOTP_CODE") or self._read_secret("proton_totp_code")
        code = self._normalize_token(one_time_code)
        if not code:
            secret = proton_totp_secret or os.getenv("PROTON_TOTP_SECRET") or self._read_secret("proton_totp_secret")
            if secret:
                code = self._generate_totp_from_secret(secret)

        self._ensure_gpg_available()
        Session, exception_types = self._import_proton_types()
        two_fa_exc = exception_types[0]

        masked_user = (username[:3] + "***") if len(username) > 3 else "***"
        logger.debug(
            "Proton refresh: authenticating as user=%s storage=%s",
            masked_user,
            self._storage_path,
        )

        try:
            session = Session(appversion=APP_VERSION, user_agent=USER_AGENT)
        except Exception as exc:
            raise RuntimeError(
                f"Failed to initialize Proton session (GPG/Keyring issue). "
                f"Ensure gpg is working and the storage path '{self._storage_path}' is writable. "
                f"Error: {exc}"
            ) from exc

        try:
            try:
                authenticated = await session.async_authenticate(username, password)
                if not authenticated:
                    raise RuntimeError("Proton authentication failed")

                try:
                    api_data = await session.async_api_request(LOGICALS_ENDPOINT)
                except two_fa_exc:
                    if not code:
                        raise RuntimeError("2FA is required. Provide proton_totp_code or proton_totp_secret")
                    validated = await session.async_validate_2fa_code(code)
                    if not validated:
                        raise RuntimeError("2FA token rejected by Proton API")
                    api_data = await session.async_api_request(LOGICALS_ENDPOINT)
            except OSError as exc:
                raise RuntimeError(
                    f"Proton refresh failed because gpg/gnupg is unavailable or misconfigured "
                    f"in this runtime (user={masked_user}, storage={self._storage_path}). "
                    f"Underlying error: {exc}"
                ) from exc

            proton_payload, stats = self._transform_to_gluetun(api_data, applied_filters)

            self._storage_path.mkdir(parents=True, exist_ok=True)
            proton_file = self._storage_path / "servers-proton.json"
            self._atomic_write_json(proton_file, proton_payload)

            merged_file = self._storage_path / "servers.json"
            if mode == "replace":
                self._atomic_write_json(merged_file, proton_payload)
            elif mode == "update":
                existing = self._load_existing_servers_json(merged_file)
                existing.setdefault("version", 1)
                existing["protonvpn"] = proton_payload["protonvpn"]
                self._atomic_write_json(merged_file, existing)

            return {
                "storage_path": str(self._storage_path),
                "servers_proton_file": str(proton_file),
                "servers_file": str(merged_file),
                "gluetun_json_mode": mode,
                "filters": {
                    "ipv6": applied_filters.ipv6,
                    "secure_core": applied_filters.secure_core,
                    "tor": applied_filters.tor,
                    "free_tier": applied_filters.free_tier,
                },
                "stats": stats,
                "totp_used": bool(code),
            }
        finally:
            try:
                await session.async_logout()
            except Exception:
                pass
