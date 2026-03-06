"""Log tail for UI."""

from fastapi import APIRouter
from ..core import LOG_BUFFER

router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs")
async def get_logs(limit: int = 200) -> dict:
    if limit <= 0:
        limit = 1
    limit = min(limit, LOG_BUFFER.maxlen or limit)
    lines = list(LOG_BUFFER)[-limit:]
    return {"success": True, "lines": lines}
