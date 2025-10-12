import threading
from typing import Tuple, Optional
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
        self._used_gluetun_ports: set[int] = set()

    def _parse(self, s: str) -> Tuple[int, int]:
        a, b = s.split("-")
        return int(a), int(b)

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

    def alloc_gluetun_port(self) -> int:
        """Allocate a port for Gluetun from the range starting at 19000."""
        with self._lock:
            # Check if we've reached the maximum number of active replicas
            if len(self._used_gluetun_ports) >= cfg.MAX_ACTIVE_REPLICAS:
                raise RuntimeError(f"Maximum active replicas limit reached ({cfg.MAX_ACTIVE_REPLICAS})")
            
            # Find the next available port starting from 19000
            for port in range(self._gluetun_port_base, self._gluetun_port_base + cfg.MAX_ACTIVE_REPLICAS):
                if port not in self._used_gluetun_ports:
                    self._used_gluetun_ports.add(port)
                    return port
            
            raise RuntimeError("No available ports in Gluetun port range")

    def reserve_gluetun_port(self, p: int):
        """Reserve a specific Gluetun port."""
        with self._lock:
            self._used_gluetun_ports.add(p)

    def free_gluetun_port(self, p: Optional[int]):
        """Free a Gluetun port."""
        if p is None: return
        with self._lock:
            self._used_gluetun_ports.discard(p)
    
    def clear_all_allocations(self):
        """Clear all port allocations. Used during cleanup to reset state."""
        with self._lock:
            self._used_host.clear()
            self._used_http.clear()
            self._used_https.clear()
            self._used_gluetun_ports.clear()

alloc = PortAllocator()
