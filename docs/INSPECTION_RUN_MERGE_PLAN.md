# Inspection Run: Merge Plan (Single Platform on Jetson)

**Design reference.** This document described the plan to run the Pi 5 + remote inference flow entirely on one Jetson. The **implementation** is in the webapp: `webapp/services/inspection.py` (cycle flow), `webapp/services/inference.py` (worker), `webapp/hardware/` (sensors, camera, motors, flip), `webapp/core/database.py` and `webapp/api/history.py` (persistence). See [ARCHITECTURE.md](ARCHITECTURE.md) and [REMAINING_WORK.md](REMAINING_WORK.md) for current layout and status.

**Goal (historical):** Run the same inspection flow that used to run on **Pi 5 (frontend + hardware)** and **remote inference server**, entirely on **one Jetson Orin Nano**. No network hop; local pipeline inference (no zip, TOP→extract+infer, BOT→extract+infer, then combine and actuate).

---

## Sensor wiring (Pi → Jetson)

- **Pi 5:** GPIO 20 and GPIO 21 = **physical pins 38 and 40** on the 40-pin header.
  - **Pin 38** = blade sensor (kick motor blade in position).
  - **Pin 40** = ball-on-stage sensor.
- **Jetson (same physical header positions):**
  - **Blade:** physical pin 38 → gpiochip0 line offset **52** (already in code as `BLADE_LINE_OFFSET`).
  - **Ball on stage:** physical pin 40 → gpiochip0 line offset **51** (I2S0_SDOUT / gpio399 on some Orin Nano pinouts). Use `BALL_STAGE_LINE_OFFSET = 51`; confirm against your carrier pinout if needed.
- **Logic (same as Pi):** Ball sensor `value == 1` → stage **clear** (no ball). After feed, `value != 1` → ball **detected**.

---

## Old flow (HQ branches: master-HQ frontend, main-hq backend)

The **exact order** from the Pi's `perform_ball_inspection()` (and repeated in a loop for Start Run) was:

1. **Retract all actuators** (ACT1, ACT2, ACT3).
2. **Turn on all lights.**
3. **Check stage clearance** – read ball sensor; `value == 1` means stage clear. If not clear → **clear stage** (ACT1 then ACT2 sweep).
4. **Feed ball to stage** – ACT3 extend → **kick motor** (until blade sensor) → wait ~3 s → ACT3 retract.
5. **Read ball sensor again.** If `value == 1` → no ball → return `ball_detected: false` (after 2 consecutive no-ball cycles the loop stops).
6. **Capture inspection images**
   - **TOP:** 2 cameras (A, B) → 2 images.
   - **Flip motor** (configurable duration).
   - **BOT:** 2 cameras (A, B) → 2 images.  
   **Total: 4 image files** (e.g. `CAMERA_A_TOP_*.png`, `CAMERA_B_TOP_*.png`, `CAMERA_A_BOT_*.png`, `CAMERA_B_BOT_*.png`). Confirmed from **main-hq** backend: `EXPECTED_IMAGE_COUNT = 4` (2 cameras × 2 positions).
7. **HQ backend:** Frontend did **ball extraction** (1024×1024) and sent a **zip** with folder `images_{try}/` and 4 files to the server (TCP: 4-byte length + zip; server responds 4-byte length + JSON). Backend (YOLO) expects **4 pre-extracted 1024×1024 images**.
8. **Process result:** If **Normal** (no defect) and confidence above threshold → **good ball**: ACT1 extend → retract. Otherwise → **bad ball**: ACT2 extend → retract.
9. **Update stats**, save cycle to DB, composite image, broadcast to UI.

**Start Run** = loop until Stop / Force Stop / 2 consecutive no-ball. **Single Inspection** = one cycle then lights off.

---

## What we keep vs what we merge

| Piece | Old (Pi + server) | On Jetson (merged) |
|-------|-------------------|--------------------|
| Hardware control | Pi (GPIO 20/21, Motor HAT, daemons) | Jetson: gpiochip0 line 52 (blade), 51 (ball), Motor HAT, FastAPI |
| Dashboard | Pi (FastAPI + frontend) | Same UI, same buttons |
| Capture | 2 cams TOP → flip → 2 cams BOT = 4 images | Same: 4 images |
| Ball extraction | Pi (frontend utils, 1024×1024) | Jetson: same logic in-process (reuse backend or frontend extraction code) |
| Inference | Remote server (YOLO, 4×1024×1024) | **Local pipeline:** no zip; TOP 2 → extract+infer; BOT 2 → extract+infer; combine result → actuate |
| Process result | Good → ACT1, bad → ACT2 | Same |
| DB + History | Pi | Add on Jetson: SQLite, save cycle, History API |

---

## Pipeline inference (speed: no zip, overlap – do not wait for TOP)

You asked to **not wait** for ball extraction or inference after the first set of pictures; the webapp should proceed with flip and the second set while TOP is processed in the background.

**Chosen approach (single inference worker, BOT waits in buffer):**

We **cannot** run two inferences in parallel (e.g. one GPU, or model not thread-safe). So:

