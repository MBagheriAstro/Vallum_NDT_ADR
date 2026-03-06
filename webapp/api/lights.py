"""Lights API: set one, off all, on all."""

from fastapi import APIRouter, HTTPException

from .. import config
from ..models import LightControl
from ..hardware import gpioset, lights_on_sync, lights_off_sync
from ..core import logger

router = APIRouter(prefix="/api", tags=["lights"])


@router.post("/lights/set")
async def set_light(payload: LightControl) -> dict:
    if payload.light_id not in config.LIGHT_LINE_OFFSETS:
        raise HTTPException(status_code=400, detail="light_id must be 1, 2, 3, or 4")
    if payload.intensity <= 0:
        gpioset(config.LIGHT_LINE_OFFSETS[payload.light_id], 0)
        state = "off"
    else:
        gpioset(config.LIGHT_LINE_OFFSETS[payload.light_id], 1)
        state = "on"
    logger.info("Light %s -> %s (intensity=%.1f)", payload.light_id, state, payload.intensity)
    return {"success": True, "light_id": payload.light_id, "state": state, "intensity": payload.intensity}


@router.post("/lights/off")
async def lights_off() -> dict:
    for offset in config.LIGHT_LINE_OFFSETS.values():
        gpioset(offset, 0)
    logger.info("All lights OFF")
    return {"success": True}


@router.post("/lights/on-all")
async def lights_on_all() -> dict:
    for offset in config.LIGHT_LINE_OFFSETS.values():
        gpioset(offset, 1)
    logger.info("All lights ON")
    return {"success": True}
