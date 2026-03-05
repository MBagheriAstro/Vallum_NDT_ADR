import os
from pathlib import Path
from typing import List, Dict, Any

import cv2  # type: ignore[import]

try:
    from ultralytics import YOLO  # type: ignore[import]
    import torch  # type: ignore[import]
    import gc
except ImportError:  # pragma: no cover - runtime dependency
    YOLO = None  # type: ignore[assignment]
    torch = None  # type: ignore[assignment]
    gc = None  # type: ignore[assignment]

from .ball_extraction import extract_ball


_YOLO_MODEL = None
_YOLO_IMGSZ = 1024
_YOLO_CONF = 0.4  # detection threshold (same ballpark as backend)
_EXPECTED_BALL_DIAMETER_PX = 1278  # default from HQ settings (11/16\" estimate)


def _get_model(logger=None):
    """Lazy-load YOLO model once."""
    global _YOLO_MODEL
    if _YOLO_MODEL is not None:
        return _YOLO_MODEL
    if YOLO is None:
        if logger:
            logger.error("ultralytics.YOLO not available - install 'ultralytics' to enable inference")
        return None
    # Default path matches HQ backend; can override via env.
    default_path = (
        Path(__file__)
        .resolve()
        .parent.parent
        / "runs"
        / "detect"
        / "yolo_defect_detection"
        / "defect_model_yolo11"
        / "weights"
        / "best.pt"
    )
    model_path = os.getenv("VALLUM_YOLO_MODEL_PATH", str(default_path))
    if logger:
        logger.info(f"Loading YOLO model from {model_path}")
    if not os.path.exists(model_path):
        if logger:
            logger.error(f"YOLO model file not found at {model_path}")
        _YOLO_MODEL = None
        return None
    _YOLO_MODEL = YOLO(model_path)
    if logger:
        try:
            names = list(_YOLO_MODEL.names.values())
            logger.info(f"YOLO classes: {', '.join(names)}")
        except Exception:
            logger.info("YOLO model loaded")
    return _YOLO_MODEL


def _run_yolo_on_ball_image(model, ball_img, logger=None) -> Dict[str, Any]:
    """
    Run YOLO on one 1024x1024 ball image (numpy array, BGR).
    Returns dict with defect_found (bool) and probability (float).
    """
    if model is None:
        return {"defect_found": False, "probability": 0.0}
    try:
        results = model.predict(source=ball_img, imgsz=_YOLO_IMGSZ, conf=_YOLO_CONF, verbose=False)
    except Exception as e:  # includes CUDA OOM etc.
        if logger:
            logger.error(f"YOLO prediction error: {e}")
        if torch is not None and gc is not None and torch.cuda.is_available():
            try:
                torch.cuda.empty_cache()
                gc.collect()
            except Exception:
                pass
        # Treat as defect to be conservative.
        return {"defect_found": True, "probability": 0.0}

    detections: List[float] = []
    for res in results:
        boxes = getattr(res, "boxes", None)
        if boxes is None:
            continue
        for box in boxes:
            try:
                conf = float(box.conf[0].cpu().numpy())
            except Exception:
                continue
            detections.append(conf)

    if not detections:
        # No detections = Normal for this image
        if logger:
            logger.info("YOLO: no detections -> Normal for this view")
        return {"defect_found": False, "probability": 1.0}

    best_conf = max(detections)
    if logger:
        logger.info(f"YOLO: defect detected with max confidence {best_conf:.4f}")
    return {"defect_found": True, "probability": best_conf}


def run_inference_on_paths(image_paths: List[str], logger=None) -> Dict[str, Any]:
    """
    High-level helper used by webapp.main._run_extract_and_infer_sync.

    - For each input image path:
      1. Load raw image.
      2. Extract ball with HQ ball_extraction (rotates based on CAMERA_A/B, LED-based center, radius).
      3. Normalize to 1024x1024 canvas.
      4. Run YOLO on the ball image.
    - Aggregate across all images:
      - If all Normal (no detections), overall prediction = Normal, probability=1.0.
      - If any defect, overall prediction = Defect, probability = max confidence over all images.
    """
    model = _get_model(logger)
    if model is None:
        # Fallback: stub Normal result so the rest of the pipeline still works.
        if logger:
            logger.warning("YOLO model not available - returning stub Normal result")
        return {"defect_found": False, "prediction": "Normal", "probability": 1.0}

    defect_found_any = False
    best_prob = 0.0

    for path in image_paths:
        img = cv2.imread(path)
        if img is None:
            if logger:
                logger.error(f"Failed to read image for inference: {path}")
            # Treat unreadable image as defect.
            defect_found_any = True
            continue

        ball_img = extract_ball(
            img,
            filename=os.path.basename(path),
            logger=logger,
            normalize_size=(1024, 1024),
            target_ball_diameter=1024,
            expected_ball_diameter_px=_EXPECTED_BALL_DIAMETER_PX,
        )
        if ball_img is None:
            if logger:
                logger.error(f"Ball extraction failed for {path}")
            defect_found_any = True
            continue

        yolo_result = _run_yolo_on_ball_image(model, ball_img, logger=logger)
        if yolo_result.get("defect_found"):
            defect_found_any = True
            best_prob = max(best_prob, float(yolo_result.get("probability", 0.0)))

    if not defect_found_any:
        return {"defect_found": False, "prediction": "Normal", "probability": 1.0}

    return {"defect_found": True, "prediction": "Defect", "probability": best_prob}

