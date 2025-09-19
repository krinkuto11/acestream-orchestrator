from __future__ import annotations
import threading
from typing import Dict, List, Optional
from datetime import datetime, timezone
from ..models.schemas import EngineState, StreamState, StreamStartedEvent, StreamEndedEvent, StreamStatSnapshot
from ..services.db import SessionLocal
from ..models.db_models import EngineRow, StreamRow, StatRow

class State:
    def __init__(self):
        self._lock = threading.RLock()
        self.engines: Dict[str, EngineState] = {}
        self.streams: Dict[str, StreamState] = {}
        self.stream_stats: Dict[str, List[StreamStatSnapshot]] = {}

    @staticmethod
    def now():
        return datetime.now(timezone.utc)

    def on_stream_started(self, evt: StreamStartedEvent) -> StreamState:
        with self._lock:
            key = evt.container_id or f"{evt.engine.host}:{evt.engine.port}"
            eng = self.engines.get(key)
            if not eng:
                eng = EngineState(container_id=key, host=evt.engine.host, port=evt.engine.port,
                                  labels=evt.labels or {}, first_seen=self.now(), last_seen=self.now(), streams=[])
                self.engines[key] = eng
            else:
                eng.host = evt.engine.host; eng.port = evt.engine.port; eng.last_seen = self.now()
                if evt.labels: eng.labels.update(evt.labels)

            stream_id = (evt.labels.get("stream_id") if evt.labels else None) or f"{evt.stream.key}|{evt.session.playback_session_id}"
            st = StreamState(id=stream_id, key_type=evt.stream.key_type, key=evt.stream.key,
                             container_id=key, playback_session_id=evt.session.playback_session_id,
                             stat_url=str(evt.session.stat_url), command_url=str(evt.session.command_url),
                             is_live=bool(evt.session.is_live), started_at=self.now(), status="started")
            self.streams[stream_id] = st
            if stream_id not in eng.streams: eng.streams.append(stream_id)

        with SessionLocal() as s:
            s.merge(EngineRow(engine_key=eng.container_id, container_id=evt.container_id, host=eng.host, port=eng.port,
                              labels=eng.labels, first_seen=eng.first_seen, last_seen=eng.last_seen))
            s.merge(StreamRow(id=stream_id, engine_key=eng.container_id, key_type=st.key_type, key=st.key,
                              playback_session_id=st.playback_session_id, stat_url=st.stat_url, command_url=st.command_url,
                              is_live=st.is_live, started_at=st.started_at, status=st.status))
            s.commit()
        return st

    def on_stream_ended(self, evt: StreamEndedEvent) -> Optional[StreamState]:
        with self._lock:
            st: Optional[StreamState] = None
            if evt.stream_id and evt.stream_id in self.streams:
                st = self.streams[evt.stream_id]
            else:
                for s in reversed(list(self.streams.values())):
                    if s.container_id == (evt.container_id or s.container_id) and s.ended_at is None:
                        st = s; break
            if not st: return None
            st.ended_at = self.now(); st.status = "ended"
        with SessionLocal() as s:
            row = s.get(StreamRow, st.id)
            if row:
                row.ended_at = st.ended_at; row.status = st.status; s.commit()
        return st

    def list_engines(self) -> List[EngineState]:
        with self._lock:
            return list(self.engines.values())

    def get_engine(self, container_id: str) -> Optional[EngineState]:
        with self._lock:
            return self.engines.get(container_id)

    def list_streams(self, status: Optional[str] = None, container_id: Optional[str] = None) -> List[StreamState]:
        with self._lock:
            res = list(self.streams.values())
            if status: res = [s for s in res if s.status == status]
            if container_id: res = [s for s in res if s.container_id == container_id]
            return res

    def get_stream(self, stream_id: str) -> Optional[StreamState]:
        with self._lock:
            return self.streams.get(stream_id)

    def get_stream_stats(self, stream_id: str):
        with self._lock:
            return self.stream_stats.get(stream_id, [])

    def append_stat(self, stream_id: str, snap: StreamStatSnapshot):
        with self._lock:
            arr = self.stream_stats.setdefault(stream_id, [])
            arr.append(snap)
            from ..core.config import cfg as _cfg
            if len(arr) > _cfg.STATS_HISTORY_MAX:
                del arr[: len(arr) - _cfg.STATS_HISTORY_MAX]
        with SessionLocal() as s:
            s.add(StatRow(stream_id=stream_id, ts=snap.ts, peers=snap.peers, speed_down=snap.speed_down,
                          speed_up=snap.speed_up, downloaded=snap.downloaded, uploaded=snap.uploaded, status=snap.status))
            s.commit()

    def load_from_db(self):
        from ..models.db_models import EngineRow, StreamRow
        with SessionLocal() as s:
            for e in s.query(EngineRow).all():
                self.engines[e.engine_key] = EngineState(container_id=e.engine_key, host=e.host, port=e.port,
                                                         labels=e.labels or {}, first_seen=e.first_seen, last_seen=e.last_seen, streams=[])
            for r in s.query(StreamRow).filter(StreamRow.status=="started").all():
                st = StreamState(id=r.id, key_type=r.key_type, key=r.key, container_id=r.engine_key,
                                 playback_session_id=r.playback_session_id, stat_url=r.stat_url, command_url=r.command_url,
                                 is_live=r.is_live, started_at=r.started_at, status=r.status)
                self.streams[st.id] = st
                eng = self.engines.get(r.engine_key)
                if eng and st.id not in eng.streams: eng.streams.append(st.id)

state = State()

def load_state_from_db():
    state.load_from_db()
