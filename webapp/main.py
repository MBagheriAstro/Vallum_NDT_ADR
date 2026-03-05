from pathlib import Path
import subprocess
import threading
import asyncio
import time
import logging
from collections import deque
import sqlite3
from datetime import datetime
from typing import Any, Dict, List, Optional
import os

from fastapi import FastAPI, HTTPException, Body
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

try:
    import gpiod  # type: ignore[import]
except ImportError:
    gpiod = None  # type: ignore[assignment]

try:
    import cv2  # type: ignore[import]
except ImportError:
    cv2 = None  # type: ignore[assignment]

try:
    # Local YOLO inference helper and ball extraction (from HQ code)
    from .inference_yolo import run_inference_on_paths as _run_yolo_inference  # type: ignore[import]
except Exception:  # pragma: no cover - fallback if package import fails
    try:
        from inference_yolo import run_inference_on_paths as _run_yolo_inference  # type: ignore[import]
    except Exception:  # pragma: no cover
        _run_yolo_inference = None  # type: ignore[assignment]

app = FastAPI(
    title="Vallum Jetson Dashboard",
    description="Unified lights/motors/inspection dashboard running on Jetson",
    version="0.1.0",
)

# Resolve static directory relative to this file so it works no matter
# where the process is started from (project root or webapp/).
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CAPTURE_DIR = STATIC_DIR / "captures"
CAPTURE_DIR.mkdir(exist_ok=True)
COMPOSITE_DIR = STATIC_DIR / "composites"
COMPOSITE_DIR.mkdir(exist_ok=True)

DB_PATH = BASE_DIR / "inspection_history.db"

# Simple in-memory log buffer for the UI (tail of recent messages)
LOG_BUFFER: "deque[str]" = deque(maxlen=500)


class _UILogHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
        except Exception:  # pragma: no cover - defensive
            msg = record.getMessage()
        LOG_BUFFER.append(msg)


_ui_log_handler = _UILogHandler()
_ui_log_handler.setLevel(logging.INFO)
_ui_log_handler.setFormatter(
    logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
)
logger = logging.getLogger("vallum.webapp")
logger.setLevel(logging.INFO)
logger.addHandler(_ui_log_handler)
logger.propagate = False
logger.info("Vallum Jetson dashboard server started")

# SQLite connection for inspection history
_DB_CONN: "sqlite3.Connection | None" = None
_DB_LOCK = threading.Lock()


def _get_db_conn() -> sqlite3.Connection:
    global _DB_CONN
    with _DB_LOCK:
        if _DB_CONN is None:
            _DB_CONN = sqlite3.connect(DB_PATH, check_same_thread=False)
            _DB_CONN.row_factory = sqlite3.Row
        return _DB_CONN


