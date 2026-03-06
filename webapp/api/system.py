"""System API: status, restart app, power."""

import os
import time
import threading
from fastapi import APIRouter, HTTPException

from .. import config
from ..models import PowerAction
from ..core import read_cpu_percent, read_ram_percent, read_disk_percent, logger
from ..hardware import read_ball_sensor, read_blade_sensor

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/system/status")
async def system_status() -> dict:
    cpu = read_cpu_percent()
    ram = read_ram_percent()
    disk = read_disk_percent()
    ball = read_ball_sensor()
    blade = read_blade_sensor()
    return {
        "success": True,
        "cpu_percent": cpu,
        "ram_percent": ram,
        "disk_percent": disk,
        "gpu_percent": None,
        "server_ok": True,
        "server_started_at": config.SERVER_STARTED_AT,
        "ball_on_stage": not (ball.get("stage_clear") or ball.get("value") == 1),
        "blade_horizontal": bool(blade.get("blade_horizontal")),
    }


@router.post("/system/restart-app")
async def restart_app() -> dict:
    def _restart() -> None:
        time.sleep(0.5)
        os._exit(0)
    threading.Thread(target=_restart, daemon=True).start()
    return {"success": True}


@router.post("/system/power")
async def power(payload: PowerAction) -> dict:
    import subprocess
    try:
        if payload.action == "shutdown":
            subprocess.Popen(["systemctl", "poweroff"])
        elif payload.action == "reboot":
            subprocess.Popen(["systemctl", "reboot"])
        else:
            raise HTTPException(status_code=400, detail="action must be 'shutdown' or 'reboot'")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    return {"success": True}
