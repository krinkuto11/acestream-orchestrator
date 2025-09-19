from ..core.config import cfg
from .provisioner import StartRequest, start_container
from .health import list_managed

def ensure_minimum():
    running = [c for c in list_managed() if c.status == "running"]
    deficit = cfg.MIN_REPLICAS - len(running)
    for _ in range(max(deficit, 0)):
        start_container(StartRequest(image=cfg.TARGET_IMAGE))

def scale_to(demand: int):
    desired = min(max(cfg.MIN_REPLICAS, demand), cfg.MAX_REPLICAS)
    current = list_managed()
    running = [c for c in current if c.status == "running"]
    if len(running) < desired:
        for _ in range(desired - len(running)):
            start_container(StartRequest(image=cfg.TARGET_IMAGE))
    elif len(running) > desired:
        for c in running[desired:]:
            c.stop(timeout=5); c.remove()
