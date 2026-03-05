"""
Software PWM light control on Jetson Orin Nano using libgpiod.

We drive four light channels connected to J12 header pins 15, 16, 18, 22.
According to the JetsonHacks Orin Nano J12 pinout, these map to gpiochip0
line offsets as follows (sysfs column in the table):

  - Pin 15  -> offset  85 (GPIO12, Alt: PWM)
  - Pin 16  -> offset 126 (SPI1_CS1)
  - Pin 18  -> offset 125 (SPI1_CS0)
  - Pin 22  -> offset 123 (SPI1_MISO)

We keep this mapping here so the rest of the app can rely on it.
"""

from __future__ import annotations

import threading
import time
from typing import Dict

import gpiod  # Requires python3-libgpiod / gpiod Python bindings

CHIP_NAME = "gpiochip0"

# Light IDs (1–4) to gpiochip0 line offsets.
LIGHT_LINE_OFFSETS: Dict[int, int] = {
    1: 85,   # J12 pin 15
    2: 126,  # J12 pin 16
    3: 125,  # J12 pin 18
    4: 123,  # J12 pin 22
}

# Software PWM configuration.
PWM_FREQ_HZ = 300.0
PWM_PERIOD_S = 1.0 / PWM_FREQ_HZ


class _LightController:
    def __init__(self) -> None:
        self._chip = gpiod.Chip(CHIP_NAME)
        self._lines: Dict[int, gpiod.Line] = {}
        self._duty: Dict[int, float] = {lid: 0.0 for lid in LIGHT_LINE_OFFSETS}
        self._stop_events: Dict[int, threading.Event] = {}
        self._threads: Dict[int, threading.Thread] = {}
        self._lock = threading.Lock()

    def _ensure_line(self, light_id: int) -> gpiod.Line:
        if light_id not in LIGHT_LINE_OFFSETS:
            raise ValueError("light_id must be 1, 2, 3, or 4")
        if light_id in self._lines:
            return self._lines[light_id]
        offset = LIGHT_LINE_OFFSETS[light_id]
        line = self._chip.get_line(offset)
        line.request(consumer="vallum_lights", type=gpiod.LINE_REQ_DIR_OUT)
        self._lines[light_id] = line
        return line

    def _pwm_loop(self, light_id: int) -> None:
        line = self._ensure_line(light_id)
        stop = self._stop_events[light_id]
        period = PWM_PERIOD_S

        while not stop.is_set():
            with self._lock:
                duty = self._duty.get(light_id, 0.0)

            if duty <= 0.0:
                line.set_value(0)
                time.sleep(period)
            elif duty >= 1.0:
                line.set_value(1)
                time.sleep(period)
            else:
                on_time = period * duty
                off_time = period - on_time
                line.set_value(1)
                time.sleep(on_time)
                line.set_value(0)
                time.sleep(off_time)

        # Ensure the line is low when stopping.
        try:
            line.set_value(0)
        except Exception:
            pass

    def _start_thread(self, light_id: int) -> None:
        if light_id in self._threads and self._threads[light_id].is_alive():
            return
        self._stop_events[light_id] = threading.Event()
        t = threading.Thread(target=self._pwm_loop, args=(light_id,), daemon=True)
        self._threads[light_id] = t
        t.start()

    def set_light(self, light_id: int, intensity: float) -> None:
        """
        Set one light's intensity, where intensity is 0.0–1.0.
        """
        if light_id not in LIGHT_LINE_OFFSETS:
            raise ValueError("light_id must be 1, 2, 3, or 4")
        clamped = max(0.0, min(1.0, float(intensity)))
        with self._lock:
            self._duty[light_id] = clamped
        # Ensure the PWM thread is running for this light.
        self._start_thread(light_id)

    def turn_off_all(self) -> None:
        """
        Turn off all lights and stop PWM threads.
        """
        with self._lock:
            for lid in self._duty:
                self._duty[lid] = 0.0

        # Give PWM loops a moment to drive lines low.
        time.sleep(PWM_PERIOD_S * 2)

        # Signal threads to stop.
        for ev in self._stop_events.values():
            ev.set()

        # Wait briefly for threads.
        for t in self._threads.values():
            if t.is_alive():
                t.join(timeout=0.1)

        self._stop_events.clear()
        self._threads.clear()

        # Drive all lines low explicitly.
        for lid in list(self._lines.keys()):
            line = self._lines[lid]
            try:
                line.set_value(0)
            except Exception:
                pass

    def cleanup(self) -> None:
        """
        Best-effort cleanup; safe to call on shutdown.
        """
        self.turn_off_all()
        try:
            self._chip.close()
        except Exception:
            pass


_controller = _LightController()


def set_light(light_id: int, intensity: float) -> None:
    """
    Public API: match the signature used from FastAPI.
    """
    _controller.set_light(light_id, intensity)


def turn_off_all() -> None:
    """
    Public API: match the signature used from FastAPI.
    """
    _controller.turn_off_all()


def cleanup() -> None:
    _controller.cleanup()

