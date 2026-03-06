#!/usr/bin/env python3
"""
Dual-camera live stream for Jetson (CSI via nvarguscamerasrc).
Use for focus adjustment and tuning exposure/gains. Same pipeline as the webapp.

Run from repo root (default = both cameras side-by-side):

  python3 scripts/camera_live_stream.py
  python3 scripts/camera_live_stream.py --exposure 100 --red-gain 2.0 --blue-gain 2.0
  python3 scripts/camera_live_stream.py --exposure 150 --red-gain-a 2.0 --blue-gain-a 2.0 --red-gain-b 2.2 --blue-gain-b 1.8

Single camera:
  python3 scripts/camera_live_stream.py --single --camera 0
  python3 scripts/camera_live_stream.py --single --camera 1 --exposure 100

Settings (exposure, red/blue gain) are applied for this run. Use --save-config to write
them to webapp/camera_config.json so the webapp uses them next time.
Controls: q = quit, s = save frame(s)
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import cv2
import numpy as np

# Defaults (match old frontend script)
DEFAULT_EXPOSURE_MS = 100.0
DEFAULT_RED_GAIN = 2.0
DEFAULT_BLUE_GAIN = 2.0

try:
    from webapp import config as app_config
    _exp = app_config.CAMERA_CONFIG.get("exposure_ms")
    _r = app_config.CAMERA_CONFIG.get("red_gain")
    _b = app_config.CAMERA_CONFIG.get("blue_gain")
    if _exp is not None:
        DEFAULT_EXPOSURE_MS = float(_exp)
    if _r is not None:
        DEFAULT_RED_GAIN = float(_r)
    if _b is not None:
        DEFAULT_BLUE_GAIN = float(_b)
except Exception:
    pass

LIGHT_OFFSETS = [85, 126, 125, 123]
CONFIG_FILE = REPO_ROOT / "webapp" / "camera_config.json"


def build_pipeline(sensor_id: int, exposure_ms: float) -> str:
    """Same GStreamer pipeline as webapp/hardware/camera.py."""
    exp_us = int(exposure_ms * 1000.0)
    return (
        f'nvarguscamerasrc sensor-id={sensor_id} exposuretimerange="{exp_us} {exp_us}" ! '
        "video/x-raw(memory:NVMM), width=1920, height=1080, format=NV12, framerate=30/1 ! "
        "nvvidconv flip-method=0 ! video/x-raw, format=BGRx ! "
        "videoconvert ! video/x-raw, format=BGR ! appsink"
    )


def lights_on():
    for offset in LIGHT_OFFSETS:
        try:
            subprocess.run(["gpioset", "gpiochip0", str(offset), "=1"], capture_output=True, timeout=2)
        except Exception:
            pass


def lights_off():
    for offset in LIGHT_OFFSETS:
        try:
            subprocess.run(["gpioset", "gpiochip0", str(offset), "=0"], capture_output=True, timeout=2)
        except Exception:
            pass


def save_camera_config(exposure_ms: float, red_gain: float, blue_gain: float) -> None:
    """Write settings to webapp/camera_config.json for next webapp/script run."""
    data = {
        "exposure_ms": exposure_ms,
        "red_gain": red_gain,
        "blue_gain": blue_gain,
        "analogue_gain": 4.0,
    }
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(data, f, indent=2)
    print("Config saved to", CONFIG_FILE)


def add_focus_guides(frame: np.ndarray, label: str = "") -> np.ndarray:
    h, w = frame.shape[:2]
    cx, cy = w // 2, h // 2
    size = 50
    cv2.line(frame, (cx - size, cy), (cx + size, cy), (0, 255, 0), 2)
    cv2.line(frame, (cx, cy - size), (cx, cy + size), (0, 255, 0), 2)
    rect_size = 200
    x1, y1 = cx - rect_size // 2, cy - rect_size // 2
    x2, y2 = cx + rect_size // 2, cy + rect_size // 2
    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
    cv2.putText(frame, "VALLUM Jetson - Live stream (focus)", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
    if label:
        cv2.putText(frame, label, (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)
    cv2.putText(frame, "q=quit  s=save", (10, h - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return frame


def run_single(sensor_id: int, exposure_ms: float, lights: bool, save_dir: Path) -> int:
    pipeline = build_pipeline(sensor_id, exposure_ms)
    cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
    if not cap.isOpened():
        print(f"ERROR: Could not open camera (sensor-id={sensor_id}). Check CSI and nvarguscamerasrc.")
        return 1
    if lights:
        lights_on()
        print("Lights on.")
    window = "VALLUM Jetson - Camera live stream (focus)"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, 1920, 1080)
    print("Controls: q = quit, s = save frame")
    try:
        while True:
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            frame = add_focus_guides(frame.copy(), f"Camera {'B' if sensor_id else 'A'} (sensor {sensor_id})")
            cv2.imshow(window, frame)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                path = save_dir / f"focus_cam{sensor_id}_{time.strftime('%Y%m%d_%H%M%S')}.jpg"
                cv2.imwrite(str(path), frame)
                print(f"Saved: {path}")
    finally:
        cap.release()
        cv2.destroyAllWindows()
        if lights:
            lights_off()
            print("Lights off.")
    return 0


def run_dual(exposure_ms: float, lights: bool, save_dir: Path) -> int:
    pipeline_a = build_pipeline(0, exposure_ms)
    pipeline_b = build_pipeline(1, exposure_ms)
    cap_a = cv2.VideoCapture(pipeline_a, cv2.CAP_GSTREAMER)
    cap_b = cv2.VideoCapture(pipeline_b, cv2.CAP_GSTREAMER)
    if not cap_a.isOpened():
        print("ERROR: Could not open camera A (sensor 0).")
        return 1
    if not cap_b.isOpened():
        print("ERROR: Could not open camera B (sensor 1).")
        cap_a.release()
        return 1
    if lights:
        lights_on()
        print("Lights on.")
    window = "VALLUM Jetson - Dual camera live stream (focus)"
    cv2.namedWindow(window, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(window, 1920, 1080)
    frame_a = frame_b = None
    lock_a, lock_b = threading.Lock(), threading.Lock()

    def capture_loop(cap, lock, name):
        nonlocal frame_a, frame_b
        while True:
            ok, f = cap.read()
            if ok and f is not None:
                with lock:
                    if name == "a":
                        frame_a = f
                    else:
                        frame_b = f
            time.sleep(0.05)

    run = [True]

    def reader_a():
        while run[0]:
            capture_loop(cap_a, lock_a, "a")

    def reader_b():
        while run[0]:
            capture_loop(cap_b, lock_b, "b")

    t_a = threading.Thread(target=reader_a, daemon=True)
    t_b = threading.Thread(target=reader_b, daemon=True)
    t_a.start()
    t_b.start()
    time.sleep(0.5)
    print("Controls: q = quit, s = save both frames")
    try:
        while True:
            with lock_a:
                fa = None if frame_a is None else frame_a.copy()
            with lock_b:
                fb = None if frame_b is None else frame_b.copy()
            if fa is not None:
                fa = add_focus_guides(fa, "Camera A")
            if fb is not None:
                fb = add_focus_guides(fb, "Camera B")
            if fa is not None and fb is not None:
                ha, wa = fa.shape[0], fa.shape[1]
                hb, wb = fb.shape[0], fb.shape[1]
                th = min(ha, hb)
                if ha != th:
                    fa = cv2.resize(fa, (int(wa * th / ha), th))
                if hb != th:
                    fb = cv2.resize(fb, (int(wb * th / hb), th))
                combined = np.hstack((fa, fb))
                cv2.imshow(window, combined)
            key = cv2.waitKey(1) & 0xFF
            if key == ord("q"):
                break
            if key == ord("s"):
                ts = time.strftime("%Y%m%d_%H%M%S")
                with lock_a:
                    if frame_a is not None:
                        p = save_dir / f"focus_cam0_{ts}.jpg"
                        cv2.imwrite(str(p), frame_a)
                        print(f"Saved: {p}")
                with lock_b:
                    if frame_b is not None:
                        p = save_dir / f"focus_cam1_{ts}.jpg"
                        cv2.imwrite(str(p), frame_b)
                        print(f"Saved: {p}")
    finally:
        run[0] = False
        cap_a.release()
        cap_b.release()
        cv2.destroyAllWindows()
        if lights:
            lights_off()
            print("Lights off.")
    return 0


def main():
    parser = argparse.ArgumentParser(
        description="VALLUM Jetson dual-camera live stream. Set exposure and gains when you run the script."
    )
    parser.add_argument("--single", action="store_true", help="Single camera only (use with --camera)")
    parser.add_argument("--camera", type=int, choices=[0, 1], default=0, help="Which camera when --single (0=A, 1=B)")
    parser.add_argument("--exposure", type=float, default=None, metavar="MS",
                        help=f"Exposure time in ms (default: {DEFAULT_EXPOSURE_MS})")
    parser.add_argument("--red-gain", type=float, default=None, help=f"Red gain, both cams (default: {DEFAULT_RED_GAIN})")
    parser.add_argument("--blue-gain", type=float, default=None, help=f"Blue gain, both cams (default: {DEFAULT_BLUE_GAIN})")
    parser.add_argument("--red-gain-a", type=float, default=None, help="Red gain camera A (dual mode)")
    parser.add_argument("--blue-gain-a", type=float, default=None, help="Blue gain camera A (dual mode)")
    parser.add_argument("--red-gain-b", type=float, default=None, help="Red gain camera B (dual mode)")
    parser.add_argument("--blue-gain-b", type=float, default=None, help="Blue gain camera B (dual mode)")
    parser.add_argument("--no-lights", action="store_true", help="Do not turn on lights")
    parser.add_argument("--save-dir", type=str, default=None, help="Where to save images (default: webapp/static/captures)")
    parser.add_argument("--save-config", action="store_true",
                        help="On exit, write exposure and gains to webapp/camera_config.json for the webapp")
    args = parser.parse_args()

    exposure_ms = args.exposure if args.exposure is not None else DEFAULT_EXPOSURE_MS
    red = args.red_gain if args.red_gain is not None else DEFAULT_RED_GAIN
    blue = args.blue_gain if args.blue_gain is not None else DEFAULT_BLUE_GAIN
    # Per-camera overrides (for dual; pipeline currently only uses exposure)
    red_a = args.red_gain_a if args.red_gain_a is not None else red
    blue_a = args.blue_gain_a if args.blue_gain_a is not None else blue
    red_b = args.red_gain_b if args.red_gain_b is not None else red
    blue_b = args.blue_gain_b if args.blue_gain_b is not None else blue

    lights = not args.no_lights
    save_dir = Path(args.save_dir) if args.save_dir else REPO_ROOT / "webapp" / "static" / "captures"
    save_dir.mkdir(parents=True, exist_ok=True)

    if "DISPLAY" not in os.environ:
        print("No DISPLAY set. Run with a display (e.g. on the Jetson, not headless SSH).")
        return 1

    print("VALLUM Jetson – dual camera live stream (focus / settings)")
    print(f"Exposure: {exposure_ms} ms  |  Red gain A/B: {red_a}/{red_b}  |  Blue gain A/B: {blue_a}/{blue_b}")
    print(f"Lights: {'on' if lights else 'off'}  |  Save dir: {save_dir}")
    print()

    try:
        if args.single:
            return run_single(args.camera, exposure_ms, lights, save_dir)
        return run_dual(exposure_ms, lights, save_dir)
    finally:
        if args.save_config:
            save_camera_config(exposure_ms, red, blue)
            print("Config saved to", CONFIG_FILE)


if __name__ == "__main__":
    sys.exit(main())
