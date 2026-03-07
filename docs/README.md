# Vallum-Jetson documentation

Single reference for setup, architecture, hardware, and inspection. The webapp is the only application entry point; scripts are for setup and camera focus only.

**Contents:** 1 [Setup](#1-setup-jetson--one-script) · 2 [Repository layout](#2-repository-layout) · 3 [Hardware](#3-hardware-how-the-webapp-drives-it) · 4 [Inspection flow](#4-inspection-flow-one-cycle) · 5 [Scripts](#5-scripts) · 6 [Status](#6-implementation-status)

---

## 1. Setup (Jetson – one script)

On a **fresh Jetson** after cloning, run once:

```bash
cd Vallum-Jetson
./scripts/setup_jetson.sh
```

You will be prompted for **sudo** once. The script:

- Installs system packages: python3-pip, python3.10-venv, libopenblas-dev, libjpeg-dev, zlib1g-dev, libgpiod-utils, i2c-tools
- Creates `venv/` and installs PyTorch 2.3 + torchvision 0.18 from NVIDIA wheels (GPU)
- Runs `pip install -r requirements.txt` (FastAPI, Motor HAT, ultralytics, opencv, etc.)
- Verifies torch, CUDA, and model path

Then run the webapp:

```bash
cd webapp && ../venv/bin/python main.py
```

- **I2C:** Enable if needed for Motor HAT (`sudo apt install -y i2c-tools`). Check HAT: `sudo i2cdetect -y 1` — expect `60` (PCA9685).
- **Model:** YOLO weights at `webapp/models/best.pt` (in repo). Inference fails with a clear error if the model or deps are missing.
- **Non-Jetson:** `pip install -r requirements.txt` and run with `python3` (CPU inference only).

---

## 2. Repository layout

```
Vallum-Jetson/
├── webapp/                 # Main application
│   ├── main.py             # FastAPI app: static mount, API routers
│   ├── config.py           # Paths, camera config, pin offsets
│   ├── models.py           # Pydantic models
│   ├── core/               # Logging, DB, system stats
│   ├── hardware/           # GPIO, lights, sensors, motors, flip, camera
│   ├── services/           # Inference worker, inspection cycle
│   ├── api/                # Routers: logs, lights, actuators, motors, system, inspection, history, cameras, images
│   ├── models/best.pt      # YOLO model (required for inspection)
│   └── static/             # Frontend (index.html, app.js, style.css, captures/, composites/)
├── scripts/
│   ├── setup_jetson.sh     # One-time full setup (system + venv + PyTorch + pip)
│   └── camera_live_stream.py   # Dual-camera live stream for focus; use --save-config to save exposure/gains for webapp
├── docs/README.md          # This file
├── README.md
└── requirements.txt
```

---

## 3. Hardware (how the webapp drives it)

| Hardware       | Method                    | Notes |
|----------------|---------------------------|--------|
| Lights (1–4)   | gpioset/gpioget           | gpiochip0 offsets 85, 126, 125, 123 (J12 pins 15, 16, 18, 22) |
| Actuators      | Adafruit MotorKit (I2C)   | M2=ACT1, M3=ACT2, M4=ACT3 |
| Kick motor     | MotorKit M1               | Configurable run time |
| Flip motor     | gpiod (Python)            | Duration: seconds (inspection) or ms (Manual Control flip); hardware uses seconds |
| Ball sensor    | gpioget line 51           | value==1 → stage clear |
| Blade sensor   | gpioget line 52           | Active-low; used for kick |
| Cameras        | OpenCV + GStreamer (nvarguscamerasrc) | CSI sensor 0 (A), 1 (B) |

Config (pin offsets, camera defaults) is in `webapp/config.py`. Camera exposure/gains can be overridden by `webapp/camera_config.json` (written by `camera_live_stream.py --save-config`).

**GPIO libraries:**

- **Lights:** **libgpiod-utils** — the webapp calls `gpioset` (subprocess) to set each light line on or off. Four lines: gpiochip0 offsets 85, 126, 125, 123 (J12 pins 15, 16, 18, 22). Code: `webapp/hardware/gpio.py`, `webapp/hardware/lights.py`. System package: `libgpiod-utils`.
- **Ball and blade sensors:** **libgpiod-utils** — the webapp calls `gpioget` (subprocess) to read line 51 (ball: 1 = stage clear) and line 52 (blade). Code: `webapp/hardware/gpio.py`, `webapp/hardware/sensors.py`. Same dependency.
- **Flip motor:** **Python gpiod** — the webapp uses the `gpiod` module to open gpiochip0, request two lines (PWM and direction), set values, sleep for the flip duration, then set brake. Needed because the sequence is timed (hold lines, wait, then change). Code: `webapp/hardware/flip.py`. System package: `python3-libgpiod`.

---

## 4. Inspection flow (one cycle)

1. Retract all actuators → lights on → check stage (ball sensor); if not clear, clear stage (ACT1 then ACT2).
2. Feed ball (ACT3 extend → kick motor → wait → ACT3 retract). Check ball sensor; if clear → no_ball.
3. Capture TOP (cam A, B) → submit to inference worker → flip motor → capture BOT (cam A, B) → submit BOT.
4. Worker runs TOP then BOT (extract + YOLO), combines result.
5. Good → ACT1 extend/retract; bad → ACT2 extend/retract. Update stats, save cycle to SQLite, build composite, lights off.

Start Run = loop until Stop or 2 consecutive no-ball. Single Inspection = one cycle then lights off.

---

## 5. Scripts

- **`scripts/setup_jetson.sh`** — Run once after clone. Installs everything (see Section 1).
- **`scripts/camera_live_stream.py`** — Dual-camera live stream for focus. Set exposure and gains via CLI when you run it; use `--save-config` on exit to write `webapp/camera_config.json` so the webapp uses those values. Example: `python3 scripts/camera_live_stream.py --exposure 100 --red-gain 2.0 --blue-gain 2.0 --save-config`.

There are no other standalone scripts; lights and motors are controlled from the webapp Manual Control tab.

---

## 6. Implementation status

- **Implemented:** Ball/blade sensors, stage clear, feed, capture TOP→flip→BOT, single inference worker (YOLO from `webapp/models/best.pt`), good/bad actuation, SQLite + save cycle, metadata API, history API (list, get, delete, export CSV), composite image, image view API, run controls.
- **Optional:** Processed images in the UI 2×2 grid (API already returns `processed_urls`).

Update this section when you add or change features.
