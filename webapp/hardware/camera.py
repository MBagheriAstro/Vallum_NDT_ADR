"""CSI camera capture via OpenCV + GStreamer (nvarguscamerasrc)."""

from pathlib import Path
from typing import Optional

from .. import config

cv2: Optional[any] = None
try:
    import cv2  # type: ignore[import]
except ImportError:
    pass


def map_camera_name_to_sensor_id(name: str) -> int:
    """'camera A' -> 0, 'camera B' -> 1."""
    n = name.strip().lower()
    return 1 if "b" in n else 0


def build_nvargus_pipeline(sensor_id: int) -> str:
    exp_us = int(config.CAMERA_CONFIG["exposure_ms"] * 1000.0)
    return (
        f'nvarguscamerasrc sensor-id={sensor_id} exposuretimerange="{exp_us} {exp_us}" ! '
        "video/x-raw(memory:NVMM), width=1920, height=1080, format=NV12, framerate=30/1 ! "
        "nvvidconv flip-method=0 ! video/x-raw, format=BGRx ! "
        "videoconvert ! video/x-raw, format=BGR ! appsink"
    )


def capture_camera_to_path(cam_name: str, out_path: Path) -> bool:
    """Blocking capture of one camera to out_path. Returns True on success."""
    if cv2 is None:
        return False
    sensor_id = map_camera_name_to_sensor_id(cam_name)
    pipeline = build_nvargus_pipeline(sensor_id)
    cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    if not cap.isOpened():
        return False
    ok, frame = cap.read()
    cap.release()
    if not ok or frame is None:
        return False
    return cv2.imwrite(str(out_path), frame)
