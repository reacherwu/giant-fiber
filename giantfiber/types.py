"""Type-safe System-1 decision contracts and schemas for GiantFiber (GF-1).

Enforces Pydantic validation over discrete action spaces, eliminating
unconstrained LLM text generation and token hallucinations.
"""

from enum import IntEnum
from typing import List, Optional
from pydantic import BaseModel, Field, ConfigDict


class ReflexAction(IntEnum):
    """Discrete System-1 physical reflex actions."""
    CRUISE = 0
    BRAKE = 1
    ROLL_LEFT_90 = 2
    ROLL_RIGHT_90 = 3
    PITCH_UP = 4
    PITCH_DOWN = 5
    STIFFEN_LEGS = 6
    EMERGENCY_KILL = 7


class ReflexVector(BaseModel):
    """Normalized 3-axis control deflection vector (-1.0 to 1.0)."""
    model_config = ConfigDict(frozen=True)

    x: float = Field(default=0.0, description="Lateral roll/deflection (-1.0 left, +1.0 right)")
    y: float = Field(default=0.0, description="Vertical pitch/dive (-1.0 down, +1.0 up)")
    z: float = Field(default=0.0, description="Longitudinal thrust/brake (-1.0 reverse, +1.0 forward)")


class CircuitTrace(IntEnum):
    """Originating Drosophila connectome functional sub-circuit."""
    UNKNOWN = 0
    GIANT_FIBER_L1 = 1
    LOBULA_PLATE_OPTIC = 2
    CENTRAL_COMPLEX_COMPASS = 3
    CHEMICAL_SENTINEL = 4


class ReflexDecision(BaseModel):
    """System-1 Type-Safe Calibrated Decision Output Contract."""
    model_config = ConfigDict(frozen=True)

    action: ReflexAction = Field(description="Discrete reflex action candidate")
    confidence: float = Field(ge=0.0, le=1.0, description="Mathematically calibrated certainty P in [0.0, 1.0]")
    vector: ReflexVector = Field(default_factory=ReflexVector, description="3-Axis flight control or joint torque")
    compute_latency_us: int = Field(ge=0, description="Microseconds of pure inference compute")
    circuit_trace: CircuitTrace = Field(default=CircuitTrace.UNKNOWN, description="Firing connectome sub-circuit")
    timestamp_us: int = Field(ge=0, description="Microsecond timestamp of evaluation")
    triggered: bool = Field(default=False, description="True if reflex fired above threshold; False if cruise")
    power_estimated_mw: float = Field(default=512.0, description="Estimated total board power consumption in mW")


class EventSpike(BaseModel):
    """Single neuromorphic asynchronous event spike."""
    model_config = ConfigDict(frozen=True)

    timestamp_us: int = Field(ge=0)
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    polarity: int = Field(default=1, description="+1 (ON event) or -1 (OFF event)")


class EventPacket(BaseModel):
    """Batch of neuromorphic event spikes."""
    model_config = ConfigDict(frozen=True)

    events: List[EventSpike] = Field(default_factory=list)
    sensor_width: int = Field(default=320)
    sensor_height: int = Field(default=320)


class ReflexConfig(BaseModel):
    """Configurable hyperparameters for the GiantFiber coprocessor."""
    confidence_threshold: float = Field(default=0.85, ge=0.5, le=1.0)
    temperature: float = Field(default=1.0, ge=0.1, le=5.0)
    looming_threshold: float = Field(default=0.65, ge=0.1, le=5.0)
    decay_tau_us: float = Field(default=25000.0, ge=1000.0)
    refractory_period_us: int = Field(default=50000, ge=0)
