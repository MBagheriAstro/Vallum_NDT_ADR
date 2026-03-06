"""
Pydantic request/response models for API payloads.
"""

from pydantic import BaseModel


class LightControl(BaseModel):
    """Set one light intensity (1–4). Intensity > 0 = ON, <= 0 = OFF."""
    light_id: int
    intensity: float


class ActuatorControl(BaseModel):
    """Control ACT1/ACT2/ACT3: extend or retract."""
    actuator_name: str
    action: str  # 'extend' | 'retract'
    duration: float = 2.0


class MotorAction(BaseModel):
    """Motor HAT or flip: motor name, action, duration."""
    motor: str  # 'm1'..'m4' for HAT; duration in ms for flip
    action: str
    duration: float = 1.0


class CameraCaptureRequest(BaseModel):
    """Capture one frame from a camera."""
    camera_name: str


class CameraConfig(BaseModel):
    """Camera exposure and gains."""
    exposure_ms: float = 100.0
    red_gain: float = 4.0
    blue_gain: float = 0.5
    analogue_gain: float = 4.0


class InspectionStartPayload(BaseModel):
    """Start run or single inspection; flip_duration in seconds."""
    flip_duration: float = 0.25


class InspectionMetadata(BaseModel):
    """Current run metadata from frontend (Save Metadata)."""
    lotNumber: str | None = None
    mfgName: str | None = None
    mfgPart: str | None = None
    material: str | None = None
    ballDiameter: str | None = None
    customerName: str | None = None


class PowerAction(BaseModel):
    """System power: shutdown or reboot."""
    action: str  # 'shutdown' | 'reboot'
