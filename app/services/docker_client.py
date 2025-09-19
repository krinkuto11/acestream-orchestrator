import docker
from docker.errors import APIError

def get_client():
    return docker.from_env(timeout=15)

def safe(container_call, *args, **kwargs):
    try:
        return container_call(*args, **kwargs)
    except APIError as e:
        raise RuntimeError(str(e)) from e
