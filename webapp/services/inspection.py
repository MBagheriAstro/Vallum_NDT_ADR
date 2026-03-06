"""
Inspection run state and one-cycle logic.
Orchestrates: retract, lights, stage clear, feed, capture TOP/BOT, flip, inference, actuate, save.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import config
from ..core import get_db_conn, logger
from ..hardware import (
    lights_on_sync,
    lights_off_sync,
    read_ball_sensor,
    get_flip_controller,
    run_motor_blocking,
    kick_until_blade,
    retract_all_actuators_async,
    clear_stage_async,
    run_act1_extend_retract_async,
    run_act2_extend_retract_async,
    actuator_state,
    actuator_state_lock,
    set_actuator_state,
    capture_camera_to_path,
)

from .inference import inference_submit_top, inference_submit_bot

# -----------------------------------------------------------------------------
# State
# -----------------------------------------------------------------------------
INSPECTION_RUNNING = False
INSPECTION_STOP_REQUESTED = False
INSPECTION_TASK: asyncio.Task | None = None
INSPECTION_LOCK = threading.Lock()
CYCLE_COUNT = 0
TOTAL_BALLS = 0
GOOD_BALLS = 0
BAD_BALLS = 0
LAST_RESULT: str = "–"
FLIP_DURATION_SEC = 0.25
current_metadata: Dict[str, Any] = {}
LAST_PROCESSED_IMAGES: Dict[str, str] = {}

cv2: Any = None
try:
    import cv2  # type: ignore[import]
except ImportError:
    pass


def inspection_state() -> dict:
    """Current inspection state for API."""
    with INSPECTION_LOCK:
        processed_urls = {k: f"/api/images/view/{v}" for k, v in LAST_PROCESSED_IMAGES.items()}
        return {
            "running": INSPECTION_RUNNING,
            "state": "running" if INSPECTION_RUNNING else ("stopping" if INSPECTION_STOP_REQUESTED else "idle"),
            "cycle_count": CYCLE_COUNT,
            "total_balls": TOTAL_BALLS,
            "good_balls": GOOD_BALLS,
            "bad_balls": BAD_BALLS,
            "last_result": LAST_RESULT,
            "processed_images": processed_urls,
        }


def set_current_metadata(meta: dict) -> None:
    global current_metadata
    current_metadata = meta


def get_current_metadata() -> dict:
    return current_metadata


def set_flip_duration_sec(sec: float) -> None:
    global FLIP_DURATION_SEC
    FLIP_DURATION_SEC = max(0.05, float(sec))


def get_flip_duration_sec() -> float:
    return FLIP_DURATION_SEC


async def run_single_inspection_async() -> dict:
    """Run one inspection cycle (single inspection); caller must ensure not already running."""
    global INSPECTION_RUNNING, CYCLE_COUNT
    INSPECTION_RUNNING = True
    try:
        with INSPECTION_LOCK:
            CYCLE_COUNT += 1
        result = await perform_one_ball_inspection()
        await inspection_lights_off()
        return result
    finally:
        INSPECTION_RUNNING = False


def reset_inspection_stats() -> None:
    global CYCLE_COUNT, TOTAL_BALLS, GOOD_BALLS, BAD_BALLS, LAST_RESULT
    with INSPECTION_LOCK:
        CYCLE_COUNT = 0
        TOTAL_BALLS = 0
        GOOD_BALLS = 0
        BAD_BALLS = 0
        LAST_RESULT = "–"


async def inspection_lights_on() -> None:
    await asyncio.to_thread(lights_on_sync)


async def inspection_lights_off() -> None:
    await asyncio.to_thread(lights_off_sync)


async def clear_stage_for_inspection_async() -> None:
    await clear_stage_async()


async def check_stage_clearance_async() -> bool:
    r = await asyncio.to_thread(read_ball_sensor)
    if not r.get("success"):
        logger.warning("Ball sensor read failed: %s", r.get("error"))
        return False
    if r.get("stage_clear") or r.get("value") == 1:
        return True
    logger.info("Stage not clear; running clear-stage sequence")
    await clear_stage_for_inspection_async()
    r2 = await asyncio.to_thread(read_ball_sensor)
    return r2.get("success") and (r2.get("stage_clear") or r2.get("value") == 1)


async def feed_ball_to_stage_async() -> bool:
    duration = 2.0
    set_actuator_state("ACT3", "extending")
    await asyncio.to_thread(run_motor_blocking, 4, "extend", duration)
    set_actuator_state("ACT3", "extended")
    try:
        await asyncio.to_thread(kick_until_blade)
    except Exception as e:
        logger.warning("Kick motor error: %s", e)
    await asyncio.sleep(3)
    set_actuator_state("ACT3", "retracting")
    await asyncio.to_thread(run_motor_blocking, 4, "retract", duration)
    set_actuator_state("ACT3", "retracted")
    return True


def _build_composite_image(top_paths: List[str], bot_paths: List[str]) -> Optional[str]:
    if cv2 is None:
        return None
    try:
        from ..ball_extraction import extract_ball
    except Exception:
        try:
            from ball_extraction import extract_ball
        except Exception:
            return None
    all_paths = top_paths + bot_paths
    balls: List[Any] = []
    for p in all_paths:
        img = cv2.imread(p)
        if img is None:
            continue
        ball = extract_ball(img, filename=Path(p).name, logger=None, normalize_size=(512, 512), target_ball_diameter=512)
        if ball is not None:
            balls.append(ball)
    if not balls:
        return None
    while len(balls) < 4:
        balls.append(balls[-1])
    a_top, b_top, a_bot, b_bot = balls[:4]
    row_top = cv2.hconcat([a_top, b_top])
    row_bot = cv2.hconcat([a_bot, b_bot])
    composite = cv2.vconcat([row_top, row_bot])
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"composite_{ts}.png"
    out_path = config.COMPOSITE_DIR / filename
    cv2.imwrite(str(out_path), composite)
    return str(out_path)


_DISPLAY_MODEL = None


def _generate_processed_images(top_paths: List[str], bot_paths: List[str]) -> None:
    global LAST_PROCESSED_IMAGES, _DISPLAY_MODEL
    LAST_PROCESSED_IMAGES = {}
    if cv2 is None:
        return
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.warning("ultralytics not installed; skipping processed images")
        return
    try:
        from ..ball_extraction import extract_ball
    except Exception:
        try:
            from ball_extraction import extract_ball
        except Exception:
            return
    if _DISPLAY_MODEL is None:
        default_path = config.BASE_DIR.parent / "runs" / "detect" / "yolo_defect_detection" / "defect_model_yolo11" / "weights" / "best.pt"
        model_path = __import__("os").environ.get("VALLUM_YOLO_MODEL_PATH", str(default_path))
        if not Path(model_path).exists():
            logger.warning("YOLO model not found at %s", model_path)
            return
        try:
            _DISPLAY_MODEL = YOLO(str(model_path))
        except Exception as e:
            logger.warning("Failed to load YOLO for processed images: %s", e)
            return
    model = _DISPLAY_MODEL
    all_paths = top_paths + bot_paths
    for p in all_paths:
        img = cv2.imread(p)
        if img is None:
            continue
        ball = extract_ball(img, filename=Path(p).name, logger=None, normalize_size=(1024, 1024), target_ball_diameter=1024)
        if ball is None:
            continue
        try:
            results = model.predict(source=ball, imgsz=1024, conf=0.4, verbose=False)
        except Exception as e:
            logger.warning("YOLO prediction error %s: %s", p, e)
            continue
        img_out = ball.copy()
        h, w = img_out.shape[:2]
        for res in results:
            boxes = getattr(res, "boxes", None)
            if boxes is None:
                continue
            for box in boxes:
                try:
                    x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                    conf = float(box.conf[0].cpu().numpy())
                    class_id = int(box.cls[0].cpu().numpy())
                    raw_name = getattr(model, "names", {}).get(class_id, str(class_id))
                    class_name = str(raw_name).title().replace("_", "-")
                except Exception:
                    continue
                x1 = max(0, min(w - 1, int(round(x1))))
                y1 = max(0, min(h - 1, int(round(y1))))
                x2 = max(0, min(w, int(round(x2))))
                y2 = max(0, min(h, int(round(y2))))
                if x2 <= x1 or y2 <= y1:
                    continue
                cv2.rectangle(img_out, (x1, y1), (x2, y2), (0, 0, 255), 3)
                label = f"{class_name} {conf:.2f}"
                (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.6, 2)
                cv2.rectangle(img_out, (x1, y1 - th - 8), (x1 + tw + 4, y1), (0, 0, 255), -1)
                cv2.putText(img_out, label, (x1 + 2, y1 - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
        processed_path = Path(p).with_name(Path(p).stem + "_processed.png")
        try:
            cv2.imwrite(str(processed_path), img_out)
        except Exception as e:
            logger.warning("Failed to write processed image %s: %s", processed_path, e)
            continue
        stem = processed_path.stem.upper()
        key = None
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
    conn = get_db_conn()
    cur = conn.cursor()
    meta = current_metadata or {}
    ts = datetime.utcnow().isoformat()
    inspection_result = "GOOD" if last_result == "good" else "BAD" if last_result == "bad" else "NO_BALL"
    with INSPECTION_LOCK:
        total, good, bad = TOTAL_BALLS, GOOD_BALLS, BAD_BALLS
    no_balls = max(0, total - good - bad)
    cur.execute(
        """
        INSERT INTO inspection_history (
            timestamp, lot_number, mfg_name, mfg_part_number, material,
            ball_diameter, ball_diameter_mm, customer_name, inspection_result,
            total_balls, good_balls, bad_balls, no_balls, composite_image_path
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            ts,
            meta.get("lotNumber"),
            meta.get("mfgName"),
            meta.get("mfgPart"),
            meta.get("material"),
            meta.get("ballDiameter"),
            None,
            meta.get("customerName"),
            inspection_result,
            total,
            good,
            bad,
            no_balls,
            composite_path,
        ),
    )
    conn.commit()


