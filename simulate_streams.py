#!/usr/bin/env python3
"""
Simulate N simultaneous stream requests against the acestream-orchestrator proxy.

Usage:
    python simulate_streams.py streams.txt
    python simulate_streams.py streams.txt --proxy http://localhost:8000 --count 50 --timeout 15
    python simulate_streams.py streams.txt --count 20 --hls

Each line in the txt file should be an AceStream content ID (40-char hex) or a
full acestream:// URL.  Lines starting with # are ignored.

The script fires all requests simultaneously, waits for the first response byte
(stream accepted / 503 / error), then cancels each connection and reports results.
"""

import argparse
import re
import sys
import time
import threading
from dataclasses import dataclass, field
from typing import Optional
import urllib.request
import urllib.error


# ── Helpers ────────────────────────────────────────────────────────────────────

_CONTENT_ID_RE = re.compile(r"[0-9a-fA-F]{40}")

def parse_content_id(line: str) -> Optional[str]:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    # acestream://abc123... or bare 40-char hex
    m = _CONTENT_ID_RE.search(line)
    return m.group(0) if m else None


def load_urls(path: str, proxy: str, count: int, hls: bool) -> list[str]:
    with open(path) as f:
        ids = [parse_content_id(l) for l in f if parse_content_id(l)]

    if not ids:
        sys.exit(f"No valid content IDs found in {path}")

    endpoint = "manifest.m3u8" if hls else "getstream"
    base = proxy.rstrip("/")

    # Cycle through IDs to fill the requested count
    urls = []
    for i in range(count):
        cid = ids[i % len(ids)]
        urls.append(f"{base}/ace/{endpoint}?id={cid}")
    return urls


# ── Result type ────────────────────────────────────────────────────────────────

@dataclass
class Result:
    index: int
    url: str
    status: int = 0           # HTTP status code, 0 = connection error
    latency_ms: float = 0.0
    error: str = ""
    redirect_to: str = ""


# ── Worker ─────────────────────────────────────────────────────────────────────

def fetch_one(index: int, url: str, timeout: float, results: list, lock: threading.Lock) -> None:
    t0 = time.monotonic()
    result = Result(index=index, url=url)

    req = urllib.request.Request(url, method="GET")
    # Don't follow redirects — a 302 to manifest.m3u8 means the stream was
    # accepted; we just want the first response, not the HLS playlist bytes.
    opener = urllib.request.build_opener(NoRedirectHandler())
    try:
        with opener.open(req, timeout=timeout) as resp:
            result.status = resp.status
            resp.read(1)  # ensure headers + first byte arrived
    except RedirectException as e:
        result.status = e.code
        result.redirect_to = e.location
    except urllib.error.HTTPError as e:
        result.status = e.code
        result.error = e.read(256).decode(errors="replace").strip()
    except Exception as e:
        result.status = 0
        result.error = str(e)[:120]
    finally:
        result.latency_ms = (time.monotonic() - t0) * 1000

    with lock:
        results.append(result)


class RedirectException(Exception):
    def __init__(self, code: int, location: str):
        self.code = code
        self.location = location


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise RedirectException(code, newurl)


# ── Runner ─────────────────────────────────────────────────────────────────────

def run(urls: list[str], timeout: float) -> list[Result]:
    results: list[Result] = []
    lock = threading.Lock()

    threads = [
        threading.Thread(
            target=fetch_one,
            args=(i, url, timeout, results, lock),
            daemon=True,
        )
        for i, url in enumerate(urls)
    ]

    print(f"Firing {len(threads)} simultaneous requests …")
    t_start = time.monotonic()

    for t in threads:
        t.start()
    for t in threads:
        t.join()

    elapsed = (time.monotonic() - t_start) * 1000
    print(f"All responses received in {elapsed:.0f} ms\n")
    return sorted(results, key=lambda r: r.index)


# ── Report ─────────────────────────────────────────────────────────────────────

STATUS_LABEL = {
    200: "OK        ",
    302: "REDIRECT  ",  # HLS accepted
    503: "CAPACITY  ",
    500: "SERVER ERR",
    0:   "CONN ERR  ",
}

def report(results: list[Result]) -> None:
    counts: dict[int, int] = {}
    latencies: list[float] = []

    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
        latencies.append(r.latency_ms)

    # Per-request table
    print(f"{'#':>4}  {'Status':<12}  {'Latency':>8}  Details")
    print("─" * 72)
    for r in results:
        label = STATUS_LABEL.get(r.status, f"{r.status:<10}")
        detail = r.redirect_to or r.error or ""
        if len(detail) > 48:
            detail = detail[:45] + "..."
        print(f"{r.index:>4}  {label}  {r.latency_ms:>7.0f}ms  {detail}")

    # Summary
    print("\n" + "─" * 72)
    total = len(results)
    accepted  = counts.get(200, 0) + counts.get(302, 0)
    rejected  = counts.get(503, 0)
    errors    = total - accepted - rejected

    lat_sorted = sorted(latencies)
    p50 = lat_sorted[int(len(lat_sorted) * 0.50)]
    p95 = lat_sorted[int(len(lat_sorted) * 0.95)]
    p99 = lat_sorted[min(int(len(lat_sorted) * 0.99), len(lat_sorted) - 1)]

    print(f"\nTotal requests : {total}")
    print(f"  Accepted     : {accepted}  ({100*accepted/total:.1f}%)")
    print(f"  Rejected 503 : {rejected}  ({100*rejected/total:.1f}%)")
    print(f"  Errors       : {errors}  ({100*errors/total:.1f}%)")
    print(f"\nLatency (first-byte)")
    print(f"  p50 : {p50:.0f} ms")
    print(f"  p95 : {p95:.0f} ms")
    print(f"  p99 : {p99:.0f} ms")
    print(f"  max : {max(latencies):.0f} ms")

    # Capacity verdict
    print()
    if rejected == 0 and errors == 0:
        print("✓ All streams accepted — system handled the load.")
    elif accepted > 0:
        print(f"⚠ Partial capacity: {rejected} stream(s) shed. "
              f"Increase MAX_REPLICAS or add a WireGuard key.")
    else:
        print("✗ No streams accepted. Check proxy is reachable and engines are up.")


# ── CLI ────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("urls_file", help="txt file with one AceStream ID or acestream:// URL per line")
    parser.add_argument("--proxy",   default="http://localhost:8000", help="proxy base URL (default: http://localhost:8000)")
    parser.add_argument("--count",   type=int, default=50, help="number of simultaneous streams to simulate (default: 50)")
    parser.add_argument("--timeout", type=float, default=15.0, help="per-request timeout in seconds (default: 15)")
    parser.add_argument("--hls",     action="store_true", help="use /ace/manifest.m3u8 instead of /ace/getstream")
    args = parser.parse_args()

    urls = load_urls(args.urls_file, args.proxy, args.count, args.hls)
    results = run(urls, args.timeout)
    report(results)

    rejected = sum(1 for r in results if r.status == 503)
    sys.exit(1 if rejected > 0 else 0)


if __name__ == "__main__":
    main()
