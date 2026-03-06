"""Cameras API: capture, configure."""

import asyncio
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import config
from ..models import CameraCaptureRequest, CameraConfig
from ..hardware import map_camera_name_to_sensor_id, capture_camera_to_path
from ..hardware.camera import cv2
from ..core import logger

router = APIRouter(prefix="/api", tags=["cameras"])


@router.post("/cameras/capture")
async def camera_capture(req: CameraCaptureRequest) -> dict:
    if cv2 is None:
        raise HTTPException(status_code=503, detail="OpenCV not available.")
    sensor_id = map_camera_name_to_sensor_id(req.camera_name)
    filename = "camA_latest.jpg" if sensor_id == 0 else "camB_latest.jpg"
    out_path = config.CAPTURE_DIR / filename
    logger.info("Camera capture: %s -> sensor-id=%s", req.camera_name, sensor_id)
    ok = await asyncio.to_thread(capture_camera_to_path, req.camera_name, out_path)
    if not ok:
        raise HTTPException(
            status_code=503,
            detail="No cameras available. Connect CSI cameras and ensure nvarguscamerasrc can see them (e.g. on Jetson with camera connected).",
        )
    return {"success": True, "sensor_id": sensor_id, "filename": filename, "url": f"/api/images/view/{filename}"}


@router.post("/cameras/configure")
async def camera_configure(cfg: CameraConfig) -> dict:
    config.CAMERA_CONFIG["exposure_ms"] = float(cfg.exposure_ms)
    config.CAMERA_CONFIG["red_gain"] = float(cfg.red_gain)
    config.CAMERA_CONFIG["blue_gain"] = float(cfg.blue_gain)
    config.CAMERA_CONFIG["analogue_gain"] = float(cfg.analogue_gain)
    logger.info("Camera config updated: exposure=%.1f ms", config.CAMERA_CONFIG["exposure_ms"])
    return {"success": True, "config": config.CAMERA_CONFIG}
