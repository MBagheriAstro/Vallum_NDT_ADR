"""
Application configuration and constants.

Paths are resolved relative to this package so the app works whether
run from project root or from webapp/. All hardware pin mappings live here.

Camera exposure/gains can be overridden by webapp/camera_config.json (written by
scripts/camera_live_stream.py --save-config when you quit the live stream).
"""

import json
from pathlib import Path
from datetime import datetime, timezone

# -----------------------------------------------------------------------------
# Paths
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
CAPTURE_DIR = STATIC_DIR / "captures"
COMPOSITE_DIR = STATIC_DIR / "composites"
DB_PATH = BASE_DIR / "inspection_history.db"

# Ensure dirs exist (called from main on startup)
CAPTURE_DIR.mkdir(exist_ok=True)
COMPOSITE_DIR.mkdir(exist_ok=True)

# -----------------------------------------------------------------------------
# Server
# -----------------------------------------------------------------------------
SERVER_STARTED_AT: str = datetime.now(timezone.utc).isoformat()

# -----------------------------------------------------------------------------
# GPIO (gpiochip0 line offsets) – Jetson Orin Nano J12 header
# -----------------------------------------------------------------------------
# Lights: J12 pins 15, 16, 18, 22 -> offsets 85, 126, 125, 123
LIGHT_LINE_OFFSETS: dict[int, int] = {
    1: 85,   # pin 15
    2: 126,  # pin 16
    3: 125,  # pin 18
    4: 123,  # pin 22
}

# Blade sensor: physical pin 38 -> line offset 52
BLADE_LINE_OFFSET = 52

# Ball-on-stage sensor: physical pin 40 -> line offset 51
BALL_STAGE_LINE_OFFSET = 51

# -----------------------------------------------------------------------------
# Camera (GStreamer / nvarguscamerasrc)
# -----------------------------------------------------------------------------
CAMERA_CONFIG_FILE = BASE_DIR / "camera_config.json"

CAMERA_CONFIG: dict[str, float] = {
    "exposure_ms": 100.0,
    "red_gain": 4.0,
    "blue_gain": 0.5,
    "analogue_gain": 4.0,
}


def _load_camera_config_file() -> None:
    """Override CAMERA_CONFIG from file if present (set by camera_live_stream.py --save-config)."""
    if not CAMERA_CONFIG_FILE.exists():
        return
    try:
        with open(CAMERA_CONFIG_FILE) as f:
            data = json.load(f)
        for key in ("exposure_ms", "red_gain", "blue_gain", "analogue_gain"):
            if key in data and isinstance(data[key], (int, float)):
                CAMERA_CONFIG[key] = float(data[key])
    except Exception:
        pass


_load_camera_config_file()

# -----------------------------------------------------------------------------
# Actuator names (Motor HAT: M2=ACT1, M3=ACT2, M4=ACT3)
# -----------------------------------------------------------------------------
ACTUATOR_NAMES = ("ACT1", "ACT2", "ACT3")

ACTUATOR_TO_MOTOR_INDEX: dict[str, int] = {
    "ACT1": 2,
    "ACT2": 3,
    "ACT3": 4,
}
