#!/usr/bin/env python3
"""Regression tests for per-engine ingress hold release behavior."""

import os
import sys
import time
from types import SimpleNamespace

# Add app to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


def test_engine_ingress_hold_drops_immediately_when_no_active_engine_bytes(monkeypatch):
    """
    Per-engine ingress hold should not keep stale rates when no active streams
    map to engines anymore (stream just ended).
    """
    from app.services import metrics
    from app.proxy import manager as proxy_manager_module
    from app.proxy import hls_proxy as hls_proxy_module

    class DummyProxyManager:
        @staticmethod
        def get_instance():
            return SimpleNamespace(redis_client=None)

    class DummyHlsProxyServer:
        @staticmethod
        def get_instance():
            return SimpleNamespace(client_managers={})

    monkeypatch.setattr(proxy_manager_module, "ProxyManager", DummyProxyManager)
    monkeypatch.setattr(hls_proxy_module, "HLSProxyServer", DummyHlsProxyServer)

    with metrics._engine_ingress_lock:
        prev_last_bytes = dict(metrics._last_engine_ingress_bytes)
        prev_last_sample_ts = metrics._last_engine_ingress_sample_ts
        prev_rate_map = dict(metrics._engine_ingress_rate_bps)
        prev_last_rate_map = dict(metrics._last_engine_ingress_rate_bps)
        prev_last_rate_ts = metrics._last_engine_rate_ts

        # Seed previous non-zero state so old behavior would hold stale values.
        metrics._last_engine_ingress_bytes = {"engine-a": 1_000_000}
        metrics._last_engine_ingress_sample_ts = time.time() - 1.0
        metrics._engine_ingress_rate_bps = {"engine-a": 5000.0}
        metrics._last_engine_ingress_rate_bps = {"engine-a": 5000.0}
        metrics._last_engine_rate_ts = time.time()

    try:
        snapshot = metrics._compute_per_engine_ingress_snapshot()
        assert snapshot == {}

        with metrics._engine_ingress_lock:
            assert metrics._engine_ingress_rate_bps == {}
    finally:
        with metrics._engine_ingress_lock:
            metrics._last_engine_ingress_bytes = prev_last_bytes
            metrics._last_engine_ingress_sample_ts = prev_last_sample_ts
            metrics._engine_ingress_rate_bps = prev_rate_map
            metrics._last_engine_ingress_rate_bps = prev_last_rate_map
            metrics._last_engine_rate_ts = prev_last_rate_ts
