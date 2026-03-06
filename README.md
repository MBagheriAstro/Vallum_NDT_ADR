# Vallum-Jetson

**Vallum/Evolve NDT ADR** dashboard and inspection control for **Jetson (Orin Nano / Nano)**. Single webapp: lights, motors (Motor HAT + flip GPIO), cameras, inspection run, and history. Everything runs on the Jetson.

## Documentation

| Document | Description |
|----------|-------------|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Code layout, how the webapp drives hardware, inspection flow. |

## Setup

1. **Enable I2C** on the Jetson (e.g. Jetson-IO or `sudo apt install -y i2c-tools`).
2. **Check HAT address:** `sudo i2cdetect -y 1` — expect `60` (PCA9685).
3. **Install dependencies** (optional venv):
   ```bash
   cd Vallum-Jetson
   python3 -m venv venv   # optional
   source venv/bin/activate
   pip install -r requirements.txt
   ```
4. **libgpiod-utils** (for lights/sensors): `sudo apt install libgpiod-utils` (and `python3-libgpiod` for flip motor).
5. **Inspection inference (GPU on Jetson):** The YOLO model is at `webapp/models/best.pt`. For **Jetson GPU inference**, run **`./scripts/setup_jetson_inference.sh`** from the repo root first; it installs PyTorch 2.3 and torchvision 0.18 from NVIDIA wheels (matching the old backend) plus ultralytics, opencv-python, Pillow, numpy. See [docs/JETSON_INFERENCE_SETUP.md](docs/JETSON_INFERENCE_SETUP.md). On non-Jetson, `pip install -r requirements.txt` is enough (CPU inference).

## Webapp (main application)

Run the dashboard and API:

```bash
cd webapp && python3 main.py
```

- **URL:** http://localhost:8080 (or http://\<jetson-ip\>:8080).
- **API docs:** http://localhost:8080/docs

The webapp controls lights (gpioset), actuators and kick motor (MotorKit), flip motor (gpiod), ball/blade sensors (gpioget), and cameras (OpenCV/GStreamer) directly. Hardware testing (lights, motors, actuators) is done via the **Manual Control** tab in the dashboard.

## Motor mapping (same as Pi frontend)

| Motor   | Pi frontend use | Throttle        |
|---------|------------------|-----------------|
| **M1**  | Kick motor       | 1.0 = run       |
| **M2**  | ACT1 (actuator)  | -1 = extend, 1 = retract |
| **M3**  | ACT2             | -1 = extend, 1 = retract |
| **M4**  | ACT3             | -1 = extend, 1 = retract  |

On the Pi, the **flip** motor is not on the HAT (it uses GPIO PWM pins 13/12). The webapp controls M1–M4 on the HAT plus the flip motor via GPIO.

## Lights (webapp / Manual Control)

Same as Pi 5: **BCM 22, 23, 24, 25** = **physical pins 15, 16, 18, 22** on the 40-pin header. The webapp uses **gpioset** (libgpiod) with gpiochip0 offsets 85, 126, 125, 123. Use the dashboard **Manual Control** tab to turn lights on/off. If lights do not respond, confirm wiring and that `libgpiod-utils` is installed (`sudo apt install libgpiod-utils`).

### JetPack 6: GPIO requires BCT pinmux change and reflash

On **JetPack 6.0**, the kernel uses the **upstream GPIO driver**, which does **not** support changing a pin into GPIO mode at runtime. So Jetson-IO and userspace cannot "switch" a pin to GPIO after boot. The pinmux is fixed in the **BCT (Boot Configuration Table)**. To use header pins (e.g. 15, 16, 18, 22) as GPIO you must:

1. **Modify the pinmux** in the BCT for your module/carrier so the desired pins are configured as GPIO.
2. For each GPIO pin: **disable the E_IO_HV (3.3V Tolerance Enable)** field in the pinmux register (or disable "3.3V Tolerance Enable" in the pinmux spreadsheet).
3. Set **Pin Direction** to **Bidirectional** so userspace can use the pin as both input and output.
4. **Reflash the board** with the updated pinmux/BCT.

