# Vallum-Jetson Architecture

This document describes how the codebase is organized and how the webapp controls hardware. It is the single source of truth for developers.

---

## Repository layout

```
Vallum-Jetson/
├── webapp/                    # Main application (dashboard + API)
│   ├── main.py                # FastAPI app entry: mounts static, includes API routers
│   ├── config.py              # Paths, camera config, actuator names, pin offsets
│   ├── models.py              # Pydantic request/response models
│   ├── ball_extraction.py     # Ball crop/resize for inference
│   ├── inference_yolo.py      # YOLO inference on extracted images
│   ├── core/                  # Logging, DB, system stats
│   │   ├── logging_config.py
│   │   ├── database.py
│   │   └── system.py
│   ├── hardware/              # GPIO, lights, sensors, motors, flip, camera
│   │   ├── gpio.py, lights.py, sensors.py, motors.py, flip.py, camera.py
│   ├── services/              # Inference worker, inspection cycle logic
│   │   ├── inference.py, inspection.py
│   ├── api/                   # API routers (logs, lights, actuators, motors, system, inspection, history, cameras, images)
│   ├── inspection_history.db  # SQLite DB (created at runtime)
│   └── static/                # Frontend (served by FastAPI)
│       ├── index.html, app.js, style.css
│       ├── captures/          # Camera captures
│       ├── composites/        # Composite images per cycle
│       └── vallum_logo.png, evolve_logo.png
├── docs/
│   ├── ARCHITECTURE.md        # This file
│   ├── SCRIPTS.md             # Note: standalone scripts removed; webapp only
│   ├── INSPECTION_RUN_MERGE_PLAN.md
│   └── REMAINING_WORK.md
├── README.md
└── requirements.txt
```

---

## How the webapp controls hardware

The webapp drives hardware via modules under **`webapp/hardware/`** and **`webapp/services/`**. Entry point is **`webapp/main.py`** (FastAPI app with API routers).

| Hardware        | How the webapp drives it                    | Notes |
|-----------------|---------------------------------------------|--------|
| **Lights (1–4)**| `gpioset` / `gpioget` (libgpiod-utils)       | Pin mapping: gpiochip0 offsets 85, 126, 125, 123 (J12 pins 15, 16, 18, 22). On/off only; no PWM in webapp. |
| **Actuators**   | Adafruit MotorKit (I2C PCA9685 HAT)         | M2=ACT1, M3=ACT2, M4=ACT3. Extend/retract/stop. |
| **Kick motor**  | MotorKit M1                                 | Run for configurable time. |
| **Flip motor**  | `gpiod` Python (in-process)                 | gpiochip0; separate from Motor HAT. Duration in seconds. |
| **Ball sensor** | `gpioget` gpiochip0 line 51                 | value==1 → stage clear; value!=1 → ball present. |
| **Blade sensor**| `gpioget` gpiochip0 line 52                 | Active-low: 0 → blade horizontal. |
| **Cameras**     | OpenCV + GStreamer (nvarguscamerasrc)        | CSI sensors 0 (cam A) and 1 (cam B). |

Constants (e.g. `LIGHT_LINE_OFFSETS`, `BLADE_LINE_OFFSET`, `BALL_STAGE_LINE_OFFSET`) are defined in `webapp/config.py` and used by `webapp/hardware/`.

---

## Main application entry

- **Run the webapp:** `python3 webapp/main.py` (or `cd webapp && python3 main.py`). Listens on `http://0.0.0.0:8080`.
- **API docs:** FastAPI exposes OpenAPI at `/docs` when the server is running.

---

## Frontend

- Single-page app: `index.html` + `app.js` + `style.css`.
- Tabs: **Inspection Run**, **Manual Control**, **History**.
- API client objects in `app.js`: `inspectionApi`, `lightsApi`, `systemApi`, `historyApi`, etc. All call the FastAPI backend; no direct hardware access.

---

## Inspection flow (one cycle)

1. Retract all actuators.
2. Lights on (gpioset all light lines to 1).
3. Check stage (ball sensor); if not clear, clear stage (ACT1 then ACT2 sweep).
4. Feed ball (ACT3 extend → kick motor → wait 3s → ACT3 retract).
5. Check ball sensor again; if clear → return no_ball.
6. Capture TOP (cam A, cam B) → submit TOP to inference worker.
7. Flip motor (gpiod, duration from UI).
8. Capture BOT (cam A, cam B) → submit BOT to worker (waits if TOP still running).
9. Worker: extract + YOLO on TOP, then on BOT; combine result.
10. Actuate: good → ACT1 extend/retract; bad → ACT2 extend/retract.
11. Update stats; save cycle to SQLite; lights off.

---

## Dependencies

- **Backend:** FastAPI, uvicorn, pydantic, libgpiod-utils (gpioset/gpioget), Adafruit MotorKit/Blinka (Motor HAT), opencv-python (cameras), python3-libgpiod (flip motor). Optional: cv2, YOLO stack for real inference.
- **Frontend:** Vanilla JS; no build step. Static files served from `webapp/static/`.

See `requirements.txt` and README for setup.

---

## Code organization and practices

- **Entrypoint:** `webapp/main.py` is a thin FastAPI app: mounts static files, serves `index.html` at `/`, and includes all API routers. No business logic in `main.py`.
- **Config:** Paths, camera settings, pin offsets, and actuator names live in `webapp/config.py`. Used by `hardware/` and `services/`.
- **API:** Routers live under `webapp/api/` (logs, lights, actuators, motors, system, inspection, history, cameras, images). Each router is focused on one area.
- **Hardware:** All GPIO, lights, sensors, motors, flip, and camera access are in `webapp/hardware/`. Blocking calls are run via `asyncio.to_thread()` from the API or services.
- **Services:** Inspection cycle logic and the single inference worker live in `webapp/services/` (inspection.py, inference.py). Inference uses `inference_yolo.run_inference_on_paths` when available, otherwise returns a stub result.
- **Frontend:** Single-page app (`index.html`, `app.js`, `style.css`); no build step. API calls use client objects in `app.js`.
- **Errors:** Missing hardware (MotorKit, gpiod, cv2) is handled with try/import and 503 responses; logs go to an in-memory buffer for the UI log panel.

---

## Documentation index

| Document | Purpose |
|----------|---------|
| [README.md](../README.md) | Setup, usage, webapp. |
| [ARCHITECTURE.md](ARCHITECTURE.md) | This file: layout, hardware, flow. |
| [SCRIPTS.md](SCRIPTS.md) | Note that standalone scripts were removed; webapp only. |
| [INSPECTION_RUN_MERGE_PLAN.md](INSPECTION_RUN_MERGE_PLAN.md) | Design reference for inspection flow (Pi → Jetson merge). |
| [REMAINING_WORK.md](REMAINING_WORK.md) | Current implementation status and optional TODOs. |
