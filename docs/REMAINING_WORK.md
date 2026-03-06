# Implementation status (inspection run)

This doc reflects the **current state** of the codebase. Implementation lives under `webapp/` (see [ARCHITECTURE.md](ARCHITECTURE.md)).

---

## Implemented

| Area | Where | Notes |
|------|--------|------|
| **Ball sensor** | `config.py` (BALL_STAGE_LINE_OFFSET), `hardware/sensors.py` (read_ball_sensor) | value==1 → stage clear |
| **Blade sensor** | `config.py`, `hardware/sensors.py` | Used for kick motor |
| **Stage clear / feed** | `services/inspection.py` (check_stage_clearance_async, feed_ball_to_stage_async, clear_stage_for_inspection_async) | ACT1/ACT2 sweep; ACT3 + kick |
| **Capture TOP → flip → BOT** | `services/inspection.py` (perform_one_ball_inspection), `hardware/camera.py` | 4 images; flip via `hardware/flip.py` |
| **Single inference worker** | `services/inference.py` | TOP then BOT in one thread; BOT waits if TOP running; combines result |
| **Ball extraction + YOLO** | `services/inference.py`, `inference_yolo.py`, `ball_extraction.py` | Model: **`webapp/models/best.pt`** (copy from backend); or `VALLUM_YOLO_MODEL_PATH`. Requires: `numpy`, `opencv-python-headless`, `ultralytics` (see `requirements.txt`). If model missing or deps not installed, inference **fails** (error logged, inspection stops). |
| **Process result** | `services/inspection.py` | Good → ACT1; bad → ACT2; stats (TOTAL_BALLS, GOOD_BALLS, BAD_BALLS) |
| **SQLite + save cycle** | `core/database.py` (init_db, get_db_conn), `services/inspection.py` (_save_inspection_cycle) | Table `inspection_history`; save after each cycle |
| **Metadata** | `api/inspection.py` (POST/GET /api/inspection/metadata), `services/inspection.py` (set_current_metadata, get_current_metadata) | Stored in memory; used when saving cycle |
| **History API** | `api/history.py` | GET list (filters, pagination), GET by id, DELETE, bulk delete, POST export (CSV) |
| **Composite image** | `services/inspection.py` (_build_composite_image) | Built per cycle; path stored in DB |
| **Image view** | `api/images.py` | GET /api/images/view/{filename} from composites or captures |
| **Run controls** | `api/inspection.py` | Start run, single inspection, stop, force stop; status via GET /api/inspection/status |

---

## Optional / future

- **Processed images in UI:** Inspection status already returns `processed_urls` for the latest cycle’s images; the frontend 2×2 grid can use these. Any further UI tweaks (e.g. stable URLs per cycle) are optional.

---

## Summary

The inspection pipeline is implemented end-to-end: sensors, feed, capture, **required** YOLO inference (model from `VALLUM-NDT-ADR-BACKEND/runs/detect/.../weights/best.pt` or `VALLUM_YOLO_MODEL_PATH`; fails with error if missing), actuation, DB save, history API (list, get, delete, bulk delete, export CSV), composite image, and image serving. Keep this file updated when you add or change features.