def _init_db() -> None:
    conn = _get_db_conn()
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS inspection_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            lot_number TEXT,
            mfg_name TEXT,
            mfg_part_number TEXT,
            material TEXT,
            ball_diameter TEXT,
            ball_diameter_mm REAL,
            customer_name TEXT,
            inspection_result TEXT,
            total_balls INTEGER,
            good_balls INTEGER,
            bad_balls INTEGER,
            no_balls INTEGER,
            composite_image_path TEXT
        )
        """
    )
    conn.commit()


_init_db()

# Serve static assets under /static and serve index.html at /
app.mount("/static", StaticFiles(directory=STATIC_DIR, html=True), name="static")


@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    """Serve the SPA shell."""
    return FileResponse(STATIC_DIR / "index.html")


# Mapping of light IDs 1–4 to gpiochip0 line offsets, based on JetsonHacks
# Orin Nano J12 header pinout:
#   - Light 1 -> J12 pin 15 -> offset 85  (GPIO12, Alt: PWM)
#   - Light 2 -> J12 pin 16 -> offset 126 (SPI1_CS1)
#   - Light 3 -> J12 pin 18 -> offset 125 (SPI1_CS0)
#   - Light 4 -> J12 pin 22 -> offset 123 (SPI1_MISO)
LIGHT_LINE_OFFSETS = {
    1: 85,
    2: 126,
    3: 125,
    4: 123,
}


class LightControl(BaseModel):
    light_id: int  # 1–4
    intensity: float  # percentage 0–100


def _gpioset(offset: int, value: int) -> None:
    """
    Call gpioset in exit mode to toggle a single line.
    Equivalent to:
      gpioset --mode=exit gpiochip0 <offset>=<value>
    """
    try:
        subprocess.run(
            ["gpioset", "--mode=exit", "gpiochip0", f"{offset}={value}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="gpioset not found on PATH. Install libgpiod-utils or run on the Jetson with gpioset available.",
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"gpioset failed: {exc.stderr or exc.stdout or exc}",
        )


def _gpioget(offset: int) -> int:
    """
    Read a single gpiochip0 line as 0/1 using gpioget.
    Returns 0 or 1; on failure defaults to 1 (no blade detected).
    """
    try:
        res = subprocess.run(
            ["gpioget", "gpiochip0", str(offset)],
            check=True,
            capture_output=True,
            text=True,
        )
        s = res.stdout.strip()
        return 1 if s == "1" else 0
    except Exception:
        # If we can't read the line, behave as if sensor says "no blade".
        return 1


@app.post("/api/lights/set")
async def api_set_light(payload: LightControl) -> dict:
    """
    For now, treat intensity as simple ON/OFF:
    - intensity <= 0 -> OFF
    - intensity > 0  -> ON
    """
    if payload.light_id not in LIGHT_LINE_OFFSETS:
        raise HTTPException(status_code=400, detail="light_id must be 1, 2, 3, or 4")

    if payload.intensity <= 0:
        _gpioset(LIGHT_LINE_OFFSETS[payload.light_id], 0)
        state = "off"
    else:
        _gpioset(LIGHT_LINE_OFFSETS[payload.light_id], 1)
        state = "on"

    logger.info("Light %s -> %s (intensity=%.1f)", payload.light_id, state, payload.intensity)
    return {"success": True, "light_id": payload.light_id, "state": state, "intensity": payload.intensity}


@app.post("/api/lights/off")
async def api_lights_off() -> dict:
    """
    Turn off all lights from the dashboard (used by TURN OFF ALL and by run/stop flows later).
    """
    for offset in LIGHT_LINE_OFFSETS.values():
        _gpioset(offset, 0)
    logger.info("All lights OFF (api_lights_off)")
    return {"success": True}


# ---------------------- Motor / Actuator control (Adafruit Motor HAT) ----------------------

try:
    from adafruit_motorkit import MotorKit  # type: ignore[import]
except ImportError:  # pragma: no cover - only present on Jetson with HAT
    MotorKit = None  # type: ignore[assignment]

_kit_lock = threading.Lock()
_kit_instance = None

# Blade sensor on Jetson Orin Nano (Pi physical pin 38, GPIO 20):
# User wiring: physical pin 38 -> gpio400 in sysfs, which corresponds to
# gpiochip0 line offset 52 in the JetsonHacks pinout.
BLADE_LINE_OFFSET = 52

# Ball-on-stage sensor (Pi physical pin 40, GPIO 21):
# Physical pin 40 on 40-pin header -> gpiochip0 line offset 51 (e.g. I2S0_SDOUT/gpio399 on Orin Nano).
# Same logic as Pi: value == 1 -> stage clear (no ball); value != 1 -> ball present.
BALL_STAGE_LINE_OFFSET = 51


def _read_ball_sensor() -> dict:
    """
    Read ball-on-stage sensor (gpiochip0 line BALL_STAGE_LINE_OFFSET).
    Returns {"success": True, "value": 0|1} where value==1 means stage clear (no ball), value!=1 means ball present.
    On read failure returns {"success": False, "error": "..."}.
    """
    try:
        val = _gpioget(BALL_STAGE_LINE_OFFSET)
        return {"success": True, "value": val, "stage_clear": val == 1}
    except Exception as e:
        return {"success": False, "error": str(e), "value": 0, "stage_clear": False}


def _get_kit() -> "MotorKit":
    if MotorKit is None:
        raise HTTPException(
            status_code=503,
            detail="Adafruit MotorKit not available (Motor HAT not installed or libraries missing).",
        )
    global _kit_instance
    with _kit_lock:
        if _kit_instance is None:
            try:
                _kit_instance = MotorKit()  # type: ignore[call-arg]
            except Exception as exc:  # pragma: no cover - hardware dependent
                raise HTTPException(status_code=503, detail=f"Failed to initialize MotorKit: {exc}")
        return _kit_instance  # type: ignore[return-value]


class ActuatorControl(BaseModel):
    actuator_name: str  # 'ACT1' | 'ACT2' | 'ACT3'
    action: str  # 'extend' | 'retract'
    duration: float = 2.0


class MotorAction(BaseModel):
    motor: str  # 'm1' | 'm2' | 'm3' | 'm4'
    action: str  # 'extend' | 'retract' | 'stop'
    duration: float = 1.0


class CameraCaptureRequest(BaseModel):
    camera_name: str  # "camera A" or "camera B"


class CameraConfig(BaseModel):
    exposure_ms: float = 100.0
    red_gain: float = 4.0
    blue_gain: float = 0.5
    analogue_gain: float = 4.0


CAMERA_CONFIG = {
    "exposure_ms": 100.0,
    "red_gain": 4.0,
    "blue_gain": 0.5,
    "analogue_gain": 4.0,
}

# Simple in-memory actuator state for safety interlocks.
ACTUATOR_NAMES = ("ACT1", "ACT2", "ACT3")
ACTUATOR_STATE = {name: "retracted" for name in ACTUATOR_NAMES}
ACTUATOR_STATE_LOCK = threading.Lock()

# Inspection run state (replicate Pi inspection flow)
INSPECTION_RUNNING = False
INSPECTION_STOP_REQUESTED = False
INSPECTION_TASK: "asyncio.Task | None" = None
INSPECTION_LOCK = threading.Lock()
CYCLE_COUNT = 0
TOTAL_BALLS = 0
GOOD_BALLS = 0
BAD_BALLS = 0
LAST_RESULT: str = "–"  # "good" | "bad" | "no_ball" | "–"
FLIP_DURATION_SEC = 0.25

# Metadata for current inspection run (set by frontend)
CURRENT_METADATA: Dict[str, Any] = {}

# Latest processed images for the Inspection Run panel
# Keys: "CAMERA_A_TOP", "CAMERA_B_TOP", "CAMERA_A_BOT", "CAMERA_B_BOT"
LAST_PROCESSED_IMAGES: Dict[str, str] = {}


def _lights_on_sync() -> None:
    for offset in LIGHT_LINE_OFFSETS.values():
        _gpioset(offset, 1)


def _lights_off_sync() -> None:
    for offset in LIGHT_LINE_OFFSETS.values():
        _gpioset(offset, 0)


async def _inspection_lights_on() -> None:
    await asyncio.to_thread(_lights_on_sync)


async def _inspection_lights_off() -> None:
    await asyncio.to_thread(_lights_off_sync)


async def _inspection_retract_all() -> None:
    duration = 2.0
    async def _task(idx: int) -> None:
        await asyncio.to_thread(_run_motor_blocking, idx, "retract", duration)
    with ACTUATOR_STATE_LOCK:
        for name in ACTUATOR_NAMES:
            ACTUATOR_STATE[name] = "retracting"
    await asyncio.gather(*[_task(i) for i in (2, 3, 4)])
    with ACTUATOR_STATE_LOCK:
        for name in ACTUATOR_NAMES:
            ACTUATOR_STATE[name] = "retracted"


@app.get("/api/logs")
async def api_get_logs(limit: int = 200) -> dict:
    """
    Return the most recent server log lines for display in the UI log panel.
    """
    if limit <= 0:
        limit = 1
    limit = min(limit, LOG_BUFFER.maxlen or limit)
    lines = list(LOG_BUFFER)[-limit:]
    return {"success": True, "lines": lines}


def _actuator_to_motor_index(actuator_name: str) -> int:
    mapping = {"ACT1": 2, "ACT2": 3, "ACT3": 4}
    if actuator_name not in mapping:
        raise HTTPException(status_code=400, detail="actuator_name must be 'ACT1', 'ACT2', or 'ACT3'")
    return mapping[actuator_name]


def _run_motor_blocking(motor_index: int, action: str, duration: float) -> None:
    kit = _get_kit()
    motor = getattr(kit, f"motor{motor_index}")
    if action == "extend":
        motor.throttle = -1.0
        try:
            asyncio.run(asyncio.sleep(duration))  # this will fail if called in running loop
        except RuntimeError:
            # Fallback to time.sleep when already in an event loop
            import time as _time

            _time.sleep(duration)
        finally:
            motor.throttle = 0.0
    elif action == "retract":
        motor.throttle = 1.0
        try:
            asyncio.run(asyncio.sleep(duration))
        except RuntimeError:
            import time as _time

            _time.sleep(duration)
        finally:
            motor.throttle = 0.0
    elif action == "stop":
        motor.throttle = 0.0
    else:
        raise HTTPException(status_code=400, detail="action must be 'extend', 'retract', or 'stop'")


def _kick_until_blade() -> None:
    """
    Kick motor using Motor HAT motor1 and stop when the blade sensor
    (on gpiochip0 line BLADE_LINE_OFFSET) indicates the blade is present.

    Mirrors the old Pi behavior:
      - Start motor1
      - Wait a short settling time
      - Poll blade sensor; stop when it goes LOW (0) or after a timeout.
    """
    kit = _get_kit()
    motor = kit.motor1
    # Start motor in the same direction we used for "retract" in the simple version.
    motor.throttle = 1.0
    try:
        # Give the mechanism a moment to move before trusting the sensor.
        time.sleep(0.5)

        max_wait = 10.0
        start = time.time()
        while time.time() - start < max_wait:
            state = _gpioget(BLADE_LINE_OFFSET)
            # Active-low: 0 means blade detected.
            if state == 0:
                break
            time.sleep(0.01)
    finally:
        motor.throttle = 0.0


@app.post("/api/actuators/control")
async def api_control_actuator(payload: ActuatorControl) -> dict:
    motor_index = _actuator_to_motor_index(payload.actuator_name)
    if payload.action not in ("extend", "retract"):
        raise HTTPException(status_code=400, detail="action must be 'extend' or 'retract'")

    # Safety interlock: only one actuator may be extended at a time.
    if payload.action == "extend":
        with ACTUATOR_STATE_LOCK:
            for name, state in ACTUATOR_STATE.items():
                if name != payload.actuator_name and state != "retracted":
                    logger.warning(
                        "Safety interlock blocked EXTEND of %s; %s is currently %s",
                        payload.actuator_name,
                        name,
                        state,
                    )
                    raise HTTPException(
                        status_code=409,
                        detail="Safety interlock: another actuator is extended; retract it first.",
                    )
            ACTUATOR_STATE[payload.actuator_name] = "extending"
    else:
        with ACTUATOR_STATE_LOCK:
            ACTUATOR_STATE[payload.actuator_name] = "retracting"

    await asyncio.to_thread(_run_motor_blocking, motor_index, payload.action, payload.duration)
    # Update final state after motion completes.
    with ACTUATOR_STATE_LOCK:
        if payload.action == "extend":
            ACTUATOR_STATE[payload.actuator_name] = "extended"
        else:
            ACTUATOR_STATE[payload.actuator_name] = "retracted"

    logger.info(
        "Actuator %s (motor %s) %s for %.2fs",
        payload.actuator_name,
        motor_index,
        payload.action,
        payload.duration,
    )
    return {
        "success": True,
        "actuator": payload.actuator_name,
        "motor_index": motor_index,
        "action": payload.action,
        "duration": payload.duration,
    }


@app.post("/api/actuators/retract-all")
async def api_retract_all_actuators() -> dict:
    # Retract ACT1–3 (motors 2–4) simultaneously for a fixed duration.
    duration = 2.0

    async def _task(idx: int) -> None:
        await asyncio.to_thread(_run_motor_blocking, idx, "retract", duration)

    with ACTUATOR_STATE_LOCK:
        for name in ACTUATOR_NAMES:
            ACTUATOR_STATE[name] = "retracting"

    await asyncio.gather(*[_task(i) for i in (2, 3, 4)])

    with ACTUATOR_STATE_LOCK:
        for name in ACTUATOR_NAMES:
            ACTUATOR_STATE[name] = "retracted"

    logger.info("Retract all actuators (motors 2–4) for %.2fs", duration)
    return {"success": True}


@app.post("/api/actuators/clear-stage")
async def api_clear_stage() -> dict:
    """
    Clear the inspection stage: sequential sweep with ACT1 then ACT2.
    Matches Pi behavior: extend then retract ACT1, then extend then retract ACT2,
    with short pauses between, to physically clear the stage (e.g. push debris off).
    """
    duration = 2.0
    pause = 0.5

    # ACT1: extend then retract
    with ACTUATOR_STATE_LOCK:
        for name in ACTUATOR_NAMES:
            if ACTUATOR_STATE[name] != "retracted":
                raise HTTPException(
                    status_code=409,
                    detail="All actuators must be retracted before clear stage; use Retract All first.",
                )
        ACTUATOR_STATE["ACT1"] = "extending"
    await asyncio.to_thread(_run_motor_blocking, 2, "extend", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT1"] = "extended"
    await asyncio.sleep(pause)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT1"] = "retracting"
    await asyncio.to_thread(_run_motor_blocking, 2, "retract", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT1"] = "retracted"
    await asyncio.sleep(pause)

    # ACT2: extend then retract
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "extending"
    await asyncio.to_thread(_run_motor_blocking, 3, "extend", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "extended"
    await asyncio.sleep(pause)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "retracting"
    await asyncio.to_thread(_run_motor_blocking, 3, "retract", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "retracted"

    logger.info("Clear stage completed: ACT1 and ACT2 sweep done")
    return {"success": True, "message": "Stage cleared (ACT1 and ACT2 extended and retracted sequentially)"}


async def _check_stage_clearance() -> bool:
    """
    Return True if stage is clear (ball sensor value == 1).
    If not clear, run clear-stage sequence (ACT1 then ACT2 sweep) then re-check.
    """
    def _read() -> dict:
        return _read_ball_sensor()
    r = await asyncio.to_thread(_read)
    if not r.get("success"):
        logger.warning("Ball sensor read failed: %s", r.get("error"))
        return False
    if r.get("stage_clear", r.get("value") == 1):
        return True
    logger.info("Stage not clear; running clear-stage sequence")
    await _inspection_clear_stage()
    r2 = await asyncio.to_thread(_read)
    return r2.get("success") and (r2.get("stage_clear") or r2.get("value") == 1)


async def _feed_ball_to_stage() -> bool:
    """
    ACT3 extend → kick motor (until blade sensor) → wait 3s → ACT3 retract.
    Returns True if steps completed without error.
    """
    duration = 2.0
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT3"] = "extending"
    await asyncio.to_thread(_run_motor_blocking, 4, "extend", duration)  # ACT3 = motor 4
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT3"] = "extended"
    try:
        await asyncio.to_thread(_kick_until_blade)
    except Exception as e:
        logger.warning("Kick motor error: %s", e)
    await asyncio.sleep(3)  # ball settle
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT3"] = "retracting"
    await asyncio.to_thread(_run_motor_blocking, 4, "retract", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT3"] = "retracted"
    return True


def _run_extract_and_infer_sync(image_paths: list) -> dict:
    """
    Run ball extraction + inference on the given image paths (2 for TOP or 2 for BOT).
    Returns {"defect_found": bool, "prediction": str, "probability": float}.
    """
    if _run_yolo_inference is None:
        logger.warning("YOLO inference helper not available; returning stub Normal result")
        return {"defect_found": False, "prediction": "Normal", "probability": 1.0}
    return _run_yolo_inference(image_paths, logger)


# ---------------------- Single inference worker (TOP first, BOT waits in buffer) ----------------------
# One thread processes one set at a time. When TOP is submitted we create a slot; when BOT
# is submitted (same cycle) BOT waits until TOP is done, then we process BOT and notify webapp.

_INFERENCE_LOCK = threading.Lock()
_INFERENCE_CV = threading.Condition(_INFERENCE_LOCK)
_INFERENCE_SLOT: "tuple | None" = None  # (top_paths, bot_paths, future, loop) when active; bot_paths None until BOT submitted
_INFERENCE_WORKER_STARTED = False


def _inference_worker_thread() -> None:
    """
    Single worker: start TOP as soon as slot has top_paths (no wait for BOT).
    After TOP is done, wait for BOT in slot (buffer); then process BOT, combine, inform webapp.
    """
    global _INFERENCE_SLOT
    while True:
        # Phase 1: wait for TOP to be submitted (slot created with top_paths)
        with _INFERENCE_CV:
            _INFERENCE_CV.wait_for(lambda: _INFERENCE_SLOT is not None)
            top_paths, bot_paths, future, loop = _INFERENCE_SLOT
            # Do not clear slot yet; BOT will fill in slot[1] while we process TOP
        # Process TOP immediately (webapp is flipping + capturing BOT in parallel)
        try:
            top_result = _run_extract_and_infer_sync(top_paths)
        except Exception as e:
            top_result = {"defect_found": True, "prediction": "Defect", "probability": 0.0}
            logger.warning("TOP inference error: %s", e)
        # Phase 2: wait for BOT to be submitted (no timeout – we wait until webapp sends 2nd set)
        with _INFERENCE_CV:
            _INFERENCE_CV.wait_for(lambda: _INFERENCE_SLOT is not None and _INFERENCE_SLOT[1] is not None)
            _, bot_paths, future, loop = _INFERENCE_SLOT
            _INFERENCE_SLOT = None  # free slot for next cycle
        # Process BOT, then combine and notify
        try:
            bot_result = _run_extract_and_infer_sync(bot_paths)
        except Exception as e:
            bot_result = {"defect_found": True, "prediction": "Defect", "probability": 0.0}
            logger.warning("BOT inference error: %s", e)
        defect_found = top_result.get("defect_found") or bot_result.get("defect_found")
        prediction = "Defect" if defect_found else "Normal"
        probability = max(top_result.get("probability", 0), bot_result.get("probability", 0))
        result = {"defect_found": defect_found, "prediction": prediction, "probability": probability}
        try:
            loop.call_soon_threadsafe(future.set_result, result)
        except Exception:
            pass


def _inference_submit_top(top_paths: list) -> "asyncio.Future":
    """Submit TOP paths; returns an asyncio Future that will receive the combined result when TOP then BOT are done."""
    global _INFERENCE_SLOT, _INFERENCE_WORKER_STARTED
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    with _INFERENCE_CV:
        if _INFERENCE_SLOT is not None:
            logger.warning("Inference slot still busy; previous cycle may not have completed")
        _INFERENCE_SLOT = (top_paths, None, future, loop)
        if not _INFERENCE_WORKER_STARTED:
            _INFERENCE_WORKER_STARTED = True
            t = threading.Thread(target=_inference_worker_thread, daemon=True)
            t.start()
        _INFERENCE_CV.notify()
    return future


def _inference_submit_bot(bot_paths: list) -> None:
    """Submit BOT paths for the current cycle. They wait in the slot until TOP is done, then worker processes BOT and sets result."""
    with _INFERENCE_CV:
        if _INFERENCE_SLOT is None:
            logger.error("Inference submit_bot called but no slot (submit_top not called?)")
            return
        top_paths, _, future, loop = _INFERENCE_SLOT
        _INFERENCE_SLOT = (top_paths, bot_paths, future, loop)
        _INFERENCE_CV.notify()


async def _inspection_clear_stage() -> None:
    """Run clear-stage sequence (same as API, no 409 check)."""
    duration = 2.0
    pause = 0.5
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT1"] = "extending"
    await asyncio.to_thread(_run_motor_blocking, 2, "extend", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT1"] = "extended"
    await asyncio.sleep(pause)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT1"] = "retracting"
    await asyncio.to_thread(_run_motor_blocking, 2, "retract", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT1"] = "retracted"
    await asyncio.sleep(pause)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "extending"
    await asyncio.to_thread(_run_motor_blocking, 3, "extend", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "extended"
    await asyncio.sleep(pause)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "retracting"
    await asyncio.to_thread(_run_motor_blocking, 3, "retract", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "retracted"


async def _inspection_act1_extend_retract() -> None:
    """Good ball: ACT1 (motor 2) extend then retract."""
    duration = 2.0
    pause = 0.5
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT1"] = "extending"
    await asyncio.to_thread(_run_motor_blocking, 2, "extend", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT1"] = "extended"
    await asyncio.sleep(pause)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT1"] = "retracting"
    await asyncio.to_thread(_run_motor_blocking, 2, "retract", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT1"] = "retracted"


async def _inspection_act2_extend_retract() -> None:
    """Bad ball: ACT2 (motor 3) extend then retract."""
    duration = 2.0
    pause = 0.5
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "extending"
    await asyncio.to_thread(_run_motor_blocking, 3, "extend", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "extended"
    await asyncio.sleep(pause)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "retracting"
    await asyncio.to_thread(_run_motor_blocking, 3, "retract", duration)
    with ACTUATOR_STATE_LOCK:
        ACTUATOR_STATE["ACT2"] = "retracted"


def _build_composite_image(top_paths: List[str], bot_paths: List[str]) -> Optional[str]:
    """
    Build a 2x2 composite image from extracted balls for History view.
    Uses the same ball extraction as inference; saves under COMPOSITE_DIR.
    """
    if cv2 is None:
        return None
    try:
        from .ball_extraction import extract_ball  # type: ignore[import]
    except Exception:
        try:
            from ball_extraction import extract_ball  # type: ignore[import]
        except Exception:
            return None

    all_paths = top_paths + bot_paths
    balls: List[Any] = []
    for p in all_paths:
        img = cv2.imread(p)
        if img is None:
            continue
        ball = extract_ball(
            img,
            filename=Path(p).name,
            logger=None,
            normalize_size=(512, 512),
            target_ball_diameter=512,
        )
        if ball is not None:
            balls.append(ball)

    if not balls:
        return None

    # Ensure we have 4 tiles; if fewer, repeat last.
    while len(balls) < 4:
        balls.append(balls[-1])

    a_top, b_top, a_bot, b_bot = balls[:4]
    row_top = cv2.hconcat([a_top, b_top])
    row_bot = cv2.hconcat([a_bot, b_bot])
    composite = cv2.vconcat([row_top, row_bot])

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"composite_{ts}.png"
    out_path = COMPOSITE_DIR / filename
    cv2.imwrite(str(out_path), composite)
    return str(out_path)


_DISPLAY_MODEL = None  # Lazy-loaded YOLO model for processed images


def _generate_processed_images(top_paths: List[str], bot_paths: List[str]) -> None:
    """
    Generate per-view processed images (extracted ball with annotations if defects)
    for the 4 views: CAMERA_A_TOP, CAMERA_B_TOP, CAMERA_A_BOT, CAMERA_B_BOT.
    Images are saved next to the raw captures with a '_processed.png' suffix.
    """
    global LAST_PROCESSED_IMAGES, _DISPLAY_MODEL
    LAST_PROCESSED_IMAGES = {}
    if cv2 is None:
        return
    try:
        from ultralytics import YOLO  # type: ignore[import]
    except ImportError:
        logger.warning("ultralytics not installed; skipping processed images generation")
        return
    try:
        from .ball_extraction import extract_ball  # type: ignore[import]
    except Exception:
        try:
            from ball_extraction import extract_ball  # type: ignore[import]
        except Exception:
            logger.warning("ball_extraction not available; skipping processed images generation")
            return

    if _DISPLAY_MODEL is None:
        # Reuse same default path / env var convention as inference backend
        default_path = (
            BASE_DIR.parent
            / "runs"
            / "detect"
            / "yolo_defect_detection"
            / "defect_model_yolo11"
            / "weights"
            / "best.pt"
        )
        model_path = os.getenv("VALLUM_YOLO_MODEL_PATH", str(default_path))
        if not Path(model_path).exists():
            logger.warning("YOLO model file not found at %s; skipping processed images", model_path)
            return
        try:
            _DISPLAY_MODEL = YOLO(str(model_path))
            logger.info("Loaded YOLO model for processed images from %s", model_path)
        except Exception as e:
            logger.warning("Failed to load YOLO model for processed images: %s", e)
            _DISPLAY_MODEL = None
            return

    model = _DISPLAY_MODEL
    all_paths = top_paths + bot_paths
    for p in all_paths:
        img = cv2.imread(p)
        if img is None:
            continue
        ball = extract_ball(
            img,
            filename=Path(p).name,
            logger=None,
            normalize_size=(1024, 1024),
            target_ball_diameter=1024,
        )
        if ball is None:
            continue
        # Run YOLO on extracted ball
        try:
            results = model.predict(source=ball, imgsz=1024, conf=0.4, verbose=False)
        except Exception as e:
            logger.warning("YOLO prediction error for processed image %s: %s", p, e)
            continue
        img_out = ball.copy()
        h, w = img_out.shape[:2]
        # Draw boxes if any detections
        try:
            from numpy import float32 as _np_float32  # type: ignore
        except Exception:
            _np_float32 = None  # type: ignore
        any_boxes = False
        for res in results:
            boxes = getattr(res, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                try:
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    conf = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())
                    raw_class_name = getattr(model, "names", {}).get(class_id, str(class_id))
                    class_name = str(raw_class_name).title().replace("_", "-")
                except Exception:
                    continue
                x1 = max(0, min(w - 1, int(round(x1))))
                y1 = max(0, min(h - 1, int(round(y1))))
                x2 = max(0, min(w, int(round(x2))))
                y2 = max(0, min(h, int(round(y2))))
                if x2 <= x1 or y2 <= y1:
                    continue
                any_boxes = True
                cv2.rectangle(img_out, (x1, y1), (x2, y2), (0, 0, 255), 3)
                label = f"{class_name} {conf:.2f}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                cv2.rectangle(
                    img_out,
                    (x1, y1 - th - 8),
                    (x1 + tw + 4, y1),
                    (0, 0, 255),
                    -1,
                )
                cv2.putText(
                    img_out,
                    label,
                    (x1 + 2, y1 - 5),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.6,
                    (255, 255, 255),
                    2,
                )
        # Even if no boxes, we still save the extracted ball as the "processed" image
        processed_path = Path(p).with_name(Path(p).stem + "_processed.png")
        try:
            cv2.imwrite(str(processed_path), img_out)
        except Exception as e:
            logger.warning("Failed to write processed image %s: %s", processed_path, e)
            continue
        stem = processed_path.stem.upper()
        key: Optional[str] = None
        if "CAMERA_A" in stem and "TOP" in stem:
            key = "CAMERA_A_TOP"
        elif "CAMERA_B" in stem and "TOP" in stem:
            key = "CAMERA_B_TOP"
        elif "CAMERA_A" in stem and ("BOT" in stem or "BOTTOM" in stem):
            key = "CAMERA_A_BOT"
        elif "CAMERA_B" in stem and ("BOT" in stem or "BOTTOM" in stem):
            key = "CAMERA_B_BOT"
        if key:
            LAST_PROCESSED_IMAGES[key] = processed_path.name


def _save_inspection_cycle(last_result: str, composite_path: Optional[str]) -> None:
    """
    Save one inspection cycle row into SQLite.
    Uses CURRENT_METADATA and current stats.
    """
    conn = _get_db_conn()
    cur = conn.cursor()
    meta = CURRENT_METADATA or {}
    lot = meta.get("lotNumber")
    mfg = meta.get("mfgName")
    part = meta.get("mfgPart")
    material = meta.get("material")
    ball_diameter = meta.get("ballDiameter")
    customer = meta.get("customerName")
    ts = datetime.utcnow().isoformat()
    inspection_result = (
        "GOOD" if last_result == "good" else "BAD" if last_result == "bad" else "NO_BALL"
    )
    with INSPECTION_LOCK:
        total = TOTAL_BALLS
        good = GOOD_BALLS
        bad = BAD_BALLS
    # Count no_balls as total - good - bad for now
    no_balls = max(0, total - good - bad)
    cur.execute(
        """
        INSERT INTO inspection_history (
            timestamp,
            lot_number,
            mfg_name,
            mfg_part_number,
            material,
            ball_diameter,
            ball_diameter_mm,
            customer_name,
            inspection_result,
            total_balls,
            good_balls,
            bad_balls,
            no_balls,
            composite_image_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            lot,
            mfg,
            part,
            material,
            ball_diameter,
            None,
            customer,
            inspection_result,
            total,
            good,
            bad,
            no_balls,
            composite_path,
        ),
    )
    conn.commit()


