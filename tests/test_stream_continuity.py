from __future__ import annotations

import asyncio
import os
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncIterator, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urljoin, urlparse

import httpx
import pytest


SYNC_BYTE = 0x47
TS_PACKET_SIZE = 188
PTS_WRAP = 1 << 33


@dataclass(frozen=True)
class SegmentRef:
    sequence: int
    url: str


@dataclass(frozen=True)
class ManifestSnapshot:
    media_sequence: int
    segments: List[SegmentRef]
    raw_text: str


@dataclass(frozen=True)
class ContinuityConfig:
    enabled: bool
    use_inprocess: bool
    base_url: str
    api_key: Optional[str]
    content_id: str
    min_engines: int
    phase_packets: int
    stream_read_timeout_s: float
    stream_open_timeout_s: float
    migration_mode: str
    migration_url: Optional[str]
    hls_segment_batch: int
    hls_pts_gap_tolerance: int
    poll_interval_s: float

    @classmethod
    def from_env(cls) -> "ContinuityConfig":
        content_id = os.getenv("STREAM_CONTINUITY_CONTENT_ID", "").strip() or _load_default_content_id()
        return cls(
            enabled=os.getenv("STREAM_CONTINUITY_E2E", "1") == "1",
            use_inprocess=os.getenv("STREAM_CONTINUITY_INPROCESS", "0") == "1",
            base_url=os.getenv("STREAM_CONTINUITY_BASE_URL", "http://localhost:8000").rstrip("/"),
            api_key=(os.getenv("API_KEY") or os.getenv("STREAM_CONTINUITY_API_KEY") or "").strip() or None,
            content_id=content_id,
            min_engines=max(1, int(os.getenv("STREAM_CONTINUITY_MIN_ENGINES", "2"))),
            phase_packets=max(1, int(os.getenv("STREAM_CONTINUITY_PHASE_PACKETS", "5000"))),
            stream_read_timeout_s=max(1.0, float(os.getenv("STREAM_CONTINUITY_READ_TIMEOUT_S", "30"))),
            stream_open_timeout_s=max(1.0, float(os.getenv("STREAM_CONTINUITY_OPEN_TIMEOUT_S", "30"))),
            migration_mode=(os.getenv("STREAM_CONTINUITY_MIGRATION_MODE", "http").strip().lower() or "http"),
            migration_url=(os.getenv("STREAM_CONTINUITY_MIGRATION_URL") or "").strip() or None,
            hls_segment_batch=max(1, int(os.getenv("STREAM_CONTINUITY_HLS_SEGMENTS", "3"))),
            hls_pts_gap_tolerance=max(0, int(os.getenv("STREAM_CONTINUITY_HLS_PTS_TOLERANCE", "9000"))),
            poll_interval_s=max(0.05, float(os.getenv("STREAM_CONTINUITY_POLL_INTERVAL_S", "0.5"))),
        )


