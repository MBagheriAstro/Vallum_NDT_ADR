# Vallum-Jetson

**Vallum/Evolve NDT ADR** — dashboard and inspection control for **Jetson (Orin Nano / Nano)**. Single webapp: lights, motors (Motor HAT + flip), cameras, inspection run, and history. Everything runs on the Jetson.

---

## Quick start (Jetson)

```bash
git clone <repo-url>
cd Vallum-Jetson
./scripts/setup_jetson.sh          # once: installs system packages, venv, PyTorch GPU, all deps
cd webapp && ../venv/bin/python main.py
```

Open **http://localhost:8080** (or http://\<jetson-ip\>:8080). API docs: http://localhost:8080/docs

---

## Documentation

**[docs/README.md](docs/README.md)** — Full reference in one place:

| Section | Contents |
|--------|----------|
| 1. Setup | One script, I2C, model path, non-Jetson |
| 2. Repository layout | Folder tree and where things live |
| 3. Hardware | How the webapp drives lights, motors, sensors, cameras |
| 4. Inspection flow | One-cycle steps (capture → infer → actuate) |
| 5. Scripts | `setup_jetson.sh`, `camera_live_stream.py` |
| 6. Status | What’s implemented, optional work |

---

## Main README (this file)

- **Setup:** Run `./scripts/setup_jetson.sh` once after clone (sudo once). Then `cd webapp && ../venv/bin/python main.py`. See [docs/README.md §1](docs/README.md#1-setup-jetson--one-script).
- **Webapp:** Manual Control (lights, actuators, motors), Inspection Run, History. Cameras use CSI (nvarguscamerasrc); if none are connected, capture returns a clear error.
- **Motor mapping:** M1 = kick, M2 = ACT1, M3 = ACT2, M4 = ACT3 (Motor HAT). Flip motor = GPIO (separate). Same as Pi frontend.
- **GPIO:** Lights and sensors use **libgpiod-utils** (gpioset/gpioget). Flip motor uses the **Python gpiod** library. See [docs/README.md §3](docs/README.md#3-hardware-how-the-webapp-drives-it).
- **Troubleshooting:** I2C/Motor HAT, permissions, Jetson-specific notes — see [docs/README.md](docs/README.md) and section below.

---

## GPIO and hardware control

| Part           | Library / tool              | Code / dependency |
|----------------|-----------------------------|-------------------|
| **Lights (4)** | **libgpiod-utils**          | `gpioset` (subprocess). Offsets 85, 126, 125, 123 → pins 15, 16, 18, 22. Install: `libgpiod-utils`. |
| **Ball sensor**| **libgpiod-utils**          | `gpioget` (subprocess). Line 51 → stage clear (1) or ball present (0). |
| **Blade sensor** | **libgpiod-utils**        | `gpioget` (subprocess). Line 52 → used for kick motor. |
| **Flip motor** | **Python gpiod**            | `gpiod.Chip`, request two lines (PWM + dir), set values, sleep, then brake. Install: `python3-libgpiod`. |

Pin offsets are in `webapp/config.py`. Actuators and kick motor use the Motor HAT (I2C), not GPIO.

---

## Troubleshooting

- **MotorKit / I2C:** Enable I2C, check HAT at 0x60: `sudo i2cdetect -y 1`. Add user to `i2c` group: `sudo usermod -a -G i2c $USER` (then log out/in or reboot).
- **Permission denied on /dev/i2c-***: Same as above or run with sudo (not ideal long-term).
- **Jetson Orin Nano detection:** See [Adafruit Blinka on Linux](https://learn.adafruit.com/circuitpython-libraries-on-linux-and-the-nvidia-jetson-nano) for I2C bus numbers if Blinka fails.

For full hardware and setup detail, see [docs/README.md](docs/README.md).
