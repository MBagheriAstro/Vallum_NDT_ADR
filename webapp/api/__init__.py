"""API route modules."""

from .lights import router as lights_router
from .actuators import router as actuators_router
from .motors import router as motors_router
from .system import router as system_router
from .inspection import router as inspection_router
from .history import router as history_router
from .cameras import router as cameras_router
from .images import router as images_router
from .logs import router as logs_router

__all__ = [
    "lights_router",
    "actuators_router",
    "motors_router",
    "system_router",
    "inspection_router",
    "history_router",
    "cameras_router",
    "images_router",
    "logs_router",
]