async def perform_one_ball_inspection() -> dict:
    global INSPECTION_RUNNING, INSPECTION_STOP_REQUESTED, CYCLE_COUNT, TOTAL_BALLS, GOOD_BALLS, BAD_BALLS, LAST_RESULT
    if not INSPECTION_RUNNING:
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    await retract_all_actuators_async()
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    await inspection_lights_on()
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    clear = await check_stage_clearance_async()
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    if not clear:
        logger.warning("Stage still not clear after clear-stage sequence")
    await feed_ball_to_stage_async()
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    ball_sensor = await asyncio.to_thread(read_ball_sensor)
    if ball_sensor.get("stage_clear") or ball_sensor.get("value") == 1:
        with INSPECTION_LOCK:
            LAST_RESULT = "no_ball"
        await inspection_lights_off()
        return {"success": True, "ball_detected": False, "total_balls": TOTAL_BALLS, "good_balls": GOOD_BALLS, "bad_balls": BAD_BALLS}
    if cv2 is None or config.CAPTURE_DIR is None:
        with INSPECTION_LOCK:
            LAST_RESULT = "no_ball"
        await inspection_lights_off()
        return {"success": True, "ball_detected": False, "total_balls": TOTAL_BALLS, "good_balls": GOOD_BALLS, "bad_balls": BAD_BALLS}
    ts = time.strftime("%Y%m%d_%H%M%S", time.localtime())
    top_paths = [str(config.CAPTURE_DIR / f"CAMERA_A_TOP_{ts}.png"), str(config.CAPTURE_DIR / f"CAMERA_B_TOP_{ts}.png")]
    for cam_name, path in [("camera A", top_paths[0]), ("camera B", top_paths[1])]:
        if not INSPECTION_RUNNING:
            await inspection_lights_off()
            return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
        ok = await asyncio.to_thread(capture_camera_to_path, cam_name, Path(path))
        if not ok:
            logger.warning("TOP capture failed for %s", path)
    inference_future = inference_submit_top(top_paths)
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        await inspection_lights_off()
        return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
    controller = get_flip_controller()
    await asyncio.to_thread(controller.run_for, FLIP_DURATION_SEC)
    bot_paths = [str(config.CAPTURE_DIR / f"CAMERA_A_BOT_{ts}.png"), str(config.CAPTURE_DIR / f"CAMERA_B_BOT_{ts}.png")]
    for cam_name, path in [("camera A", bot_paths[0]), ("camera B", bot_paths[1])]:
        if not INSPECTION_RUNNING:
            await inspection_lights_off()
            return {"success": False, "ball_detected": False, "message": "Inspection stopped"}
        ok = await asyncio.to_thread(capture_camera_to_path, cam_name, Path(path))
        if not ok:
            logger.warning("BOT capture failed for %s", path)
    inference_submit_bot(bot_paths)
    result = await inference_future
    defect_found = result.get("defect_found")
    prediction = result.get("prediction", "Defect" if defect_found else "Normal")
    probability = result.get("probability", 0.0)
    CONFIDENCE_THRESHOLD = 0.75
    is_good = prediction == "Normal" and probability >= CONFIDENCE_THRESHOLD
    if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
        await inspection_lights_off()
        return {"success": False, "ball_detected": True, "message": "Inspection stopped"}
    composite_path = None
    try:
        composite_path = await asyncio.to_thread(_build_composite_image, top_paths, bot_paths)
    except Exception as e:
        logger.warning("Failed to build composite: %s", e)
    try:
        await asyncio.to_thread(_generate_processed_images, top_paths, bot_paths)
    except Exception as e:
        logger.warning("Failed to generate processed images: %s", e)
    if is_good:
        await run_act1_extend_retract_async()
        with INSPECTION_LOCK:
            TOTAL_BALLS += 1
            GOOD_BALLS += 1
            LAST_RESULT = "good"
    else:
        await run_act2_extend_retract_async()
        with INSPECTION_LOCK:
            TOTAL_BALLS += 1
            BAD_BALLS += 1
            LAST_RESULT = "bad"
    try:
        await asyncio.to_thread(_save_inspection_cycle, LAST_RESULT, composite_path)
    except Exception as e:
        logger.warning("Failed to save inspection cycle: %s", e)
    await inspection_lights_off()
    return {"success": True, "ball_detected": True, "total_balls": TOTAL_BALLS, "good_balls": GOOD_BALLS, "bad_balls": BAD_BALLS}