# ---------------------- Inspection run (Start / Stop / Single) ----------------------

class InspectionStartPayload(BaseModel):
    flip_duration: float = 0.25


def _reset_inspection_stats() -> None:
    global CYCLE_COUNT, TOTAL_BALLS, GOOD_BALLS, BAD_BALLS, LAST_RESULT
    with INSPECTION_LOCK:
        CYCLE_COUNT = 0
        TOTAL_BALLS = 0
        GOOD_BALLS = 0
        BAD_BALLS = 0
        LAST_RESULT = "–"


def _capture_camera_to_path(cam_name: str, out_path: Path) -> bool:
    """Blocking capture of one camera to out_path. Returns True on success."""
    if cv2 is None:
        return False
    sensor_id = _map_camera_name_to_sensor_id(cam_name)
    pipeline = _build_nvargus_pipeline(sensor_id)
    cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    if not cap.isOpened():
        return False
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return False
    return cv2.imwrite(str(out_path), frame)


async def _perform_one_ball_inspection() -> dict:
    """
    One inspection cycle: retract, lights on, check stage (clear if needed), feed ball,
    check ball sensor; if ball present: capture TOP → start TOP extract+infer in background →
    flip → capture BOT → BOT extract+infer → wait TOP → combine → actuate (ACT1/ACT2), update stats.
    """
    global CYCLE_COUNT, TOTAL_BALLS, GOOD_BALLS, BAD_BALLS, LAST_RESULT, INSPECTION_RUNNING, INSPECTION_STOP_REQUESTED, FLIP_DURATION_SEC
    if not INSPECTION_RUNNING:
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    await _inspection_retract_all()
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    await _inspection_lights_on()
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    # Check stage clearance; clear stage if not clear (same order as Pi)
    clear = await _check_stage_clearance()
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    if not clear:
        logger.warning("Stage still not clear after clear-stage sequence")
    # Feed ball to stage: ACT3 extend → kick → wait 3s → ACT3 retract
    await _feed_ball_to_stage()
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    # Read ball sensor: value==1 means stage clear (no ball)
    ball_sensor = await asyncio.to_thread(_read_ball_sensor)
    if not ball_sensor.get("success"):
        logger.warning("Ball sensor read failed after feed")
    if ball_sensor.get("stage_clear") or ball_sensor.get("value") == 1:
        with INSPECTION_LOCK:
            LAST_RESULT = "no_ball"
        await _inspection_lights_off()
        return {"success": True, "ball_detected": False, "total_balls": TOTAL_BALLS, "good_balls": GOOD_BALLS, "bad_balls": BAD_BALLS}
    # Ball present: capture TOP → submit TOP to single inference worker → flip → capture BOT →
    # submit BOT (waits in buffer until TOP is done) → worker does TOP then BOT and informs us → actuate
    if cv2 is None or CAPTURE_DIR is None:
        with INSPECTION_LOCK:
            LAST_RESULT = "no_ball"
        await _inspection_lights_off()
        return {"success": True, "ball_detected": False, "total_balls": TOTAL_BALLS, "good_balls": GOOD_BALLS, "bad_balls": BAD_BALLS}
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    top_paths = [
        str(CAPTURE_DIR / f"CAMERA_A_TOP_{ts}.png"),
        str(CAPTURE_DIR / f"CAMERA_B_TOP_{ts}.png"),
    ]
    for cam_name, path in [("camera A", top_paths[0]), ("camera B", top_paths[1])]:
        if not INSPECTION_RUNNING:
            await _inspection_lights_off()
            return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
        ok = await asyncio.to_thread(_capture_camera_to_path, cam_name, Path(path))
        if not ok:
            logger.warning("TOP capture failed for %s", path)
    # Submit TOP to single inference worker (worker may start TOP immediately; we get a Future)
    inference_future = _inference_submit_top(top_paths)
    # Flip motor
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        await _inspection_lights_off()
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    controller = _get_flip_controller()
    await asyncio.to_thread(controller.run_for, FLIP_DURATION_SEC)
    # Capture BOT
    bot_paths = [
        str(CAPTURE_DIR / f"CAMERA_A_BOT_{ts}.png"),
        str(CAPTURE_DIR / f"CAMERA_B_BOT_{ts}.png"),
    ]
    for cam_name, path in [("camera A", bot_paths[0]), ("camera B", bot_paths[1])]:
        if not INSPECTION_RUNNING:
            await _inspection_lights_off()
            return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
        ok = await asyncio.to_thread(_capture_camera_to_path, cam_name, Path(path))
        if not ok:
            logger.warning("BOT capture failed for %s", path)
    # Submit BOT to same cycle; worker will process TOP first, then BOT (BOT waited in buffer), then set result
    _inference_submit_bot(bot_paths)
    # Wait for worker to finish TOP then BOT and combine; result is set on inference_future
    result = await inference_future
    defect_found = result.get("defect_found")
    prediction = result.get("prediction", "Defect" if defect_found else "Normal")
    probability = result.get("probability", 0.0)
    # Process result: Normal (and threshold) → ACT1; else ACT2. Threshold 0.75 for good (match Pi).
    CONFIDENCE_THRESHOLD = 0.75
    is_good = prediction == "Normal" and probability >= CONFIDENCE_THRESHOLD
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        await _inspection_lights_off()
        return {"success": False, "ball_detected": True, "message": "Inspection stopped"}
    # Save composite image for History (2x2 from extracted balls)
    composite_path: Optional[str] = None
    try:
        composite_path = await asyncio.to_thread(_build_composite_image, top_paths, bot_paths)
    except Exception as e:
        logger.warning("Failed to build composite image: %s", e)

    # Generate per-view processed images (for Inspection Run processed-images panel)
    try:
        await asyncio.to_thread(_generate_processed_images, top_paths, bot_paths)
    except Exception as e:
        logger.warning("Failed to generate processed images: %s", e)

    if is_good:
        await _inspection_act1_extend_retract()
        with INSPECTION_LOCK:
            TOTAL_BALLS += 1
            GOOD_BALLS += 1
            LAST_RESULT = "good"
    else:
        await _inspection_act2_extend_retract()
        with INSPECTION_LOCK:
            TOTAL_BALLS += 1
            BAD_BALLS += 1
            LAST_RESULT = "bad"
    # Persist cycle to SQLite history (for History tab)
    try:
        await asyncio.to_thread(
            _save_inspection_cycle,
            LAST_RESULT,
            composite_path,
        )
    except Exception as e:
        logger.warning("Failed to save inspection cycle: %s", e)
    await _inspection_lights_off()
    return {"success": True, "ball_detected": True, "total_balls": TOTAL_BALLS, "good_balls": GOOD_BALLS, "bad_balls": BAD_BALLS}


