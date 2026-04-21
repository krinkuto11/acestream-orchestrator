#!/usr/bin/env python3
"""
Runway diagnostic: connect directly to the proxy stream, poll Redis every 0.5 s,
and print a live table showing ring-buffer head, source rate, client position,
runway depth, proxy measured bitrate vs actual delivery rate, and pacing ratio.

Usage:
    python runway_diag.py <content_id> [options]

    content_id   AceStream content ID hash

Options:
    --proxy      Proxy base URL      (default: http://localhost:8000)
    --redis      Redis host:port     (default: localhost:6379)
    --chunk      Ring buffer chunk size in bytes (default: 1060672 = 188*5644)
    --duration   Seconds to collect  (default: 120)
    --key        API key if needed   (default: none)
"""

import argparse
import sys
import time
import threading
import collections
import textwrap

try:
    import requests
except ImportError:
    sys.exit("pip install requests")

try:
    import redis as redislib
    HAS_REDIS = True
except ImportError:
    HAS_REDIS = False
    print("WARNING: redis-py not installed — Redis columns will be empty. pip install redis\n")

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
DEFAULT_CHUNK = 188 * 5644          # ~1 MB, matches Go proxy default
POLL_INTERVAL = 0.5                 # seconds between samples
RATE_WINDOW   = 3.0                 # seconds of history for rate calculations

# ──────────────────────────────────────────────────────────────────────────────
# Redis helpers
# ──────────────────────────────────────────────────────────────────────────────
def redis_connect(host, port):
    if not HAS_REDIS:
        return None
    try:
        r = redislib.Redis(host=host, port=port, decode_responses=True, socket_timeout=1)
        r.ping()
        return r
    except Exception as e:
        print(f"Redis unavailable ({e}) — Redis columns will be empty")
        return None


def redis_get(rdb, key, default=None):
    if rdb is None:
        return default
    try:
        v = rdb.get(key)
        return v if v is not None else default
    except Exception:
        return default


def redis_hgetall(rdb, key):
    if rdb is None:
        return {}
    try:
        return rdb.hgetall(key) or {}
    except Exception:
        return {}


def redis_smembers(rdb, key):
    if rdb is None:
        return set()
    try:
        return rdb.smembers(key) or set()
    except Exception:
        return set()

# ──────────────────────────────────────────────────────────────────────────────
# RateMeter: simple sliding-window bytes/s calculator
# ──────────────────────────────────────────────────────────────────────────────
class RateMeter:
    def __init__(self, window=RATE_WINDOW):
        self.window = window
        self.samples = collections.deque()   # (timestamp, cumulative_bytes)
        self.total   = 0

    def add(self, nbytes):
        now = time.monotonic()
        self.total += nbytes
        self.samples.append((now, self.total))

    def bps(self):
        now = time.monotonic()
        cutoff = now - self.window
        while self.samples and self.samples[0][0] < cutoff:
            self.samples.popleft()
        if len(self.samples) < 2:
            return 0.0
        dt = self.samples[-1][0] - self.samples[0][0]
        db = self.samples[-1][1] - self.samples[0][1]
        return db / dt if dt > 0 else 0.0

# ──────────────────────────────────────────────────────────────────────────────
# Stream receiver thread
# ──────────────────────────────────────────────────────────────────────────────
class StreamReceiver(threading.Thread):
    def __init__(self, url, headers, meter):
        super().__init__(daemon=True)
        self.url     = url
        self.headers = headers
        self.meter   = meter
        self.stop    = threading.Event()
        self.error   = None
        self.connected_at = None

    def run(self):
        try:
            with requests.get(self.url, headers=self.headers,
                              stream=True, timeout=30) as resp:
                resp.raise_for_status()
                self.connected_at = time.monotonic()
                for chunk in resp.iter_content(chunk_size=32768):
                    if self.stop.is_set():
                        break
                    if chunk:
                        self.meter.add(len(chunk))
        except Exception as e:
            self.error = str(e)

# ──────────────────────────────────────────────────────────────────────────────
# Snapshot: one row of the table
# ──────────────────────────────────────────────────────────────────────────────
class Snap:
    __slots__ = (
        "t", "head", "source_rate_cps",
        "proxy_br_bps", "rx_bps",
        "client_initial", "client_chunks",
        "runway_chunks", "runway_sec",
        "burst_ratio",
    )


