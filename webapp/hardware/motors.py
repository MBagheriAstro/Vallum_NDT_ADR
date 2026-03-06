"""Motor HAT (Adafruit MotorKit): actuators ACT1–ACT3, kick motor M1."""

import asyncio
import threading
import time
from typing import Any

from fastapi import HTTPException

from .. import config
from .gpio import gpioget

try:
    from adafruit_motorkit import MotorKit  # type: ignore[import]
except ImportError:
    MotorKit = None  # type: ignore[assignment]

_kit_lock = threading.Lock()
_kit_instance: Any = None

actuator_state: dict[str, str] = {name: "retracted" for name in config.ACTUATOR_NAMES}
actuator_state_lock = threading.Lock()


def set_actuator_state(name: str, state: str) -> None:
    with actuator_state_lock:
        actuator_state[name] = state


def get_kit() -> Any:
    if MotorKit is None:
        raise HTTPException(
            status_code=503,
            detail="Adafruit MotorKit not available (Motor HAT not installed or libraries missing).",
        )
    global _kit_instance
    with _kit_lock:
        if _kit_instance is None:
            try:
                _kit_instance = MotorKit()
            except Exception as exc:
                raise HTTPException(status_code=503, detail=f"Failed to initialize MotorKit: {exc}")
        return _kit_instance


def run_motor_blocking(motor_index: int, action: str, duration: float) -> None:
    kit = get_kit()
    motor = getattr(kit, f"motor{motor_index}")
    if action == "extend":
        motor.throttle = -1.0
        try:
            time.sleep(duration)
        finally:
            motor.throttle = 0.0
    elif action == "retract":
        motor.throttle = 1.0
        try:
            time.sleep(duration)
        finally:
            motor.throttle = 0.0
    elif action == "stop":
        motor.throttle = 0.0
    else:
        raise HTTPException(status_code=400, detail="action must be 'extend', 'retract', or 'stop'")


def kick_until_blade() -> None:
    """Run motor1 until blade sensor (line 52) is LOW or timeout."""
    kit = get_kit()
    motor = kit.motor1
    motor.throttle = 1.0
    try:
        time.sleep(0.5)
        max_wait = 10.0
        start = time.time()
        while time.time() - start < max_wait:
            if gpioget(config.BLADE_LINE_OFFSET) == 0:
                break
            time.sleep(0.01)
    finally:
        motor.throttle = 0.0


async def retract_all_actuators_async() -> None:
    duration = 2.0
    async def task(idx: int) -> None:
        await asyncio.to_thread(run_motor_blocking, idx, "retract", duration)
    with actuator_state_lock:
        for name in config.ACTUATOR_NAMES:
            actuator_state[name] = "retracting"
    await asyncio.gather(*[task(i) for i in (2, 3, 4)])
    with actuator_state_lock:
        for name in config.ACTUATOR_NAMES:
            actuator_state[name] = "retracted"


async def clear_stage_async() -> None:
    """ACT1 extend/retract then ACT2 extend/retract (no 409 check)."""
    duration = 2.0
    pause = 0.5
    with actuator_state_lock:
        actuator_state["ACT1"] = "extending"
    await asyncio.to_thread(run_motor_blocking, 2, "extend", duration)
    with actuator_state_lock:
        actuator_state["ACT1"] = "extended"
    await asyncio.sleep(pause)
    with actuator_state_lock:
        actuator_state["ACT1"] = "retracting"
    await asyncio.to_thread(run_motor_blocking, 2, "retract", duration)
    with actuator_state_lock:
        actuator_state["ACT1"] = "retracted"
    await asyncio.sleep(pause)
    with actuator_state_lock:
        actuator_state["ACT2"] = "extending"
    await asyncio.to_thread(run_motor_blocking, 3, "extend", duration)
    with actuator_state_lock:
        actuator_state["ACT2"] = "extended"
    await asyncio.sleep(pause)
    with actuator_state_lock:
        actuator_state["ACT2"] = "retracting"
    await asyncio.to_thread(run_motor_blocking, 3, "retract", duration)
    with actuator_state_lock:
        actuator_state["ACT2"] = "retracted"


async def run_act1_extend_retract_async() -> None:
    duration = 2.0
    pause = 0.5
    with actuator_state_lock:
        actuator_state["ACT1"] = "extending"
    await asyncio.to_thread(run_motor_blocking, 2, "extend", duration)
    with actuator_state_lock:
        actuator_state["ACT1"] = "extended"
    await asyncio.sleep(pause)
    with actuator_state_lock:
        actuator_state["ACT1"] = "retracting"
    await asyncio.to_thread(run_motor_blocking, 2, "retract", duration)
    with actuator_state_lock:
        actuator_state["ACT1"] = "retracted"


async def run_act2_extend_retract_async() -> None:
    duration = 2.0
    pause = 0.5
    with actuator_state_lock:
        actuator_state["ACT2"] = "extending"
    await asyncio.to_thread(run_motor_blocking, 3, "extend", duration)
    with actuator_state_lock:
        actuator_state["ACT2"] = "extended"
    await asyncio.sleep(pause)
    with actuator_state_lock:
        actuator_state["ACT2"] = "retracting"
    await asyncio.to_thread(run_motor_blocking, 3, "retract", duration)
    with actuator_state_lock:
        actuator_state["ACT2"] = "retracted"
