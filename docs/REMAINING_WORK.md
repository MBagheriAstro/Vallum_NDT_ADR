# What’s Done vs What’s Left (Inspection Run Merge)

## Fully implemented (functional today)

| Item | Status |
|------|--------|
| **Ball sensor** | ✅ `BALL_STAGE_LINE_OFFSET=51`, `_read_ball_sensor()`, `GET /api/sensors/ball` |
| **check_stage_clearance** | ✅ Ball sensor + clear stage (ACT1 then ACT2) if not clear |
| **feed_ball_to_stage** | ✅ ACT3 extend → kick motor → 3s wait → ACT3 retract |
| **Capture TOP → flip → BOT** | ✅ 4 images (CAMERA_A_TOP, CAMERA_B_TOP, CAMERA_A_BOT, CAMERA_B_BOT) with timestamp |
| **Single inference worker** | ✅ TOP starts immediately; BOT waits in buffer; no timeout; combine and notify webapp |
| **Process result** | ✅ Good → ACT1 extend/retract; bad → ACT2 extend/retract; TOTAL_BALLS, GOOD_BALLS, BAD_BALLS, LAST_RESULT |
| **Inspection flow** | ✅ Retract → lights on → check stage → feed → ball check → capture → submit TOP → flip → capture → submit BOT → await result → actuate → lights off |
| **Run controls (UI)** | ✅ Start Run, Single Inspection, Stop, Force Stop; status polling; flip duration from Manual tab |
| **Metadata (frontend)** | ✅ Save Metadata / load from sessionStorage; `window.currentMetadata` |

So the **hardware + capture + worker + actuation** path is in place. What’s missing is **real inference** and **persistence/UI**.

---

## Not yet implemented

### 1. Ball extraction + real inference (critical for real decisions)

- **Current:** `_run_extract_and_infer_sync()` is a **stub** – always returns `Normal`, no extraction or model.
- **Needed:**
  - **Ball extraction:** For each of the 2 input paths (TOP or BOT), load image, run extraction (e.g. from VALLUM-NDT-ADR-FRONTEND `utils/ball_extraction` or BACKEND), output 1024×1024 ball image.
  - **Inference:** Run YOLO (or same model as main-hq backend) on the 2 extracted images; return `defect_found`, `prediction`, `probability` per set; worker already combines TOP + BOT.
- **Where:** Implement inside `_run_extract_and_infer_sync()` (or call a small helper that does extract + YOLO). Reuse backend’s model path and class logic.

### 2. Database + save cycle (Phase 3)

- **SQLite:** Create DB (e.g. `webapp/inspections.db`), table(s) for inspection cycles: id, timestamp, lot_number, mfg_name, mfg_part, material, ball_diameter, customer, flip_duration, total_balls, good_balls, bad_balls, result (good/bad/no_ball), image paths (or dir), composite_image_path, etc.
- **Save cycle:** After each inspection (after actuate, before or after lights off), call a `save_inspection_cycle(...)` that writes one row with current stats and metadata.
- **Metadata source:** Backend must get “current run” metadata (lot, mfg, part, material, ball_diameter, customer). Options: frontend POSTs metadata when starting run/single inspection, or backend exposes `GET /api/inspection/metadata` that returns last POSTed metadata (frontend calls a new `POST /api/inspection/metadata` when user clicks Save Metadata).

### 3. History API (frontend already calls it)

- **Endpoints to add:**  
  - `GET /api/history` (list with optional filters/pagination)  
  - `GET /api/history/{id}` (one record)  
  - `DELETE /api/history/{id}`  
  - `DELETE /api/history/bulk` (body: list of ids)  
  - `POST /api/history/export` (body: params; return file or URL)  
- All read/write the same SQLite DB used for save cycle.

### 4. Composite image

- After each cycle, generate one composite image from the 4 inspection images (e.g. 2×2 grid); save to a known path (e.g. under `static/captures/` or a dedicated folder).
- Store that path in the cycle row as `composite_image_path`.
- Optional: serve it via something like `GET /api/images/view/<filename>` so the History tab can show it (frontend already uses `/api/images/view/` + filename).

### 5. Processed images in UI (2×2 grid)

- Inspection Run tab has placeholders for “Camera A TOP”, “Camera B TOP”, “Camera A BOT”, “Camera B BOT” (1024×1024 processed).
- **Needed:** After each cycle, either (a) expose the 4 processed (extracted) image paths in the inspection status or a “last cycle” endpoint, and have the frontend set those as `src` for the 4 cells, or (b) serve them under a stable URL pattern (e.g. `/api/images/view/<cycle_id>_<camera>_<top|bot>.png`) and have the UI refresh from status when a new cycle completes.

### 6. Metadata to backend (for save cycle)

- So that saved cycles have lot, mfg, part, material, ball_diameter, customer:
  - Either: **POST /api/inspection/metadata** – frontend calls it when user clicks Save Metadata (body = metadata object); backend stores in memory or a small table and uses it when saving the next cycle(s).
  - Or: frontend sends metadata in the body of **POST /api/inspection/start** and **POST /api/inspection/single-inspection**; backend uses that for the cycle(s) started by that request.
- Then `save_inspection_cycle()` reads from that stored metadata.

---

## Suggested order to implement

1. **Ball extraction + YOLO** in `_run_extract_and_infer_sync()` so real good/bad decisions and actuation work.
2. **Metadata to backend** (POST metadata or in start/single-inspection body) so we know what to save.
3. **SQLite + save cycle** so every inspection is persisted.
4. **History API** so the History tab works (list, delete, export).
5. **Composite image** generation and store path in DB (and optional view endpoint).
6. **Processed images in UI** so the 2×2 grid shows the latest cycle’s extracted images.

---

## Summary

- **Functional today:** Full inspection sequence (sensors, feed, capture TOP/BOT, single worker with buffer, actuation, stats) runs end-to-end; decisions are **stub** (always “good”).
- **To be fully implemented:** (1) Real ball extraction + inference, (2) DB + save cycle + metadata to backend, (3) History API, (4) Composite image, (5) Processed images in UI.