async def _run_inspection_cycle_loop() -> None:
    """Background loop: run _perform_one_ball_inspection until stop requested or 2 empty cycles."""
    global INSPECTION_RUNNING, INSPECTION_STOP_REQUESTED, INSPECTION_TASK, CYCLE_COUNT
    empty_cycles = 0
    try:
        while INSPECTION_RUNNING and not INSPECTION_STOP_REQUESTED:
            with INSPECTION_LOCK:
                CYCLE_COUNT += 1
            logger.info("Inspection cycle %s starting", CYCLE_COUNT)
            result = await _perform_one_ball_inspection()
            if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
                break
            if result.get("ball_detected"):
                empty_cycles = 0
            else:
                empty_cycles += 1
                if empty_cycles >= 2:
                    logger.info("No ball for 2 consecutive cycles; stopping inspection")
                    break
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        logger.info("Inspection loop cancelled")
    finally:
        INSPECTION_RUNNING = False
        INSPECTION_STOP_REQUESTED = False
        INSPECTION_TASK = None
        await _inspection_lights_off()
        logger.info("Inspection loop ended; cycles=%s", CYCLE_COUNT)


@app.get("/api/inspection/status")
async def api_inspection_status() -> dict:
    """Return current inspection state for UI (run metrics, state)."""
    with INSPECTION_LOCK:
        processed_urls = {
            key: f"/api/images/view/{fname}" for key, fname in LAST_PROCESSED_IMAGES.items()
        }
        return {
            "success": True,
            "running": INSPECTION_RUNNING,
            "state": "running" if INSPECTION_RUNNING else ("stopping" if INSPECTION_STOP_REQUESTED else "idle"),
            "cycle_count": CYCLE_COUNT,
            "total_balls": TOTAL_BALLS,
            "good_balls": GOOD_BALLS,
            "bad_balls": BAD_BALLS,
            "last_result": LAST_RESULT,
            "processed_images": processed_urls,
        }