1. **Single inference worker** (one thread): processes one set at a time. When TOP paths arrive, it starts processing TOP (extract + infer). If BOT paths arrive while TOP is still running, **BOT waits in a buffer**. When TOP is done, worker processes BOT (extract + infer), then combines and **informs the webapp** (sets result on an asyncio Future).
2. **Webapp:** Capture TOP 2 → **submit TOP** to worker (gets a Future; worker may start TOP immediately). Flip → capture BOT 2 → **submit BOT** (worker adds BOT to the same cycle; BOT waits if TOP is still running). Webapp **awaits the Future**; when worker finishes TOP then BOT and combines, it sets the result on the Future and webapp continues → actuate (ACT1/ACT2).

So: first set is done first; second set waits in a buffer until first set is done, then we do the second set and then inform the webapp.

---

## Step-by-step implementation plan

### Phase 1 – Sensors, stage clear, feed, capture (same order)

1. **Ball sensor** – Add `BALL_STAGE_LINE_OFFSET = 51` (physical pin 40). Implement `_read_ball_sensor()` using `_gpioget(BALL_STAGE_LINE_OFFSET)`; `value == 1` → stage clear, `value != 1` → ball present.
2. **check_stage_clearance** – Use ball sensor; if not clear, call existing clear stage (ACT1 then ACT2 sweep).
3. **feed_ball_to_stage** – ACT3 extend → kick motor (until blade sensor) → sleep ~3 s → ACT3 retract (reuse existing APIs).
4. **Capture inspection images** – TOP (CAM A, CAM B) → flip (FLIP_DURATION_SEC) → BOT (CAM A, CAM B). Save 4 files with naming matching backend (CAMERA_A_TOP, CAMERA_B_TOP, CAMERA_A_BOT, CAMERA_B_BOT).
5. **Ball extraction** – Reuse HQ logic (frontend or backend): each of the 4 raw images → extract ball → 1024×1024. Output 4 images in a known order for inference.

### Phase 2 – Local pipeline inference (no zip)

6. **In-process inference** – Integrate main-hq backend inference (YOLO + 4×1024×1024) into the webapp process. API: e.g. `run_inference(image_paths_list) -> { prediction, probability, try_number }`. Same decision logic as backend (any defect → bad; else Normal).
7. **Pipeline execution** – **Single inference worker**: one thread, one slot (TOP paths + BOT paths + Future). After TOP 2 captured, **submit TOP** (worker may start TOP immediately; webapp gets a Future). Flip, capture BOT 2, **submit BOT** (BOT waits in buffer until TOP is done). Worker runs TOP extract+infer, then BOT extract+infer, combines, sets result on Future. Webapp awaits Future then actuates.
8. **Process result** – Same as Pi: Normal (and threshold) → ACT1 extend/retract; else → ACT2 extend/retract. Update TOTAL_BALLS, GOOD_BALLS, BAD_BALLS, LAST_RESULT.

### Phase 3 – Database, composite, UI

9. **SQLite** – Table(s) for inspection cycles (metadata, settings, results, image paths, composite path).
10. **Save cycle** – After each inspection, persist cycle with metadata from frontend (e.g. current run metadata API or POST with start run / single inspection).
11. **Composite image** – Generate from the 4 images; save path in DB.
12. **History API** – GET/POST/DELETE/export on the same DB.
13. **Processed images in UI** – Latest cycle result exposes image paths or URLs; 2×2 grid (CAM A TOP/BOT, CAM B TOP/BOT) updates from them.
14. **Metadata for run** – Backend gets current metadata when saving cycle (e.g. frontend POST or GET /api/inspection/metadata).

---

## Summary (what's missing vs added)

| Item | Current Jetson | After plan |
|------|----------------|------------|
| Ball sensor (pin 40) | Missing | Add BALL_STAGE_LINE_OFFSET=51, _read_ball_sensor() |
| check_stage_clearance | Missing | Use ball sensor + clear stage |
| feed_ball_to_stage | Missing | ACT3 + kick + ACT3 |
| Capture TOP → flip → BOT | Not in cycle | 4 images, same order |
| Ball extraction | Missing | In-process, 1024×1024 per image |
| Inference | Missing | In-process YOLO, pipeline (TOP then BOT, combine) |
| process_inspection_result | Missing | ACT1 good / ACT2 bad, update stats |
| Database + History | Missing | SQLite, save cycle, History API |
| Composite + processed images UI | Missing | Generate composite; 2×2 from latest result |
| Metadata to backend | sessionStorage only | API or POST when saving cycle |

---

## Decisions (resolved)

1. **Ball sensor:** Pi physical 38 = blade (Jetson line 52 ✓). Pi physical 40 = ball on stage → Jetson line **51** (confirm on your carrier if needed).
2. **4 images:** Confirmed from main-hq; 2 cams × 2 positions. No 8-image variant.
3. **Inference:** Pipeline, no zip; TOP→extract+infer, BOT→extract+infer, combine; **single process** for speed.
4. **Order:** Confirmed; matches master-HQ frontend flow.