# ──────────────────────────────────────────────────────────────────────────────
# Main diagnostic loop
# ──────────────────────────────────────────────────────────────────────────────
def run(args):
    content_id = args.content_id
    proxy_url  = args.proxy.rstrip("/")
    chunk_size = args.chunk

    redis_host, _, redis_port_s = args.redis.partition(":")
    redis_port = int(redis_port_s) if redis_port_s else 6379
    rdb = redis_connect(redis_host, redis_port)

    # Redis key helpers (mirrors rediskeys/keys.go)
    def rk_buf_index():  return f"ace_proxy:stream:{content_id}:buffer:index"
    def rk_metadata():   return f"ace_proxy:stream:{content_id}:metadata"
    def rk_clients():    return f"ace_proxy:stream:{content_id}:clients"
    def rk_client(cid):  return f"ace_proxy:stream:{content_id}:clients:{cid}"

    # Start receiver
    stream_url = f"{proxy_url}/ace/getstream?id={content_id}"
    headers    = {"User-Agent": "runway-diagnostic/1.0"}
    if args.key:
        headers["X-API-Key"] = args.key

    meter    = RateMeter()
    receiver = StreamReceiver(stream_url, headers, meter)
    receiver.start()

    print(f"\n  Stream  : {stream_url}")
    print(f"  Redis   : {redis_host}:{redis_port}")
    print(f"  Chunk   : {chunk_size:,} bytes ({chunk_size/1e6:.2f} MB)")
    print(f"  Duration: {args.duration} s")
    print()
    time.sleep(0.3)  # let receiver connect before first poll

    # ── table header ──────────────────────────────────────────────────────────
    HDR = (
        f"{'t':>6}  "
        f"{'HEAD':>8}  "
        f"{'SRC_CPS':>8}  "
        f"{'SRC_MB/s':>9}  "
        f"{'PROXY_BR':>9}  "
        f"{'RX_MB/s':>9}  "
        f"{'PACE_X':>7}  "
        f"{'CLI_POS':>8}  "
        f"{'RUNWAY_C':>9}  "
        f"{'RUNWAY_S':>9}"
    )
    SEP = "─" * len(HDR)
    print(HDR)
    print(SEP)

    snaps          = []
    prev_head      = None
    prev_head_time = None
    head_ema       = 0.0
    start          = time.monotonic()

    try:
        while time.monotonic() - start < args.duration:
            t = time.monotonic() - start

            # ── Redis reads ───────────────────────────────────────────────────
            head_s = redis_get(rdb, rk_buf_index(), "-1")
            head   = int(head_s) if head_s and head_s.lstrip("-").isdigit() else -1

            meta       = redis_hgetall(rdb, rk_metadata())
            proxy_br   = int(meta.get("bitrate", 0) or 0)   # bytes/s

            client_ids = redis_smembers(rdb, rk_clients())
            # pick the first client (likely ours)
            cli_initial = -1
            cli_chunks  = -1
            for cid in client_ids:
                c = redis_hgetall(rdb, rk_client(cid))
                if c:
                    cli_initial = int(c.get("initial_index", -1) or -1)
                    cli_chunks  = int(c.get("chunks_sent",   0)  or 0)
                    break   # one client is enough for now

            # ── source rate (ring head advancement EMA) ───────────────────────
            now = time.monotonic()
            if prev_head is not None and head > prev_head and prev_head_time is not None:
                dt      = now - prev_head_time
                delta_c = head - prev_head
                instant = delta_c / dt          # chunks/s
                alpha   = 0.4
                head_ema = alpha * instant + (1 - alpha) * head_ema if head_ema else instant
            if head >= 0:
                prev_head      = head
                prev_head_time = now

            src_cps  = head_ema
            src_bps  = src_cps * chunk_size     # bytes/s from upstream

            # ── client position and runway ────────────────────────────────────
            runway_chunks = -1
            runway_sec    = float("nan")
            if head >= 0 and cli_initial >= 0 and cli_chunks >= 0:
                cli_pos       = cli_initial + cli_chunks
                runway_chunks = max(0, head - cli_pos)
                ref_bps       = proxy_br if proxy_br > 0 else (src_bps if src_bps > 0 else 0)
                if ref_bps > 0:
                    runway_sec = runway_chunks * chunk_size / ref_bps

            # ── delivery rate and pacing ratio ────────────────────────────────
            rx_bps     = meter.bps()
            pace_ratio = (rx_bps / proxy_br) if proxy_br > 0 else float("nan")

            # ── store + print ─────────────────────────────────────────────────
            s = Snap()
            s.t               = t
            s.head            = head
            s.source_rate_cps = src_cps
            s.proxy_br_bps    = proxy_br
            s.rx_bps          = rx_bps
            s.client_initial  = cli_initial
            s.client_chunks   = cli_chunks
            s.runway_chunks   = runway_chunks
            s.runway_sec      = runway_sec
            s.burst_ratio     = pace_ratio
            snaps.append(s)

            src_mb  = src_bps  / 1e6
            pbr_mb  = proxy_br / 1e6
            rx_mb   = rx_bps   / 1e6
            rwy_c   = runway_chunks if runway_chunks >= 0 else -1
            rwy_s   = f"{runway_sec:.1f}" if runway_sec == runway_sec else "  ?"
            px      = f"{pace_ratio:.2f}x" if pace_ratio == pace_ratio and pace_ratio > 0 else "   ?"

            # flag anomalies inline
            flag = ""
            if proxy_br > 0 and rx_bps > proxy_br * 1.5:
                flag = " ◀ RX > 1.5× BR"
            elif rwy_c == 0:
                flag = " ● runway=0"
            elif rwy_c >= 0 and proxy_br > 0 and runway_sec < 2:
                flag = " ▲ low runway"

            print(
                f"{t:6.1f}  "
                f"{head:>8}  "
                f"{src_cps:>8.2f}  "
                f"{src_mb:>9.2f}  "
                f"{pbr_mb:>9.2f}  "
                f"{rx_mb:>9.2f}  "
                f"{px:>7}  "
                f"{(cli_initial + cli_chunks) if cli_chunks >= 0 else -1:>8}  "
                f"{rwy_c:>9}  "
                f"{rwy_s:>9}"
                f"{flag}"
            )

            if receiver.error and not receiver.is_alive():
                print(f"\n  [receiver died] {receiver.error}")
                break

            time.sleep(POLL_INTERVAL)

    except KeyboardInterrupt:
        print("\n  (interrupted)")

    receiver.stop.set()

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    print(SEP)
    print("  SUMMARY")
    print(SEP)

    if not snaps:
        print("  No data collected.")
        return

    total_t  = snaps[-1].t
    valid_rw = [s for s in snaps if s.runway_chunks >= 0]
    valid_bps = [s for s in snaps if s.proxy_br_bps > 0]
    valid_src = [s for s in snaps if s.source_rate_cps > 0]
    zero_rw  = [s for s in valid_rw if s.runway_chunks == 0]

    avg_proxy_br  = sum(s.proxy_br_bps   for s in valid_bps) / len(valid_bps) if valid_bps else 0
    avg_src_bps   = sum(s.source_rate_cps * chunk_size for s in valid_src) / len(valid_src) if valid_src else 0
    avg_rx_bps    = sum(s.rx_bps          for s in snaps) / len(snaps)
    peak_rx_bps   = max((s.rx_bps         for s in snaps), default=0)
    min_runway_s  = min((s.runway_sec      for s in valid_rw if s.runway_sec == s.runway_sec), default=float("nan"))
    max_runway_s  = max((s.runway_sec      for s in valid_rw if s.runway_sec == s.runway_sec), default=float("nan"))

    # When did runway first hit 0?
    first_zero = next((s.t for s in valid_rw if s.runway_chunks == 0), None)
    # Initial runway
    init_runway = next((s.runway_sec for s in valid_rw if s.runway_sec == s.runway_sec), float("nan"))

    # How many seconds spent at runway=0?
    zero_secs = len(zero_rw) * POLL_INTERVAL

    print(f"  Duration              : {total_t:.1f} s")
    print(f"  Avg proxy bitrate     : {avg_proxy_br/1e6:.2f} MB/s  ({avg_proxy_br*8/1e6:.2f} Mbps)")
    print(f"  Avg source rate       : {avg_src_bps/1e6:.2f} MB/s  ({avg_src_bps*8/1e6:.2f} Mbps)")
    print(f"  Avg delivery to client: {avg_rx_bps/1e6:.2f} MB/s  ({avg_rx_bps*8/1e6:.2f} Mbps)")
    print(f"  Peak delivery rate    : {peak_rx_bps/1e6:.2f} MB/s  ({peak_rx_bps*8/1e6:.2f} Mbps)")
    print(f"  Initial runway        : {init_runway:.1f} s")
    print(f"  Min runway            : {min_runway_s:.1f} s")
    print(f"  Max runway            : {max_runway_s:.1f} s")
    print(f"  Time at runway=0      : {zero_secs:.1f} s  ({100*zero_secs/total_t:.0f}% of run)")
    if first_zero is not None:
        print(f"  Runway first hit 0 at : t={first_zero:.1f} s")
    print()

    # Diagnosis
    print("  DIAGNOSIS")
    print(SEP)
    issues = []

    if avg_proxy_br > 0 and avg_src_bps > avg_proxy_br * 1.8:
        issues.append(
            f"• SOURCE RATE INFLATION: proxy measured bitrate ({avg_proxy_br*8/1e6:.1f} Mbps) << "
            f"actual source rate ({avg_src_bps*8/1e6:.1f} Mbps) — "
            f"AceStream burst likely inflating sourceRateEMA; effectiveBPS() cap may not be working."
        )

    if avg_proxy_br > 0 and avg_rx_bps > avg_proxy_br * 1.3:
        issues.append(
            f"• PACING NOT THROTTLING: avg delivery ({avg_rx_bps*8/1e6:.1f} Mbps) is "
            f"{avg_rx_bps/avg_proxy_br:.1f}× proxy bitrate ({avg_proxy_br*8/1e6:.1f} Mbps). "
            f"Pacing burst budget is too large or effectiveBR is inflated."
        )

    if avg_proxy_br > 0 and avg_rx_bps < avg_proxy_br * 0.8:
        issues.append(
            f"• UNDER-DELIVERY: avg delivery ({avg_rx_bps*8/1e6:.1f} Mbps) is only "
            f"{avg_rx_bps/avg_proxy_br:.0%} of proxy bitrate — pacing too aggressive or upstream stalling."
        )

    if first_zero is not None and first_zero < 5.0:
        issues.append(
            f"• FAST RUNWAY COLLAPSE: runway hit 0 at t={first_zero:.1f} s — "
            f"burst consumed entire prebuffer before steady pacing engaged."
        )

    if zero_secs > total_t * 0.5:
        issues.append(
            f"• RUNWAY MOSTLY ZERO: {zero_secs:.0f} s out of {total_t:.0f} s at runway=0 — "
            f"delivery consistently outpacing ingress; mult=1.0 low-runway tier may not be firing."
        )

    if avg_src_bps > 0 and avg_rx_bps > avg_src_bps * 1.05:
        issues.append(
            f"• CLIENT FASTER THAN UPSTREAM: delivery ({avg_rx_bps/1e6:.2f} MB/s) > "
            f"source ({avg_src_bps/1e6:.2f} MB/s). Ring buffer draining by design; "
            f"check if initPacingBurst() is capping burst to runway correctly."
        )

    if not issues:
        issues.append("• No obvious anomalies detected. Check raw table above for transient spikes.")

    for line in issues:
        print()
        for wrapped in textwrap.wrap(line, width=90, subsequent_indent="  "):
            print(f"  {wrapped}")

    print()
    print("  LEGEND")
    print("  HEAD      ring-buffer head index (chunks written by upstream)")
    print("  SRC_CPS   source rate EMA from HEAD delta (chunks/s)")
    print("  SRC_MB/s  source rate in MB/s")
    print("  PROXY_BR  bitrate the Go proxy has measured and stored in Redis (MB/s)")
    print("  RX_MB/s   actual bytes/s this script is receiving from the proxy")
    print("  PACE_X    RX / PROXY_BR — ratio > 1 means pacing is under-throttling")
    print("  CLI_POS   estimated client localIndex (initial_index + chunks_sent from Redis)")
    print("  RUNWAY_C  HEAD - CLI_POS in chunks")
    print("  RUNWAY_S  RUNWAY_C × chunk_size / PROXY_BR in seconds")
    print()


# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="AceStream proxy runway diagnostic — connects directly and polls Redis",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("content_id", help="AceStream content ID hash")
    ap.add_argument("--proxy",    default="http://localhost:8000", help="Proxy base URL")
    ap.add_argument("--redis",    default="localhost:6379",        help="Redis host:port")
    ap.add_argument("--chunk",    type=int, default=DEFAULT_CHUNK, help="Ring chunk size (bytes)")
    ap.add_argument("--duration", type=int, default=120,           help="Collection time (s)")
    ap.add_argument("--key",      default="",                      help="API key if required")
    args = ap.parse_args()
    run(args)
