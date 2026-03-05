from pathlib import Path
import subprocess
import threading
import asyncio
import time
import logging
from collections import deque

from fastapi import FastAPI, HTTPException
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

# Blade sensor on Jetson Orin Nano:
# User wiring: physical pin 38 -> gpio400 in sysfs, which corresponds to
# gpiochip0 line offset 52 in the JetsonHacks pinout.
BLADE_LINE_OFFSET = 52


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