@app.get("/api/history")
async def api_history_list(
    limit: int = 20,
    offset: int = 0,
    date_from: str | None = None,
    date_to: str | None = None,
    lot_number: str | None = None,
    mfg_name: str | None = None,
    inspection_result: str | None = None,
) -> dict:
    """
    List inspection history for History tab with basic filters and pagination.
    """
    conn = _get_db_conn()
    cur = conn.cursor()
    where: List[str] = []
    params: List[Any] = []
    if date_from:
        where.append("timestamp >= ?")
        params.append(date_from)
    if date_to:
        where.append("timestamp <= ?")
        params.append(date_to)
    if lot_number:
        where.append("lot_number LIKE ?")
        params.append(f"%{lot_number}%")
    if mfg_name:
        where.append("mfg_name LIKE ?")
        params.append(f"%{mfg_name}%")
    if inspection_result:
        where.append("inspection_result = ?")
        params.append(inspection_result)
    where_sql = " WHERE " + " AND ".join(where) if where else ""

    count_row = cur.execute(
        f"SELECT COUNT(*) as c FROM inspection_history{where_sql}", params
    ).fetchone()
    total = int(count_row["c"] if count_row else 0)

    rows = cur.execute(
        f"""
        SELECT * FROM inspection_history
        {where_sql}
        ORDER BY timestamp DESC
        LIMIT ? OFFSET ?
        """,
        params + [limit, offset],
    ).fetchall()

    data: List[Dict[str, Any]] = []
    for r in rows:
        data.append(
            {
                "id": r["id"],
                "timestamp": r["timestamp"],
                "lot_number": r["lot_number"],
                "mfg_name": r["mfg_name"],
                "mfg_part_number": r["mfg_part_number"],
                "material": r["material"],
                "ball_diameter": r["ball_diameter"],
                "ball_diameter_mm": r["ball_diameter_mm"],
                "customer_name": r["customer_name"],
                "inspection_result": r["inspection_result"],
                "composite_image_path": r["composite_image_path"],
            }
        )

    stats_row = cur.execute(
        "SELECT COUNT(*) as total_cycles, SUM(good_balls) as good_balls, "
        "SUM(bad_balls) as bad_balls, SUM(no_balls) as no_balls FROM inspection_history"
    ).fetchone()
    statistics = {
        "total_cycles": int(stats_row["total_cycles"] or 0),
        "good_balls": int(stats_row["good_balls"] or 0),
        "bad_balls": int(stats_row["bad_balls"] or 0),
        "no_balls": int(stats_row["no_balls"] or 0),
    }
    pagination = {"total": total, "limit": limit, "offset": offset}
    return {"success": True, "data": data, "statistics": statistics, "pagination": pagination}


