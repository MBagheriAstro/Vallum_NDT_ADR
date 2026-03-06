"""Serve composite and processed images."""

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from .. import config

router = APIRouter(prefix="/api", tags=["images"])


@router.get("/images/view/{filename}")
async def view_image(filename: str) -> FileResponse:
    path = config.COMPOSITE_DIR / filename
    if not path.exists():
        path = config.CAPTURE_DIR / filename
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return FileResponse(path)
