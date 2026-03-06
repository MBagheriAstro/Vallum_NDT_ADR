"""Flip motor via libgpiod (GPIO, not Motor HAT)."""

import time
from typing import Any

from fastapi import HTTPException

try:
    import gpiod  # type: ignore[import]
except ImportError:
    gpiod = None  # type: ignore[assignment]

_controller: Any = None


class FlipMotorController:
    """Run flip motor for a duration; stop/brake at end."""

    def __init__(self) -> None:
        if gpiod is None:
            raise HTTPException(
                status_code=503,
                detail="gpiod not available; install python3-libgpiod.",
            )
        self.chip = gpiod.Chip("gpiochip0")
        self._pwm_offset = 43  # pin 33, GPIO13
        self._dir_offset = 41  # pin 32, GPIO07
        self.pwm = self.chip.get_line(self._pwm_offset)
        self.dir = self.chip.get_line(self._dir_offset)
        self.pwm.request(consumer="flip_pwm", type=gpiod.LINE_REQ_DIR_OUT)
        self.dir.request(consumer="flip_dir", type=gpiod.LINE_REQ_DIR_OUT)

    def run_for(self, duration: float) -> None:
        self.pwm.set_value(1)
        self.dir.set_value(0)
        time.sleep(duration)
        self.pwm.set_value(1)
        self.dir.set_value(1)


def get_flip_controller() -> FlipMotorController:
    global _controller
    if _controller is None:
        _controller = FlipMotorController()
    return _controller
