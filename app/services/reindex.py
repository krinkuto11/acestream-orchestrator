from .ports import alloc
from .health import list_managed
from .provisioner import ACESTREAM_LABEL_HTTP, ACESTREAM_LABEL_HTTPS, HOST_LABEL_HTTP, HOST_LABEL_HTTPS
from .state import state
from ..models.schemas import EngineState

def reindex_existing():
    for c in list_managed():
        lbl = c.labels or {}
        try:
            if ACESTREAM_LABEL_HTTP in lbl: alloc.reserve_http(int(lbl[ACESTREAM_LABEL_HTTP]))
            if ACESTREAM_LABEL_HTTPS in lbl: alloc.reserve_https(int(lbl[ACESTREAM_LABEL_HTTPS]))
        except Exception: pass
        try:
            if HOST_LABEL_HTTP in lbl: alloc.reserve_host(int(lbl[HOST_LABEL_HTTP]))
            if HOST_LABEL_HTTPS in lbl: alloc.reserve_host(int(lbl[HOST_LABEL_HTTPS]))
        except Exception: pass
        key = c.id
        if key not in state.engines:
            host = "127.0.0.1"
            port = int(lbl.get(HOST_LABEL_HTTP) or 0)
            now = state.now()
            state.engines[key] = EngineState(container_id=key, host=host, port=port, labels=lbl, first_seen=now, last_seen=now, streams=[])
