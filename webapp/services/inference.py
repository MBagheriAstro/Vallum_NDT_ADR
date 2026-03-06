"""
Single inference worker: TOP then BOT, one set at a time.
Submit TOP returns a Future; submit BOT adds BOT to the same cycle; Future resolves with combined result.
"""

import asyncio
import threading
from typing import Any

from ..core.logging_config import logger

try:
    from ..inference_yolo import run_inference_on_paths as _run_yolo_inference
    from ..inference_yolo import YOLOModelError
except Exception:
    try:
        from inference_yolo import run_inference_on_paths as _run_yolo_inference
        from inference_yolo import YOLOModelError
    except Exception:
        _run_yolo_inference = None  # type: ignore[assignment]
        YOLOModelError = Exception  # type: ignore[assignment, misc]

_inference_lock = threading.Lock()
_inference_cv = threading.Condition(_inference_lock)
_slot: tuple | None = None  # (top_paths, bot_paths, future, loop)
_worker_started = False


def _run_extract_and_infer_sync(image_paths: list) -> dict:
    """Run ball extraction + YOLO on given paths. Returns defect_found, prediction, probability. Raises if model missing."""
    if _run_yolo_inference is None:
        logger.error("YOLO inference module not available (import failed). Install ultralytics and ensure model exists.")
        raise RuntimeError("YOLO inference not available - install ultralytics and ensure model exists")
    return _run_yolo_inference(image_paths, logger)


def _worker_thread() -> None:
    global _slot
    while True:
        with _inference_cv:
            _inference_cv.wait_for(lambda: _slot is not None)
            top_paths, bot_paths, future, loop = _slot
        try:
            top_result = _run_extract_and_infer_sync(top_paths)
        except Exception as e:
            logger.error("TOP inference failed: %s", e)
            try:
                loop.call_soon_threadsafe(future.set_exception, e)
            except Exception:
                pass
            continue
        with _inference_cv:
            _inference_cv.wait_for(lambda: _slot is not None and _slot[1] is not None)
            _, bot_paths, future, loop = _slot
            _slot = None
        try:
            bot_result = _run_extract_and_infer_sync(bot_paths)
        except Exception as e:
            logger.error("BOT inference failed: %s", e)
            try:
                loop.call_soon_threadsafe(future.set_exception, e)
            except Exception:
                pass
            continue
        defect_found = top_result.get("defect_found") or bot_result.get("defect_found")
        prediction = "Defect" if defect_found else "Normal"
        probability = max(top_result.get("probability", 0), bot_result.get("probability", 0))
        result: dict[str, Any] = {"defect_found": defect_found, "prediction": prediction, "probability": probability}
        try:
            loop.call_soon_threadsafe(future.set_result, result)
        except Exception:
            pass


def inference_submit_top(top_paths: list) -> asyncio.Future:
    """Submit TOP paths; returns Future that gets combined result when TOP then BOT are done."""
    global _slot, _worker_started
    loop = asyncio.get_event_loop()
    future = loop.create_future()
    with _inference_cv:
        if _slot is not None:
            logger.warning("Inference slot busy; previous cycle may not have completed")
        _slot = (top_paths, None, future, loop)
        if not _worker_started:
            _worker_started = True
            t = threading.Thread(target=_worker_thread, daemon=True)
            t.start()
        _inference_cv.notify()
    return future


def inference_submit_bot(bot_paths: list) -> None:
    """Submit BOT paths for current cycle; worker processes after TOP."""
    with _inference_cv:
        if _slot is None:
            logger.error("inference_submit_bot called but no slot")
            return
        top_paths, _, future, loop = _slot
        _slot = (top_paths, bot_paths, future, loop)
        _inference_cv.notify()