async def run_inspection_cycle_loop() -> None:
    global INSPECTION_RUNNING, INSPECTION_STOP_REQUESTED, INSPECTION_TASK, CYCLE_COUNT
    empty_cycles = 0
    try:
        while INSPECTION_RUNNING and not INSPECTION_STOP_REQUESTED:
            with INSPECTION_LOCK:
                CYCLE_COUNT += 1
            logger.info("Inspection cycle %s starting", CYCLE_COUNT)
            result = await perform_one_ball_inspection()
            if not INSPECTION_RUNNING or INSPECTION_STOP_REQUESTED:
                break
            if result.get("ball_detected"):
                empty_cycles = 0
            else:
                empty_cycles += 1
                if empty_cycles >= 2:
                    logger.info("No ball for 2 consecutive cycles; stopping")
                    break
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        logger.info("Inspection loop cancelled")
    except Exception as e:
        logger.error("Inspection cycle failed (e.g. YOLO model missing): %s", e)
    finally:
        global INSPECTION_TASK
        INSPECTION_RUNNING = False
        INSPECTION_STOP_REQUESTED = False
        INSPECTION_TASK = None
        await inspection_lights_off()
        logger.info("Inspection loop ended; cycles=%s", CYCLE_COUNT)


def start_inspection_loop() -> None:
    global INSPECTION_RUNNING, INSPECTION_STOP_REQUESTED, INSPECTION_TASK
    INSPECTION_RUNNING = True
    INSPECTION_STOP_REQUESTED = False
    INSPECTION_TASK = asyncio.create_task(run_inspection_cycle_loop())


def stop_inspection_requested() -> None:
    global INSPECTION_STOP_REQUESTED
    INSPECTION_STOP_REQUESTED = True


async def stop_inspection_immediate() -> None:
    global INSPECTION_RUNNING, INSPECTION_TASK
    INSPECTION_RUNNING = False
    task = INSPECTION_TASK
    INSPECTION_TASK = None
    if task and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    await inspection_lights_off()
