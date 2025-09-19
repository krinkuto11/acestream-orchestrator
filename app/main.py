from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Query, Depends, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from typing import Optional, List
from datetime import datetime
import asyncio
import os

from .utils.logging import setup
from .core.config import cfg
from .services.autoscaler import ensure_minimum, scale_to
from .services.provisioner import StartRequest, start_container, stop_container, AceProvisionRequest, AceProvisionResponse, start_acestream, HOST_LABEL_HTTP
from .services.health import sweep_idle
from .services.inspect import inspect_container, ContainerNotFound
from .services.state import state, load_state_from_db
from .models.schemas import StreamStartedEvent, StreamEndedEvent, EngineState, StreamState, StreamStatSnapshot
from .services.collector import collector
from .services.metrics import metrics_app, orch_events_started, orch_events_ended, orch_streams_active, orch_provision_total
from .services.auth import require_api_key
from .services.db import engine
from .models.db_models import Base
from .services.reindex import reindex_existing

setup()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    Base.metadata.create_all(bind=engine)
    ensure_minimum()
    asyncio.create_task(collector.start())
    load_state_from_db()
    reindex_existing()
    
    yield
    
    # Shutdown
    await collector.stop()

app = FastAPI(title="On-Demand Orchestrator", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files with validation
panel_dir = "app/static/panel"
if os.path.exists(panel_dir) and os.path.isdir(panel_dir):
    app.mount("/panel", StaticFiles(directory=panel_dir, html=True), name="panel")
else:
    import logging
    logging.warning(f"Panel directory {panel_dir} not found. /panel endpoint will not be available.")

app.mount("/metrics", metrics_app)

# Provisioning
@app.post("/provision", dependencies=[Depends(require_api_key)])
def provision(req: StartRequest):
    cid = start_container(req)
    orch_provision_total.labels("generic").inc()
    return {"container_id": cid}

@app.post("/provision/acestream", response_model=AceProvisionResponse, dependencies=[Depends(require_api_key)])
def provision_acestream(req: AceProvisionRequest):
    orch_provision_total.labels("acestream").inc()
    return start_acestream(req)

@app.post("/scale/{demand}", dependencies=[Depends(require_api_key)])
def scale(demand: int):
    scale_to(demand)
    return {"scaled_to": demand}

@app.post("/gc", dependencies=[Depends(require_api_key)])
def garbage_collect():
    sweep_idle()
    return {"status": "ok"}

@app.delete("/containers/{container_id}", dependencies=[Depends(require_api_key)])
def delete(container_id: str):
    stop_container(container_id)
    return {"deleted": container_id}

@app.get("/containers/{container_id}")
def get_container(container_id: str):
    try:
        return inspect_container(container_id)
    except ContainerNotFound:
        raise HTTPException(status_code=404, detail="container not found")

# Events
@app.post("/events/stream_started", response_model=StreamState, dependencies=[Depends(require_api_key)])
def ev_stream_started(evt: StreamStartedEvent):
    orch_events_started.inc(); orch_streams_active.inc()
    return state.on_stream_started(evt)

@app.post("/events/stream_ended", dependencies=[Depends(require_api_key)])
def ev_stream_ended(evt: StreamEndedEvent, bg: BackgroundTasks):
    st = state.on_stream_ended(evt)
    if st: orch_events_ended.inc(); orch_streams_active.dec()
    if cfg.AUTO_DELETE and st:
        def _auto():
            cid = st.container_id
            for i in range(3):
                try:
                    stop_container(cid); return
                except Exception:
                    from .services.health import list_managed
                    try:
                        for c in list_managed():
                            if (c.labels or {}).get("stream_id") == st.id:
                                stop_container(c.id); return
                            import urllib.parse
                            pu = urllib.parse.urlparse(st.stat_url)
                            host_port = pu.port
                            if (c.labels or {}).get(HOST_LABEL_HTTP) == str(host_port):
                                stop_container(c.id); return
                    except Exception:
                        pass
                    import time; time.sleep(1 * (i+1))
        bg.add_task(_auto)
    return {"updated": bool(st), "stream": st}

# Read APIs
@app.get("/engines", response_model=List[EngineState])
def get_engines():
    return state.list_engines()

@app.get("/engines/{container_id}")
def get_engine(container_id: str):
    eng = state.get_engine(container_id)
    if not eng:
        return {"error": "not found"}
    streams = state.list_streams(container_id=container_id)
    return {"engine": eng, "streams": streams}

@app.get("/streams", response_model=List[StreamState])
def get_streams(status: Optional[str] = Query(None, pattern="^(started|ended)$"), container_id: Optional[str] = None):
    return state.list_streams(status=status, container_id=container_id)

@app.get("/streams/{stream_id}/stats", response_model=List[StreamStatSnapshot])
def get_stream_stats(stream_id: str, since: Optional[datetime] = None):
    snaps = state.get_stream_stats(stream_id)
    if since:
        snaps = [x for x in snaps if x.ts >= since]
    return snaps

# by-label
from .services.inspect import inspect_container
from .services.health import list_managed
@app.get("/by-label", dependencies=[Depends(require_api_key)])
def by_label(key: str, value: str):
    res = []
    for c in list_managed():
        if (c.labels or {}).get(key) == value:
            try:
                res.append(inspect_container(c.id))
            except Exception:
                continue
    return res
