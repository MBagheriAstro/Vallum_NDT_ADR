"""Motors API: HAT control (m1–m4), kick, flip."""

import asyncio
from fastapi import APIRouter, HTTPException

from ..models import MotorAction
from ..hardware import run_motor_blocking, kick_until_blade, get_flip_controller
from ..core import logger

router = APIRouter(prefix="/api", tags=["motors"])


@router.post("/motors/control")
async def control_motor(payload: MotorAction) -> dict:
    name = payload.motor.strip().lower()
    if not name.startswith("m") or name[1:] not in ("1", "2", "3", "4"):
        raise HTTPException(status_code=400, detail="motor must be m1, m2, m3, or m4")
    motor_index = int(name[1:])
    if payload.action not in ("extend", "retract", "stop"):
        raise HTTPException(status_code=400, detail="action must be extend, retract, or stop")
    await asyncio.to_thread(run_motor_blocking, motor_index, payload.action, payload.duration)
    logger.info("Motor %s %s for %.2fs", name, payload.action, payload.duration)
    return {"success": True, "motor": name, "motor_index": motor_index, "action": payload.action, "duration": payload.duration}


@router.post("/motors/kick")
async def kick_motor() -> dict:
    await asyncio.to_thread(kick_until_blade)
    logger.info("Kick motor completed")
    return {"success": True}


@router.post("/motors/flip")
async def flip_motor(payload: MotorAction) -> dict:
    duration_ms = float(payload.duration or 250.0)
    duration = max(0.05, duration_ms / 1000.0)
    logger.info("Flip motor %.0f ms (%.2fs)", duration_ms, duration)
    controller = get_flip_controller()
    await asyncio.to_thread(controller.run_for, duration)
    logger.info("Flip motor completed")
    return {"success": True, "duration": duration}