@app.get("/api/history/{cycle_id}")
async def api_history_get(cycle_id: int) -> dict:
    conn = _get_db_conn()
    cur = conn.cursor()
    row = cur.execute(
        "SELECT * FROM inspection_history WHERE id = ?", (cycle_id,)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Cycle not found")
    record = {
        "id": row["id"],
        "timestamp": row["timestamp"],
        "lot_number": row["lot_number"],
        "mfg_name": row["mfg_name"],
        "mfg_part_number": row["mfg_part_number"],
        "material": row["material"],
        "ball_diameter": row["ball_diameter"],
        "ball_diameter_mm": row["ball_diameter_mm"],
        "customer_name": row["customer_name"],
        "inspection_result": row["inspection_result"],
        "composite_image_path": row["composite_image_path"],
    }
    return {"success": True, "data": record}


@app.delete("/api/history/{cycle_id}")
async def api_history_delete(cycle_id: int) -> dict:
    conn = _get_db_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM inspection_history WHERE id = ?", (cycle_id,))
    conn.commit()
    return {"success": True}


@app.delete("/api/history/bulk")
async def api_history_bulk_delete(ids: List[int] = Body(...)) -> dict:
    if not ids:
        return {"success": True}
    conn = _get_db_conn()
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in ids)
    cur.execute(f"DELETE FROM inspection_history WHERE id IN ({placeholders})", ids)
    conn.commit()
    return {"success": True}


