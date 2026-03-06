"""GPIO via libgpiod-utils (gpioset/gpioget)."""

import subprocess
from fastapi import HTTPException

from .. import config


def gpioset(offset: int, value: int) -> None:
    """Set one gpiochip0 line. Raises HTTPException on failure."""
    try:
        subprocess.run(
            ["gpioset", "--mode=exit", "gpiochip0", f"{offset}={value}"],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=503,
            detail="gpioset not found. Install libgpiod-utils.",
        )
    except subprocess.CalledProcessError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"gpioset failed: {exc.stderr or exc.stdout or exc}",
        )


def gpioget(offset: int) -> int:
    """Read one gpiochip0 line. Returns 0 or 1. On failure returns 1 (safe default)."""
    try:
        res = subprocess.run(
            ["gpioget", "gpiochip0", str(offset)],
            check=True,
            capture_output=True,
            text=True,
        )
        return 1 if (res.stdout or "").strip() == "1" else 0
    except Exception:
        return 1
