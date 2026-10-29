"""Type-safe System-1 decision contracts and schemas for GiantFiber (GF-1).

Pydantic models for the discrete action contract returned by the engine.
"""

from enum import IntEnum

from pydantic import BaseModel, ConfigDict, Field


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
    """Pathway that produced the decision."""
    UNKNOWN = 0
    GIANT_FIBER = 1          # DNp01 spike -> short-mode escape
    NON_GF_DESCENDING = 2    # parallel descending pathway -> long-mode escape
    HEADING_INTEGRATOR = 3   # no escape; heading-hold output


class EscapeMode(IntEnum):
    """Escape mode of the current episode (von Reyn et al. 2014)."""
    NONE = 0
    LONG = 1
    SHORT = 2


class ReflexDecision(BaseModel):
    """System-1 Type-Safe Calibrated Decision Output Contract."""
    model_config = ConfigDict(frozen=True)

    action: ReflexAction = Field(description="Discrete reflex action candidate")
    confidence: float = Field(ge=0.0, le=1.0, description="Softmax confidence of the decision gate (monotone in threshold margin; not a calibrated probability)")
    vector: ReflexVector = Field(default_factory=ReflexVector, description="3-Axis flight control or joint torque")
    compute_latency_us: int = Field(ge=0, description="Microseconds of pure inference compute")
    circuit_trace: CircuitTrace = Field(default=CircuitTrace.UNKNOWN, description="Firing connectome sub-circuit")
    timestamp_us: int = Field(ge=0, description="Microsecond timestamp of evaluation")
    triggered: bool = Field(default=False, description="True on the step an escape is initiated or upgraded")
    escape_mode: EscapeMode = Field(default=EscapeMode.NONE, description="Escape mode of the current episode")


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

    events: list[EventSpike] = Field(default_factory=list)
    sensor_width: int = Field(default=320)
    sensor_height: int = Field(default=320)


class ReflexConfig(BaseModel):
    """Engine parameters. Defaults mirror `EngineConfig::default()` in Rust; they are modelling choices."""
    confidence_threshold: float = Field(default=0.85, ge=0.0, le=1.0)
    temperature: float = Field(default=1.0, ge=0.1, le=5.0)
    looming_threshold: float = Field(default=0.55, gt=0.0, le=1.0, description="Giant Fiber threshold (normalised drive)")
    decay_tau_us: float = Field(default=25000.0, ge=1000.0)
    refractory_period_us: int = Field(default=300000, ge=0)
    fov_deg: float = Field(default=90.0, gt=10.0, lt=170.0)
    gf_tau_us: float = Field(default=10000.0, ge=100.0)
    nongf_threshold: float = Field(default=0.30, gt=0.0, le=1.0)
    nongf_tau_us: float = Field(default=30000.0, ge=100.0)
    lplc2_half_size_deg: float = Field(default=20.0, gt=0.0)
    lc4_half_velocity_dps: float = Field(default=200.0, gt=0.0)
    long_mode_window_us: float = Field(default=30000.0, ge=0.0)
    min_edge_cells: int = Field(default=6, ge=1)