class TsPacketInspector:
    """Strict MPEG-TS inspector that validates sync and continuity counters."""

    def __init__(self) -> None:
        self.packet_count = 0
        self.sync_errors: List[str] = []
        self.cc_skips: List[str] = []
        self._last_cc_by_pid: Dict[int, int] = {}
        self._pts_values: List[int] = []

    @property
    def pts_values(self) -> List[int]:
        return list(self._pts_values)

    def consume_packet(self, packet: bytes) -> None:
        if len(packet) != TS_PACKET_SIZE:
            raise AssertionError(f"Invalid TS packet length: {len(packet)}")

        self.packet_count += 1
        if packet[0] != SYNC_BYTE:
            msg = f"Sync byte mismatch at packet #{self.packet_count}: got 0x{packet[0]:02x}"
            self.sync_errors.append(msg)
            raise AssertionError(msg)

        b1, b2, b3 = packet[1], packet[2], packet[3]
        pid = ((b1 & 0x1F) << 8) | b2
        payload_unit_start = bool((b1 >> 6) & 0x01)
        adaptation_field_control = (b3 >> 4) & 0x03
        cc = b3 & 0x0F

        has_payload = adaptation_field_control in (1, 3)
        if has_payload:
            previous_cc = self._last_cc_by_pid.get(pid)
            if previous_cc is not None:
                expected = (previous_cc + 1) & 0x0F
                # Duplicate continuity counter is tolerated; skip/jump is not.
                if cc not in (previous_cc, expected):
                    self.cc_skips.append(
                        f"PID 0x{pid:04x}: previous={previous_cc}, expected={expected}, got={cc}"
                    )
            self._last_cc_by_pid[pid] = cc

        pts = self._try_extract_pts(packet, pid, payload_unit_start, adaptation_field_control)
        if pts is not None:
            self._pts_values.append(pts)

    def consume_bytes(self, payload: bytes) -> None:
        if not payload:
            return

        if len(payload) < TS_PACKET_SIZE:
            raise AssertionError("Segment payload shorter than one TS packet")

        packet_count = len(payload) // TS_PACKET_SIZE
        tail = len(payload) % TS_PACKET_SIZE
        if tail != 0:
            raise AssertionError(
                f"Segment payload is not TS-aligned (len={len(payload)}, remainder={tail})"
            )

        offset = 0
        for _ in range(packet_count):
            self.consume_packet(payload[offset : offset + TS_PACKET_SIZE])
            offset += TS_PACKET_SIZE

    def assert_strict_continuity(self) -> None:
        assert not self.sync_errors, f"TS sync validation failed: {self.sync_errors[:3]}"
        assert not self.cc_skips, f"TS continuity counter skips detected: {self.cc_skips[:3]}"

    @staticmethod
    def _try_extract_pts(
        packet: bytes,
        pid: int,
        payload_unit_start: bool,
        adaptation_field_control: int,
    ) -> Optional[int]:
        if not payload_unit_start:
            return None

        if adaptation_field_control not in (1, 3):
            return None

        offset = 4
        if adaptation_field_control == 3:
            if offset >= TS_PACKET_SIZE:
                return None
            adaptation_len = packet[offset]
            offset += 1 + adaptation_len

        if offset + 14 > TS_PACKET_SIZE:
            return None

        payload = packet[offset:]
        if len(payload) < 14:
            return None

        # Skip PAT/PMT sections.
        if pid == 0x0000:
            return None

        if payload[0:3] != b"\x00\x00\x01":
            return None

        # PES header flags byte 7 and header-data-length byte 8.
        pts_dts_flags = (payload[7] >> 6) & 0x03
        if pts_dts_flags not in (0x02, 0x03):
            return None

        header_data_len = payload[8]
        if header_data_len < 5 or len(payload) < 14:
            return None

        pts_field = payload[9:14]
        if len(pts_field) < 5:
            return None

        # ISO/IEC 13818-1 PES PTS layout (33 bits spread over 5 bytes).
        b0, b1, b2, b3, b4 = struct.unpack("5B", pts_field)
        pts = (
            ((b0 >> 1) & 0x07) << 30
            | (b1 << 22)
            | ((b2 >> 1) & 0x7F) << 15
            | (b3 << 7)
            | ((b4 >> 1) & 0x7F)
        )
        return int(pts)


class MockMpegTsPlayer:
    """Strict async TS reader that keeps one socket open and validates packets in-flight."""

    def __init__(self, *, read_chunk_size: int = 64 * 1024, read_timeout_s: float = 30.0) -> None:
        self.read_chunk_size = max(TS_PACKET_SIZE, int(read_chunk_size))
        self.read_timeout_s = max(1.0, float(read_timeout_s))
        self.inspector = TsPacketInspector()

        self._stream_iter: Optional[AsyncIterator[bytes]] = None
        self._pending = bytearray()
        self._packets_read = 0
        self.read_errors: List[BaseException] = []

    async def attach(self, response: httpx.Response) -> None:
        self._stream_iter = response.aiter_bytes(chunk_size=self.read_chunk_size)

    async def read_packets(self, packet_count: int) -> None:
        if self._stream_iter is None:
            raise RuntimeError("MockMpegTsPlayer.attach() must be called before reading")

        target_total = self._packets_read + max(1, int(packet_count))

        while self._packets_read < target_total:
            while len(self._pending) >= TS_PACKET_SIZE and self._packets_read < target_total:
                packet = bytes(self._pending[:TS_PACKET_SIZE])
                del self._pending[:TS_PACKET_SIZE]
                self.inspector.consume_packet(packet)
                self._packets_read += 1

            if self._packets_read >= target_total:
                break

            try:
                chunk = await asyncio.wait_for(anext(self._stream_iter), timeout=self.read_timeout_s)
            except StopAsyncIteration as exc:
                self.read_errors.append(exc)
                raise AssertionError(
                    f"Socket closed after {self._packets_read} TS packets; expected at least {target_total}"
                ) from exc
            except (httpx.ReadError, httpx.RemoteProtocolError, asyncio.TimeoutError) as exc:
                self.read_errors.append(exc)
                raise AssertionError(f"Streaming socket became unstable: {exc}") from exc

            self._pending.extend(chunk)

    def assert_no_transport_errors(self) -> None:
        assert not self.read_errors, f"Transport errors encountered: {self.read_errors!r}"


