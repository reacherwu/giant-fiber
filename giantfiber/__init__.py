"""GiantFiber (GF-1): event-camera looming-escape reflex modelled on the Drosophila Giant Fiber circuit."""

from .engine import GiantFiberCoprocessor
from .envelope import (
    VALIDATED_MAX_CLOSING_SPEED_MPS,
    OutsideEnvelopeWarning,
    check_threat_speed,
)
from .types import (
    CircuitTrace,
    EscapeMode,
    EventPacket,
    EventSpike,
    ReflexAction,
    ReflexConfig,
    ReflexDecision,
    ReflexVector,
)

__version__ = "0.2.0"
__all__ = [
    "VALIDATED_MAX_CLOSING_SPEED_MPS",
    "CircuitTrace",
    "EscapeMode",
    "EventPacket",
    "EventSpike",
    "GiantFiberCoprocessor",
    "OutsideEnvelopeWarning",
    "ReflexAction",
    "ReflexConfig",
    "ReflexDecision",
    "ReflexVector",
    "check_threat_speed",
]