@app.post("/api/history/export")
async def api_history_export(body: Dict[str, Any]) -> dict:
    """
    Export history as CSV. Frontend sends { filters, format }.
    """
    filters = body.get("filters") or {}
    limit = 10000
    offset = 0
    # Reuse list logic with filters; ignore pagination for export and dump up to 10k rows.
    result = await api_history_list(
        limit=limit,
        offset=offset,
        date_from=filters.get("date_from"),
        date_to=filters.get("date_to"),
        lot_number=filters.get("lot_number"),
        mfg_name=filters.get("mfg_name"),
        inspection_result=filters.get("inspection_result"),
    )
    rows = result.get("data", [])
    headers = [
        "id",
        "timestamp",
        "lot_number",
        "mfg_name",
        "mfg_part_number",
        "material",
        "ball_diameter",
        "ball_diameter_mm",
        "customer_name",
        "inspection_result",
    ]
    lines = [",".join(headers)]
    for r in rows:
        line = ",".join(
            [
                str(r.get("id", "")),
                str(r.get("timestamp", "")),
                str(r.get("lot_number", "")),
                str(r.get("mfg_name", "")),
                str(r.get("mfg_part_number", "")),
                str(r.get("material", "")),
                str(r.get("ball_diameter", "")),
                str(r.get("ball_diameter_mm", "")),
                str(r.get("customer_name", "")),
                str(r.get("inspection_result", "")),
            ]
        )
        lines.append(line)
    content = "\n".join(lines)
    filename = f"history_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    return {"success": True, "data": {"content": content, "filename": filename}}


@app.post("/api/inspection/start")
async def api_inspection_start(payload: InspectionStartPayload) -> dict:
    """Start continuous inspection (background loop)."""
    global INSPECTION_RUNNING, INSPECTION_TASK, FLIP_DURATION_SEC
    if INSPECTION_RUNNING:
        raise HTTPException(status_code=400, detail="Inspection already running")
    FLIP_DURATION_SEC = max(0.05, float(payload.flip_duration))
    _reset_inspection_stats()
    INSPECTION_RUNNING = True
    INSPECTION_STOP_REQUESTED = False
    INSPECTION_TASK = asyncio.create_task(_run_inspection_cycle_loop())
    logger.info("Inspection started (flip_duration=%.2fs)", FLIP_DURATION_SEC)
    return {"success": True, "message": "Inspection started"}


@app.post("/api/inspection/stop")
async def api_inspection_stop() -> dict:
    """Request graceful stop (after current cycle)."""
    global INSPECTION_STOP_REQUESTED
    if not INSPECTION_RUNNING:
        raise HTTPException(status_code=400, detail="Inspection not running")
    INSPECTION_STOP_REQUESTED = True
    logger.info("Stop requested; current cycle will complete")
    return {"success": True, "message": "Stop requested - current cycle will complete"}


@app.post("/api/inspection/stop-immediate")
async def api_inspection_stop_immediate() -> dict:
    """Force stop immediately (cancel task, lights off)."""
    global INSPECTION_RUNNING, INSPECTION_TASK
    if not INSPECTION_RUNNING:
        raise HTTPException(status_code=400, detail="Inspection not running")
    INSPECTION_RUNNING = False
    task = INSPECTION_TASK
    INSPECTION_TASK = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await _inspection_lights_off()
    logger.info("Inspection stopped immediately")
    return {"success": True, "message": "Inspection stopped immediately"}


@app.post("/api/inspection/single-inspection")
async def api_inspection_single(payload: InspectionStartPayload) -> dict:
    """Run one inspection cycle (single inspection)."""
    global INSPECTION_RUNNING, FLIP_DURATION_SEC, CYCLE_COUNT
    if INSPECTION_RUNNING:
        raise HTTPException(status_code=400, detail="Inspection already running")
    FLIP_DURATION_SEC = max(0.05, float(payload.flip_duration))
    INSPECTION_RUNNING = True
    try:
        with INSPECTION_LOCK:
            CYCLE_COUNT += 1
        result = await _perform_one_ball_inspection()
        await _inspection_lights_off()
        return result
    finally:
        INSPECTION_RUNNING = False


@app.post("/api/motors/control")
async def api_control_motor(payload: MotorAction) -> dict:
    name = payload.motor.strip().lower()
    if not name.startswith("m") or name[1:] not in ("1", "2", "3", "4"):
        raise HTTPException(status_code=400, detail="motor must be m1, m2, m3, or m4")
    motor_index = int(name[1:])
    if payload.action not in ("extend", "retract", "stop"):
        raise HTTPException(status_code=400, detail="action must be 'extend', 'retract', or 'stop'")
    await asyncio.to_thread(_run_motor_blocking, motor_index, payload.action, payload.duration)
    logger.info(
        "Motor %s (index %s) %s for %.2fs",
        name,
        motor_index,
        payload.action,
        payload.duration,
    )
    return {
        "success": True,
        "motor": name,
        "motor_index": motor_index,
        "action": payload.action,
        "duration": payload.duration,
    }


@app.post("/api/motors/kick")
async def api_kick_motor() -> dict:
    """
    Kick action: run motor1 until the blade sensor on pin 38/gpio400
    (gpiochip0 line 52) reports the blade is in position, or a timeout.
    """
    logger.info("Kick motor requested (motor1 until blade sensor)")
    await asyncio.to_thread(_kick_until_blade)
    logger.info("Kick motor completed (blade sensor or timeout)")
    return {"success": True}


class InspectionMetadata(BaseModel):
    lotNumber: str | None = None
    mfgName: str | None = None
    mfgPart: str | None = None
    material: str | None = None
    ballDiameter: str | None = None
    customerName: str | None = None


@app.post("/api/inspection/metadata")
async def api_set_inspection_metadata(payload: InspectionMetadata) -> dict:
    """
    Store current inspection metadata from frontend (Save Metadata button).
    """
    global CURRENT_METADATA
    CURRENT_METADATA = payload.dict()
    logger.info("Updated inspection metadata: %s", CURRENT_METADATA)
    return {"success": True}


