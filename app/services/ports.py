import threading
from typing import Tuple, Optional, Dict
from ..core.config import cfg

class PortAllocator:
    def __init__(self):
        self._lock = threading.RLock()
        self._host_min, self._host_max = self._parse(cfg.PORT_RANGE_HOST)
        self._http_min, self._http_max = self._parse(cfg.ACE_HTTP_RANGE)
        self._https_min, self._https_max = self._parse(cfg.ACE_HTTPS_RANGE)
        self._host_next = self._host_min
        self._http_next = self._http_min
        self._https_next = self._https_min
        self._used_host: set[int] = set()
        self._used_http: set[int] = set()
        self._used_https: set[int] = set()
        
        # Gluetun-specific port allocation starting from 19000
        self._gluetun_port_base = 19000
        self._gluetun_min = max(self._gluetun_port_base, self._host_min)
        self._gluetun_max = self._host_max
        self._gluetun_next = self._gluetun_min
        self._used_gluetun_ports: set[int] = set()
        
        # VPN-specific port ranges for redundant mode
        # Maps VPN container name to (min_port, max_port, next_port, used_ports_set)
        self._vpn_port_ranges: Dict[str, Tuple[int, int, int, set[int]]] = {}
        self._init_vpn_port_ranges()

    def _parse(self, s: str) -> Tuple[int, int]:
        a, b = s.split("-")
        return int(a), int(b)
    
    def _init_vpn_port_ranges(self):
        """Initialize VPN-specific port ranges for redundant mode."""
        import logging
        logger = logging.getLogger(__name__)
        
        # Set up port ranges for each VPN if configured
        if cfg.GLUETUN_CONTAINER_NAME and cfg.GLUETUN_PORT_RANGE_1:
            try:
                min_port, max_port = self._parse(cfg.GLUETUN_PORT_RANGE_1)
                self._vpn_port_ranges[cfg.GLUETUN_CONTAINER_NAME] = (min_port, max_port, min_port, set())
                logger.info(f"VPN port range for {cfg.GLUETUN_CONTAINER_NAME}: {min_port}-{max_port}")
            except (ValueError, AttributeError) as e:
                logger.error(f"Invalid GLUETUN_PORT_RANGE_1 format '{cfg.GLUETUN_PORT_RANGE_1}': {e}. Expected format: 'min-max'")
        
        if cfg.GLUETUN_CONTAINER_NAME_2 and cfg.GLUETUN_PORT_RANGE_2:
            try:
                min_port, max_port = self._parse(cfg.GLUETUN_PORT_RANGE_2)
                self._vpn_port_ranges[cfg.GLUETUN_CONTAINER_NAME_2] = (min_port, max_port, min_port, set())
                logger.info(f"VPN port range for {cfg.GLUETUN_CONTAINER_NAME_2}: {min_port}-{max_port}")
            except (ValueError, AttributeError) as e:
                logger.error(f"Invalid GLUETUN_PORT_RANGE_2 format '{cfg.GLUETUN_PORT_RANGE_2}': {e}. Expected format: 'min-max'")

    def _next_in(self, cur: int, lo: int, hi: int, used: set[int]) -> int:
        p = cur
        for _ in range(hi - lo + 1):
            if p > hi:
                p = lo
            if p not in used:
                return p
            p += 1
        raise RuntimeError("no free ports in range")

    def alloc_host(self) -> int:
        with self._lock:
            p = self._next_in(self._host_next, self._host_min, self._host_max, self._used_host)
            self._used_host.add(p)
            self._host_next = p + 1
            return p

    def alloc_http(self) -> int:
        with self._lock:
            p = self._next_in(self._http_next, self._http_min, self._http_max, self._used_http)
            self._used_http.add(p)
            self._http_next = p + 1
            return p

    def alloc_https(self, avoid: Optional[int] = None) -> int:
        with self._lock:
            while True:
                p = self._next_in(self._https_next, self._https_min, self._https_max, self._used_https)
                if avoid is None or p != avoid:
                    self._used_https.add(p)
                    self._https_next = p + 1
                    return p
                self._https_next = p + 1

    def reserve_host(self, p: int):
        with self._lock:
            self._used_host.add(p)

    def reserve_http(self, p: int):
        with self._lock:
            self._used_http.add(p)

    def reserve_https(self, p: int):
        with self._lock:
            self._used_https.add(p)

    def free_host(self, p: Optional[int]):
        if p is None: return
        with self._lock:
            self._used_host.discard(p)

    def free_http(self, p: Optional[int]):
        if p is None: return
        with self._lock:
            self._used_http.discard(p)

    def free_https(self, p: Optional[int]):
        if p is None: return
        with self._lock:
            self._used_https.discard(p)

    def allocate_engine_ports(
        self,
        *,
        use_gluetun: bool,
        vpn_container: Optional[str],
        requested_host_port: Optional[int],
        user_http_port: Optional[int],
        user_https_port: Optional[int],
        user_api_port: Optional[int],
        map_https: bool,
    ) -> Dict[str, Optional[int]]:
        """Atomically reserve all ports required for a new engine.

        Returns a dictionary with container and host ports.
        If any allocation fails, all partial reservations are rolled back.
        """

        with self._lock:
            reservations: list[tuple[str, int, Optional[str]]] = []

            def _track(kind: str, port: int, vpn: Optional[str] = None):
                reservations.append((kind, port, vpn))

            def _rollback():
                for kind, port, vpn in reversed(reservations):
                    if kind == "host":
                        self.free_host(port)
                    elif kind == "http":
                        self.free_http(port)
                    elif kind == "https":
                        self.free_https(port)
                    elif kind == "gluetun":
                        self.free_gluetun_port(port, vpn)

            try:
                if user_http_port is not None:
                    c_http = user_http_port
                    host_http = requested_host_port or user_http_port
                    if use_gluetun:
                        self.reserve_gluetun_port(c_http, vpn_container)
                        _track("gluetun", c_http, vpn_container)
                    else:
                        self.reserve_http(c_http)
                        _track("http", c_http)
                else:
                    if use_gluetun:
                        host_http = self.alloc_gluetun_port(vpn_container)
                        _track("gluetun", host_http, vpn_container)
                        c_http = host_http
                    else:
                        host_http = requested_host_port or self.alloc_host()
                        if requested_host_port is None:
                            _track("host", host_http)
                        c_http = host_http
                        self.reserve_http(c_http)
                        _track("http", c_http)

                if user_https_port is not None:
                    c_https = user_https_port
                    self.reserve_https(c_https)
                    _track("https", c_https)
                else:
                    c_https = self.alloc_https(avoid=c_http)
                    _track("https", c_https)

                if user_api_port is not None:
                    c_api = user_api_port
                    host_api = user_api_port
                    if use_gluetun:
                        self.reserve_gluetun_port(host_api, vpn_container)
                        _track("gluetun", host_api, vpn_container)
                    else:
                        self.reserve_host(host_api)
                        _track("host", host_api)
                else:
                    if use_gluetun:
                        host_api = self.alloc_gluetun_port(vpn_container)
                        _track("gluetun", host_api, vpn_container)
                    else:
                        host_api = self.alloc_host()
                        _track("host", host_api)
                    c_api = host_api

                host_https = None
                if not use_gluetun and map_https:
                    host_https = self.alloc_host()
                    _track("host", host_https)

                return {
                    "container_http_port": c_http,
                    "container_https_port": c_https,
                    "container_api_port": c_api,
                    "host_http_port": host_http,
                    "host_api_port": host_api,
                    "host_https_port": host_https,
                }
            except Exception:
                _rollback()
                raise

    def alloc_gluetun_port(self, vpn_container: Optional[str] = None) -> int:
        """
        Allocate a port for Gluetun from the appropriate range.
        
        Args:
            vpn_container: VPN container name. If provided and VPN-specific ranges are configured,
                          allocates from that VPN's range. Otherwise uses global allocation.
        
        Returns:
            Allocated port number
        """
        with self._lock:
            # If VPN-specific range is configured, use it
            if vpn_container and vpn_container in self._vpn_port_ranges:
                min_port, max_port, next_port, used_ports = self._vpn_port_ranges[vpn_container]
                
                # Find next available port in this VPN's range
                port = self._next_in(next_port, min_port, max_port, used_ports)
                used_ports.add(port)
                
                # Update next_port for this VPN
                self._vpn_port_ranges[vpn_container] = (min_port, max_port, port + 1, used_ports)
                return port
            
            # Fallback to global allocation (backwards compatibility)
            if self._gluetun_min > self._gluetun_max:
                raise RuntimeError("Invalid Gluetun fallback port range")

            port = self._next_in(self._gluetun_next, self._gluetun_min, self._gluetun_max, self._used_gluetun_ports)
            self._used_gluetun_ports.add(port)
            self._gluetun_next = port + 1
            return port

    def reserve_gluetun_port(self, p: int, vpn_container: Optional[str] = None):
        """Reserve a specific Gluetun port."""
        with self._lock:
            # If VPN-specific range is configured, reserve in that range
            if vpn_container and vpn_container in self._vpn_port_ranges:
                min_port, max_port, next_port, used_ports = self._vpn_port_ranges[vpn_container]
                used_ports.add(p)
                self._vpn_port_ranges[vpn_container] = (min_port, max_port, next_port, used_ports)
            else:
                # Fallback to global reservation
                self._used_gluetun_ports.add(p)

    def free_gluetun_port(self, p: Optional[int], vpn_container: Optional[str] = None):
        """Free a Gluetun port."""
        if p is None: return
        with self._lock:
            # If VPN-specific range is configured, free from that range
            if vpn_container and vpn_container in self._vpn_port_ranges:
                min_port, max_port, next_port, used_ports = self._vpn_port_ranges[vpn_container]
                used_ports.discard(p)
                self._vpn_port_ranges[vpn_container] = (min_port, max_port, next_port, used_ports)
            else:
                # Fallback to global freeing
                self._used_gluetun_ports.discard(p)
    
    def clear_all_allocations(self):
        """Clear all port allocations. Used during cleanup to reset state."""
        with self._lock:
            self._used_host.clear()
            self._used_http.clear()
            self._used_https.clear()
            self._used_gluetun_ports.clear()
            self._gluetun_next = self._gluetun_min
            
            # Clear VPN-specific port allocations
            for vpn_name in self._vpn_port_ranges:
                min_port, max_port, next_port, used_ports = self._vpn_port_ranges[vpn_name]
                used_ports.clear()
                self._vpn_port_ranges[vpn_name] = (min_port, max_port, min_port, used_ports)

alloc = PortAllocator()
