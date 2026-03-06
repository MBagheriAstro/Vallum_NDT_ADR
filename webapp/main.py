"""Vallum Jetson Dashboard — single entry point for the web app."""

import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Allow running as script from webapp dir: python main.py
_webapp_dir = Path(__file__).resolve().parent
_repo_root = _webapp_dir.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from webapp import config
from webapp.core import init_db, logger
from webapp.api import (
    lights_router,
    actuators_router,
    motors_router,
    system_router,
    inspection_router,
    history_router,
    cameras_router,
    images_router,
    logs_router,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    logger.info("Vallum Jetson dashboard server started")
    yield


app = FastAPI(
    title="Vallum Jetson Dashboard",
    description="Unified lights/motors/inspection dashboard running on Jetson",
    version="0.1.0",
    lifespan=lifespan,
)

# Ensure capture and composite dirs exist
config.CAPTURE_DIR.mkdir(parents=True, exist_ok=True)
config.COMPOSITE_DIR.mkdir(parents=True, exist_ok=True)

app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

app.include_router(lights_router)
app.include_router(actuators_router)
app.include_router(motors_router)
app.include_router(system_router)
app.include_router(inspection_router)
app.include_router(history_router)
app.include_router(cameras_router)
app.include_router(images_router)
app.include_router(logs_router)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(config.STATIC_DIR / "index.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webapp.main:app", host="0.0.0.0", port=8080, reload=True)