@app.get("/api/inspection/metadata")
async def api_get_inspection_metadata() -> dict:
    """
    Return last stored inspection metadata.
    """
    return {"success": True, "metadata": CURRENT_METADATA}


@app.get("/api/sensors/ball")
async def api_read_ball_sensor() -> dict:
    """
    Read ball-on-stage sensor (physical pin 40, gpiochip0 line 51).
    value==1 -> stage clear (no ball); value!=1 -> ball present.
    """
    return await asyncio.to_thread(_read_ball_sensor)


class _FlipMotorController:
    """
    Lightweight GPIO-based flip motor controller using libgpiod.

    Mirrors the old Pi behavior:
      - Run:  PWM = 1, DIR = 0
      - Stop: PWM = 0 (line low), DIR = 1 (brake)
    """

    def __init__(self) -> None:
        if gpiod is None:
            raise HTTPException(
                status_code=503,
                detail="gpiod Python bindings not available; install python3-libgpiod.",
            )
        # Jetson Orin Nano J12 pin mapping based on JetsonHacks pinout
        self.chip = gpiod.Chip("gpiochip0")
        self._pwm_offset = 43  # pin 33, GPIO13
        self._dir_offset = 41  # pin 32, GPIO07
        self.pwm = self.chip.get_line(self._pwm_offset)
        self.dir = self.chip.get_line(self._dir_offset)
        self.pwm.request(consumer="flip_pwm", type=gpiod.LINE_REQ_DIR_OUT)
        self.dir.request(consumer="flip_dir", type=gpiod.LINE_REQ_DIR_OUT)

    def run_for(self, duration: float) -> None:
        # Forward: DIR low, PWM high (same as flip_motor_pwm.value=1, flip_motor_dir.off())
        self.pwm.set_value(1)
        self.dir.set_value(0)
        time.sleep(duration)
        # Stop/brake: both inputs high (same as flip_motor_pwm.on(), flip_motor_dir.on())
        self.pwm.set_value(1)
        self.dir.set_value(1)


_flip_controller: "_FlipMotorController | None" = None


def _get_flip_controller() -> "_FlipMotorController":
    global _flip_controller
    if _flip_controller is None:
        _flip_controller = _FlipMotorController()
    return _flip_controller


@app.post("/api/motors/flip")
async def api_flip_motor(payload: MotorAction) -> dict:
    """
    Flip motor: Jetson GPIO driver (separate from Motor HAT), using libgpiod.

    The UI duration field is in milliseconds; convert to seconds here.
    """
    duration_ms = float(payload.duration or 250.0)
    duration = max(0.05, duration_ms / 1000.0)
    logger.info("Flip motor requested for %.0f ms (%.2fs)", duration_ms, duration)
    controller = _get_flip_controller()
    await asyncio.to_thread(controller.run_for, duration)
    logger.info("Flip motor completed")
    return {"success": True, "duration": duration}


@app.post("/api/lights/on-all")
async def api_lights_on_all() -> dict:
    """
    Convenience endpoint to turn all four lights fully on (used by TURN ON ALL).
    """
    for offset in LIGHT_LINE_OFFSETS.values():
        _gpioset(offset, 1)
    logger.info("All lights ON (api_lights_on_all)")
    return {"success": True}


@app.get("/api/images/view/{filename}")
async def api_view_image(filename: str) -> FileResponse:
    """
    Serve composite images and processed images (Inspection Run).
    """
    path = COMPOSITE_DIR / filename
    if not path.exists():
        alt = CAPTURE_DIR / filename
        if not alt.exists():
            raise HTTPException(status_code=404, detail="Image not found")
        path = alt
    return FileResponse(path)


# ---------------------- Simple camera capture (Jetson CSI cameras) ----------------------

def _map_camera_name_to_sensor_id(name: str) -> int:
    """
    Map frontend camera names to Jetson CSI sensor IDs.

    On Pi we used 0/1; on Jetson Orin Nano, CSI sensors are typically
    accessed via nvarguscamerasrc with sensor-id=0/1.
    """
    n = name.strip().lower()
    if "b" in n:
        return 1
    return 0


def _build_nvargus_pipeline(sensor_id: int) -> str:
    """
    Build a GStreamer pipeline string for OpenCV to access a CSI camera.

    This uses nvarguscamerasrc, converts to BGR for cv2, and ends in appsink.
    We fix exposure via exposuretimerange using the current CAMERA_CONFIG.
    """
    exp_us = int(CAMERA_CONFIG["exposure_ms"] * 1000.0)
    return (
        f"nvarguscamerasrc sensor-id={sensor_id} exposuretimerange=\"{exp_us} {exp_us}\" ! "
        "video/x-raw(memory:NVMM), width=1920, height=1080, format=NV12, framerate=30/1 ! "
        "nvvidconv flip-method=0 ! video/x-raw, format=BGRx ! "
        "videoconvert ! video/x-raw, format=BGR ! appsink"
    )


@app.post("/api/cameras/capture")
async def api_camera_capture(req: CameraCaptureRequest) -> dict:
    """
    Capture a single frame from the requested camera and save it under static/captures.
    """
    if cv2 is None:
        raise HTTPException(
            status_code=503,
            detail="OpenCV (cv2) not available; install opencv-python on the Jetson.",
        )

    sensor_id = _map_camera_name_to_sensor_id(req.camera_name)
    filename = "camA_latest.jpg" if sensor_id == 0 else "camB_latest.jpg"
    out_path = CAPTURE_DIR / filename

    def _capture() -> bool:
        pipeline = _build_nvargus_pipeline(sensor_id)
        cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
        if not cap.isOpened():
            return False
        ok, frame = cap.read()
        cap.release()
        if not ok or frame is None:
            return False
        return cv2.imwrite(str(out_path), frame)

    logger.info(
        "Camera capture requested: %s -> sensor-id=%s, output=%s",
        req.camera_name,
        sensor_id,
        filename,
    )
    ok = await asyncio.to_thread(_capture)
    if not ok:
        logger.warning("Camera capture FAILED for sensor-id=%s", sensor_id)
        raise HTTPException(status_code=500, detail=f"Failed to capture from CSI camera sensor-id={sensor_id}")

    logger.info("Camera capture SUCCESS for sensor-id=%s (%s)", sensor_id, filename)
    return {
        "success": True,
        "sensor_id": sensor_id,
        "filename": filename,
        "url": f"/static/captures/{filename}",
    }


@app.post("/api/cameras/configure")
async def api_camera_configure(cfg: CameraConfig) -> dict:
    """
    Update camera configuration used for subsequent captures.
    For now we apply exposure_ms via nvarguscamerasrc exposuretimerange.
    """
    CAMERA_CONFIG["exposure_ms"] = float(cfg.exposure_ms)
    CAMERA_CONFIG["red_gain"] = float(cfg.red_gain)
    CAMERA_CONFIG["blue_gain"] = float(cfg.blue_gain)
    CAMERA_CONFIG["analogue_gain"] = float(cfg.analogue_gain)
    logger.info(
        "Camera config updated: exposure=%.1f ms, red_gain=%.2f, blue_gain=%.2f, analogue_gain=%.2f",
        CAMERA_CONFIG["exposure_ms"],
        CAMERA_CONFIG["red_gain"],
        CAMERA_CONFIG["blue_gain"],
        CAMERA_CONFIG["analogue_gain"],
    )
    return {"success": True, "config": CAMERA_CONFIG}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8080,
        reload=True,
    )

