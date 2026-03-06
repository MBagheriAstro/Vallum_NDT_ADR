"""Actuators API: control ACT1/2/3, retract all, clear stage."""

from fastapi import APIRouter, HTTPException

from .. import config
from ..models import ActuatorControl
from ..hardware import (
    run_motor_blocking,
    actuator_state,
    actuator_state_lock,
    set_actuator_state,
    retract_all_actuators_async,
    clear_stage_async,
)
from ..core import logger

router = APIRouter(prefix="/api", tags=["actuators"])


@router.post("/actuators/control")
async def control_actuator(payload: ActuatorControl) -> dict:
    if payload.actuator_name not in config.ACTUATOR_TO_MOTOR_INDEX:
        raise HTTPException(status_code=400, detail="actuator_name must be ACT1, ACT2, or ACT3")
    if payload.action not in ("extend", "retract"):
        raise HTTPException(status_code=400, detail="action must be 'extend' or 'retract'")
    motor_index = config.ACTUATOR_TO_MOTOR_INDEX[payload.actuator_name]
    if payload.action == "extend":
        with actuator_state_lock:
            for name, state in actuator_state.items():
                if name != payload.actuator_name and state != "retracted":
                    logger.warning("Safety interlock blocked EXTEND of %s; %s is %s", payload.actuator_name, name, state)
                    raise HTTPException(status_code=409, detail="Safety interlock: retract other actuator first.")
        set_actuator_state(payload.actuator_name, "extending")
    else:
        set_actuator_state(payload.actuator_name, "retracting")
    import asyncio
    await asyncio.to_thread(run_motor_blocking, motor_index, payload.action, payload.duration)
    if payload.action == "extend":
        set_actuator_state(payload.actuator_name, "extended")
    else:
        set_actuator_state(payload.actuator_name, "retracted")
    logger.info("Actuator %s (motor %s) %s for %.2fs", payload.actuator_name, motor_index, payload.action, payload.duration)
    return {"success": True, "actuator": payload.actuator_name, "motor_index": motor_index, "action": payload.action, "duration": payload.duration}


@router.post("/actuators/retract-all")
async def retract_all() -> dict:
    await retract_all_actuators_async()
    logger.info("Retract all actuators done")
    return {"success": True}


@router.post("/actuators/clear-stage")
async def clear_stage() -> dict:
    with actuator_state_lock:
        for name in config.ACTUATOR_NAMES:
            if actuator_state[name] != "retracted":
                raise HTTPException(status_code=409, detail="All actuators must be retracted; use Retract All first.")
    import asyncio
    set_actuator_state("ACT1", "extending")
    await asyncio.to_thread(run_motor_blocking, 2, "extend", 2.0)
    set_actuator_state("ACT1", "extended")
    await asyncio.sleep(0.5)
    set_actuator_state("ACT1", "retracting")
    await asyncio.to_thread(run_motor_blocking, 2, "retract", 2.0)
    set_actuator_state("ACT1", "retracted")
    await asyncio.sleep(0.5)
    set_actuator_state("ACT2", "extending")
    await asyncio.to_thread(run_motor_blocking, 3, "extend", 2.0)
    set_actuator_state("ACT2", "extended")
    await asyncio.sleep(0.5)
    set_actuator_state("ACT2", "retracting")
    await asyncio.to_thread(run_motor_blocking, 3, "retract", 2.0)
    set_actuator_state("ACT2", "retracted")
    logger.info("Clear stage completed")
    return {"success": True, "message": "Stage cleared"}
