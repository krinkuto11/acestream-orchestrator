from fastapi import Header, HTTPException
from ..core.config import cfg

def require_api_key(authorization: str | None = Header(None)):
    if not cfg.API_KEY:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    token = authorization.split(" ", 1)[1]
    if token != cfg.API_KEY:
        raise HTTPException(status_code=403, detail="invalid token")