def _load_default_content_id() -> str:
    hashes_file = Path(__file__).resolve().parents[1] / "hashes.txt"
    if not hashes_file.exists():
        return ""

    for raw_line in hashes_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        token = line.split()[0].strip()
        if token:
            return token
    return ""


def _headers(api_key: Optional[str]) -> Dict[str, str]:
    if not api_key:
        return {}
    return {"X-API-KEY": api_key}


def _extract_items(payload: object) -> List[dict]:
    if isinstance(payload, dict):
        items = payload.get("items")
        if isinstance(items, list):
            return [row for row in items if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


async def _build_client(cfg: ContinuityConfig) -> httpx.AsyncClient:
    timeout = httpx.Timeout(connect=cfg.stream_open_timeout_s, read=None, write=30.0, pool=30.0)
    if cfg.use_inprocess:
        from app.main import app

        transport = httpx.ASGITransport(app=app)
        return httpx.AsyncClient(
            base_url="http://testserver",
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
        )

    return httpx.AsyncClient(base_url=cfg.base_url, timeout=timeout, follow_redirects=True)


async def _wait_for_min_engines(
    client: httpx.AsyncClient,
    *,
    headers: Dict[str, str],
    min_engines: int,
    timeout_s: float,
    poll_interval_s: float,
) -> List[dict]:
    deadline = asyncio.get_running_loop().time() + max(1.0, timeout_s)
    last_payload: object = []

    while asyncio.get_running_loop().time() < deadline:
        response = await client.get("/engines", headers=headers)
        response.raise_for_status()
        last_payload = response.json()
        engines = _extract_items(last_payload)
        if len(engines) >= min_engines:
            return engines
        await asyncio.sleep(poll_interval_s)

    raise AssertionError(
        f"Expected at least {min_engines} healthy engines before continuity test; got {_extract_items(last_payload)}"
    )


async def _lookup_active_stream(
    client: httpx.AsyncClient,
    *,
    stream_key: str,
    headers: Dict[str, str],
    timeout_s: float,
    poll_interval_s: float,
) -> dict:
    deadline = asyncio.get_running_loop().time() + max(1.0, timeout_s)

    while asyncio.get_running_loop().time() < deadline:
        response = await client.get("/streams", headers=headers)
        response.raise_for_status()
        for row in _extract_items(response.json()):
            if str(row.get("key") or "") != stream_key:
                continue
            if str(row.get("status") or "") != "started":
                continue
            return row
        await asyncio.sleep(poll_interval_s)

    raise AssertionError(f"Could not locate active stream state for key={stream_key!r}")


async def _trigger_stream_migration(
    client: httpx.AsyncClient,
    *,
    cfg: ContinuityConfig,
    stream_key: str,
    old_container_id: Optional[str],
    headers: Dict[str, str],
) -> dict:
    mode = cfg.migration_mode
    if mode == "internal":
        if not cfg.use_inprocess:
            pytest.skip("STREAM_CONTINUITY_MIGRATION_MODE=internal requires STREAM_CONTINUITY_INPROCESS=1")
        from app.proxy.manager import ProxyManager

        result = await asyncio.to_thread(ProxyManager.migrate_stream, stream_key, None, old_container_id)
        if not isinstance(result, dict):
            raise AssertionError(f"Unexpected migration result payload: {result!r}")
        return result

    if mode == "http":
        if not cfg.migration_url:
            pytest.skip("Set STREAM_CONTINUITY_MIGRATION_URL for HTTP-triggered migration tests")

        payload = {"stream_key": stream_key}
        if old_container_id:
            payload["old_container_id"] = old_container_id

        response = await client.post(cfg.migration_url, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict):
            return body
        return {"migrated": False, "reason": f"unexpected_response:{body!r}"}

    pytest.skip(f"Unsupported STREAM_CONTINUITY_MIGRATION_MODE={mode!r}")


def _parse_manifest(text: str, *, base_url: str) -> ManifestSnapshot:
    media_sequence = 0
    segment_urls: List[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if line.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            media_sequence = int(line.split(":", 1)[1].strip())
            continue
        if line.startswith("#"):
            continue
        segment_urls.append(urljoin(base_url, line))

    segments = [SegmentRef(sequence=media_sequence + i, url=url) for i, url in enumerate(segment_urls)]
    return ManifestSnapshot(media_sequence=media_sequence, segments=segments, raw_text=text)


async def _fetch_manifest_snapshot(
    client: httpx.AsyncClient,
    *,
    content_id: str,
    headers: Dict[str, str],
) -> ManifestSnapshot:
    response = await client.get("/ace/getstream", params={"id": content_id}, headers=headers)
    response.raise_for_status()

    manifest_text = response.text
    if "#EXTM3U" not in manifest_text:
        raise AssertionError(
            "Expected HLS manifest text from /ace/getstream; got non-manifest payload "
            f"(status={response.status_code}, content_type={response.headers.get('content-type')!r})"
        )

    # Manifest may contain relative segment URIs.
    base_url = str(response.url)
    return _parse_manifest(manifest_text, base_url=base_url)


async def _wait_for_segment_batch(
    client: httpx.AsyncClient,
    *,
    content_id: str,
    headers: Dict[str, str],
    count: int,
    min_sequence_exclusive: Optional[int],
    timeout_s: float,
    poll_interval_s: float,
) -> Tuple[ManifestSnapshot, List[SegmentRef]]:
    deadline = asyncio.get_running_loop().time() + max(1.0, timeout_s)

    while asyncio.get_running_loop().time() < deadline:
        snapshot = await _fetch_manifest_snapshot(client, content_id=content_id, headers=headers)
        eligible = snapshot.segments
        if min_sequence_exclusive is not None:
            eligible = [seg for seg in snapshot.segments if seg.sequence > min_sequence_exclusive]

        if len(eligible) >= count:
            return snapshot, eligible[:count]

        await asyncio.sleep(poll_interval_s)

    raise AssertionError(
        "Timed out waiting for sufficient HLS segments "
        f"(need={count}, after_seq={min_sequence_exclusive})"
    )


def _segment_pts_range(segment_payload: bytes) -> Tuple[int, int]:
    inspector = TsPacketInspector()
    inspector.consume_bytes(segment_payload)
    pts_values = inspector.pts_values
    if not pts_values:
        raise AssertionError("No PTS values were found in HLS segment payload")
    return min(pts_values), max(pts_values)


def _pts_forward_delta(previous_end_pts: int, next_start_pts: int) -> int:
    delta = (next_start_pts - previous_end_pts) % PTS_WRAP
    if delta > (PTS_WRAP // 2):
        # Convert wrap-around backwards delta to signed-negative style range.
        delta -= PTS_WRAP
    return int(delta)


def _require_continuity_env(cfg: ContinuityConfig) -> None:
    if not cfg.enabled:
        pytest.fail(
            "STREAM_CONTINUITY_E2E must be enabled (set STREAM_CONTINUITY_E2E=1). "
            "These tests require running engines and an orchestrator endpoint."
        )
    if not cfg.content_id:
        pytest.skip(
            "Set STREAM_CONTINUITY_CONTENT_ID or provide hashes.txt with at least one stream id/infohash"
        )


@pytest.mark.asyncio
async def test_mpegts_seamless_handover() -> None:
    """Strict TS continuity check across a hot-swap migration using one open HTTP stream."""
    cfg = ContinuityConfig.from_env()
    _require_continuity_env(cfg)

    headers = _headers(cfg.api_key)

    async with await _build_client(cfg) as client:
        await _wait_for_min_engines(
            client,
            headers=headers,
            min_engines=cfg.min_engines,
            timeout_s=45.0,
            poll_interval_s=cfg.poll_interval_s,
        )

        params = {"id": cfg.content_id}
        timeout = httpx.Timeout(connect=cfg.stream_open_timeout_s, read=None, write=30.0, pool=30.0)

        async with client.stream("GET", "/ace/getstream", params=params, headers=headers, timeout=timeout) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            assert "video/mp2t" in content_type.lower(), (
                "Expected TS stream response. "
                f"Received content_type={content_type!r}; ensure proxy mode is TS for this test."
            )

            player = MockMpegTsPlayer(read_timeout_s=cfg.stream_read_timeout_s)
            await player.attach(response)

            # Phase 1: parse first 5k packets before migration.
            await player.read_packets(cfg.phase_packets)
            player.inspector.assert_strict_continuity()

            stream_state = await _lookup_active_stream(
                client,
                stream_key=cfg.content_id,
                headers=headers,
                timeout_s=20.0,
                poll_interval_s=cfg.poll_interval_s,
            )
            old_container_id = str(stream_state.get("container_id") or "").strip() or None

            migration_result = await _trigger_stream_migration(
                client,
                cfg=cfg,
                stream_key=cfg.content_id,
                old_container_id=old_container_id,
                headers=headers,
            )
            assert bool(migration_result.get("migrated")), (
                "Expected migration trigger to report success; "
                f"got result={migration_result}"
            )

            # Phase 2: continue reading from the same HTTP response object.
            await player.read_packets(cfg.phase_packets)

            player.assert_no_transport_errors()
            player.inspector.assert_strict_continuity()


@pytest.mark.asyncio
async def test_hls_seamless_handover() -> None:
    """HLS continuity check using segment sequence and PES-PTS alignment across migration."""
    cfg = ContinuityConfig.from_env()
    _require_continuity_env(cfg)

    headers = _headers(cfg.api_key)

    async with await _build_client(cfg) as client:
        await _wait_for_min_engines(
            client,
            headers=headers,
            min_engines=cfg.min_engines,
            timeout_s=45.0,
            poll_interval_s=cfg.poll_interval_s,
        )

        # Phase 1: fetch first batch of segments from active HLS channel.
        manifest_before, before_batch = await _wait_for_segment_batch(
            client,
            content_id=cfg.content_id,
            headers=headers,
            count=cfg.hls_segment_batch,
            min_sequence_exclusive=None,
            timeout_s=60.0,
            poll_interval_s=cfg.poll_interval_s,
        )

        before_pts_ranges: List[Tuple[int, int]] = []
        for segment in before_batch:
            seg_response = await client.get(segment.url, headers=headers)
            seg_response.raise_for_status()
            before_pts_ranges.append(_segment_pts_range(seg_response.content))

        before_last_seq = before_batch[-1].sequence
        before_last_end_pts = before_pts_ranges[-1][1]

        stream_state = await _lookup_active_stream(
            client,
            stream_key=cfg.content_id,
            headers=headers,
            timeout_s=20.0,
            poll_interval_s=cfg.poll_interval_s,
        )
        old_container_id = str(stream_state.get("container_id") or "").strip() or None

        migration_result = await _trigger_stream_migration(
            client,
            cfg=cfg,
            stream_key=cfg.content_id,
            old_container_id=old_container_id,
            headers=headers,
        )
        assert bool(migration_result.get("migrated")), (
            "Expected migration trigger to report success; "
            f"got result={migration_result}"
        )

        # Phase 2: fetch segments strictly after the phase-1 boundary.
        manifest_after, after_batch = await _wait_for_segment_batch(
            client,
            content_id=cfg.content_id,
            headers=headers,
            count=cfg.hls_segment_batch,
            min_sequence_exclusive=before_last_seq,
            timeout_s=60.0,
            poll_interval_s=cfg.poll_interval_s,
        )

        after_pts_ranges: List[Tuple[int, int]] = []
        for segment in after_batch:
            seg_response = await client.get(segment.url, headers=headers)
            seg_response.raise_for_status()
            after_pts_ranges.append(_segment_pts_range(seg_response.content))

        first_after_seq = after_batch[0].sequence
        assert first_after_seq == before_last_seq + 1, (
            "HLS media sequence skipped during migration: "
            f"before_last_seq={before_last_seq}, first_after_seq={first_after_seq}"
        )

        assert manifest_after.media_sequence >= manifest_before.media_sequence, (
            "Manifest media sequence moved backwards after migration: "
            f"before={manifest_before.media_sequence}, after={manifest_after.media_sequence}"
        )

        after_first_start_pts = after_pts_ranges[0][0]
        pts_delta = _pts_forward_delta(before_last_end_pts, after_first_start_pts)
        assert pts_delta >= 0, (
            "PTS moved backwards across migration: "
            f"before_end={before_last_end_pts}, after_start={after_first_start_pts}, delta={pts_delta}"
        )
        assert pts_delta <= cfg.hls_pts_gap_tolerance, (
            "PTS gap too large across migration (possible dropped frames): "
            f"delta={pts_delta}, tolerance={cfg.hls_pts_gap_tolerance}"
        )
