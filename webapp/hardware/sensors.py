"""Ball and blade sensors via gpioget."""

from .. import config
from .gpio import gpioget


def read_ball_sensor() -> dict:
    """
    Ball-on-stage sensor. value==1 -> stage clear (no ball); value!=1 -> ball present.
    Returns {"success", "value", "stage_clear"} or {"success": False, "error": "..."}.
    """
    try:
        val = gpioget(config.BALL_STAGE_LINE_OFFSET)
        return {"success": True, "value": val, "stage_clear": val == 1}
    except Exception as e:
        return {"success": False, "error": str(e), "value": 0, "stage_clear": False}


def read_blade_sensor() -> dict:
    """
    Blade sensor (active-low). value==0 -> blade horizontal/detected.
    Returns {"success", "value", "blade_horizontal"} or {"success": False, "error": "..."}.
    """
    try:
        val = gpioget(config.BLADE_LINE_OFFSET)
        return {"success": True, "value": val, "blade_horizontal": val == 0}
    except Exception as e:
        return {"success": False, "error": str(e), "value": 1, "blade_horizontal": False}
