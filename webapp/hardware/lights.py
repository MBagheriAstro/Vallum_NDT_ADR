"""Light control: on/off via gpioset."""

from .. import config
from .gpio import gpioset


def lights_on_sync() -> None:
    """Turn all four lights on (blocking)."""
    for offset in config.LIGHT_LINE_OFFSETS.values():
        gpioset(offset, 1)


def lights_off_sync() -> None:
    """Turn all four lights off (blocking)."""
    for offset in config.LIGHT_LINE_OFFSETS.values():
        gpioset(offset, 0)
