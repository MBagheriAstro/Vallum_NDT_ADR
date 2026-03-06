"""Inspection API: start/stop/single, status, metadata, sensors."""

import asyncio
from fastapi import APIRouter, HTTPException

from ..models import InspectionStartPayload, InspectionMetadata
from ..services import (
    inspection_state,
    perform_one_ball_inspection,
    reset_inspection_stats,
    set_flip_duration_sec,
    get_flip_duration_sec,
    start_inspection_loop,
    stop_inspection_requested,
    stop_inspection_immediate,
    set_current_metadata,
)
from ..services.inspection import INSPECTION_RUNNING, INSPECTION_TASK
from ..hardware import read_ball_sensor
from ..core import logger

router = APIRouter(prefix="/api", tags=["inspection"])


@router.get("/inspection/status")
async def inspection_status() -> dict:
    out = inspection_state()
    return {"success": True, **out}


@router.post("/inspection/start")
async def inspection_start(payload: InspectionStartPayload) -> dict:
    if INSPECTION_RUNNING:
        raise HTTPException(status_code=400, detail="Inspection already running")
    set_flip_duration_sec(payload.flip_duration)
    reset_inspection_stats()
    start_inspection_loop()
    logger.info("Inspection started (flip_duration=%.2fs)", get_flip_duration_sec())
    return {"success": True, "message": "Inspection started"}


@router.post("/inspection/stop")
async def inspection_stop() -> dict:
    if not INSPECTION_RUNNING:
        raise HTTPException(status_code=400, detail="Inspection not running")
    stop_inspection_requested()
    logger.info("Stop requested")
    return {"success": True, "message": "Stop requested"}


@router.post("/inspection/stop-immediate")
async def inspection_stop_immediate() -> dict:
    if not INSPECTION_RUNNING:
        raise HTTPException(status_code=400, detail="Inspection not running")
    await stop_inspection_immediate()
    logger.info("Inspection stopped immediately")
    return {"success": True, "message": "Inspection stopped immediately"}


@router.post("/inspection/single-inspection")
async def single_inspection(payload: InspectionStartPayload) -> dict:
    if INSPECTION_RUNNING:
        raise HTTPException(status_code=400, detail="Inspection already running")
    set_flip_duration_sec(payload.flip_duration)
    from ..services.inspection import run_single_inspection_async
    result = await run_single_inspection_async()
    return result


@router.post("/inspection/metadata")
async def set_metadata(payload: InspectionMetadata) -> dict:
    set_current_metadata(payload.model_dump())
    logger.info("Updated inspection metadata: %s", payload.model_dump())
    return {"success": True}


@router.get("/inspection/metadata")
async def get_metadata() -> dict:
    from ..services.inspection import get_current_metadata
    return {"success": True, "metadata": get_current_metadata()}


@router.get("/sensors/ball")
async def ball_sensor() -> dict:
    return await asyncio.to_thread(read_ball_sensor)