Documentation: [NVIDIA Jetson Module Adaptation and Bring-Up – Pinmux changes](https://docs.nvidia.com/jetson/archives/r36.3/DeveloperGuide/HR/JetsonModuleAdaptationAndBringUp/JetsonAgxOrinSeries.html?highlight=pin%20direction#pinmux-changes) (process is similar for Orin Nano; use your module’s pinmux spreadsheet and BCT layout).

After reflashing with the corrected pinmux, test lights from the webapp **Manual Control** tab (or voltage tests).

### Jetson Nano J12 pinout (from expansion header tables)

| Physical pin | Module name   | SoC / default   | Type        | Notes |
|--------------|---------------|-----------------|-------------|--------|
| **15**       | GPIO12        | GP88_PWM1       | GPIO (Bidir)| HW PWM capable |
| **16**       | SPI1_CSI1*    | GP40            | GPIO (Bidir/Output) | Alternate: SPI CS1 |
| **18**       | SPI1_CSI0*    | GP39            | GPIO (Bidir/Output) | Alternate: SPI CS0 |
| **22**       | SPI1_MISO     | GP37            | GPIO (Bidir/Input)  | We drive as output |

- Pins 15, 16, 18, 22 default to **GPIO**; we set them as outputs. Pin 22 is documented as Bidir/Input but works as output when configured.
- The “weak output drivers” note (TI TXB0108 level translators) in the tables applies to pins **26, 29, 31, 32, 33, 35, 36, 37, 38, 40** — not 15, 16, 18, 22.
- If SPI is enabled and claiming 16/18/22, those pins may not behave as GPIO; disable the SPI overlay or conflicting device tree if lights still don’t respond.

### Check if SPI is enabled (and might be using pins 16, 18, 22)

Run on the Jetson:

```bash
# 1. See if SPI devices exist (if so, SPI is enabled and may be using our pins)
ls -l /dev/spi*

# 2. See which SPI controllers exist in the device tree / kernel
ls /sys/bus/spi/devices/ 2>/dev/null || true

# 3. Optional: see loaded modules related to SPI
lsmod | grep -i spi
```

- If `ls /dev/spi*` shows devices (e.g. `spidev0.0`), SPI is enabled. Pins 16, 18, 22 might be muxed to SPI; try disabling SPI via Jetson-IO (or your board's device tree) and re-test the lights.
- If you get "No such file or directory" for `/dev/spi*`, SPI is not enabled and is unlikely to be blocking the GPIO pins.

### Turn off SPI (free pins 16, 18, 22 for lights)

Use **Jetson-IO** so the 40-pin header SPI pins become GPIO. Run on the Jetson (with display/keyboard or SSH with X forwarding if the tool is GUI):

1. **Launch the config tool:**
   ```bash
   sudo /opt/nvidia/jetson-io/jetson-io.py
   ```

2. In the menu: choose **"Configure Jetson 40-pin Header"** (or equivalent 40-pin expansion header option).

3. Choose **"Configure header pins manually"** (or "By function" / "By pin" depending on your menu).

4. **Disable SPI:** Find the SPI function(s) (e.g. SPI1, SPI2) and **deselect** or set those pins to **GPIO** so they are no longer used for SPI. The pins that were SPI (e.g. 16, 18, 22) will then be available as GPIO.

5. **Save:** Apply/save the pin changes, then choose **"Save and reboot to reconfigure pins"** (or equivalent). Reboot.

6. After reboot, check that SPI is off and lights work:
   ```bash
   ls /dev/spi*          # should get "No such file or directory"
   # Then use the webapp Manual Control tab to test lights
   ```

**If Jetson-IO shows the pins as "unused" and nothing to disable:** SPI is enabled in the **base device tree**, not by the header tool. Use the provided overlay to disable the expansion-header SPI controllers:

1. **Copy the overlay to `/boot`:**
   ```bash
   sudo cp /media/jetson/Data/Programs/Vallum_NDT_ADR/Vallum-Jetson/tegra234-disable-spi-expansion.dtbo /boot/
   ```

2. **Add it to the boot entry** in `/boot/extlinux/extlinux.conf`. Under the `LABEL JetsonIO` (or the entry you boot from), find the `OVERLAYS` line and add the new overlay **first** so it is applied before the camera overlay, for example:
   ```
   OVERLAYS /boot/tegra234-disable-spi-expansion.dtbo,/boot/tegra234-p3767-camera-p3768-imx477-dual.dtbo
   ```
   Use a **comma** between overlay files (no spaces). If you have no other overlays, use:
   ```
   OVERLAYS /boot/tegra234-disable-spi-expansion.dtbo
   ```

3. **Reboot.** After reboot, run `ls /dev/spi*` — you should get "No such file or directory". Then test lights from the webapp Manual Control tab.

To **re-enable SPI** later, remove the overlay from the `OVERLAYS` line and reboot.

## Troubleshooting

- **MotorKit init fails:** Ensure I2C is enabled and the HAT is at 0x60. Try:
  ```bash
  sudo usermod -a -G i2c $USER
  # then log out/in or reboot
  ```
- **Permission denied on /dev/i2c-***: Add your user to the `i2c` group (see above) or run with `sudo` (not ideal long-term).
- **Jetson Orin Nano:** Blinka and PlatformDetect support this board; if detection fails, see [Adafruit Blinka on Linux](https://learn.adafruit.com/circuitpython-libraries-on-linux-and-the-nvidia-jetson-nano) and Jetson-specific I2C bus numbers (e.g. which bus the 40-pin header uses).
