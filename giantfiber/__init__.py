"""GiantFiber (GF-1): event-camera looming-escape reflex modelled on the Drosophila Giant Fiber circuit."""

from .types import (
    ReflexAction,
    ReflexVector,
    ReflexDecision,
    CircuitTrace,
    EscapeMode,
    EventSpike,
    EventPacket,
    ReflexConfig,
)
from .engine import GiantFiberCoprocessor

__version__ = "0.2.0"
__all__ = [
    "GiantFiberCoprocessor",
    "ReflexAction",
    "ReflexVector",
    "ReflexDecision",
    "CircuitTrace",
    "EscapeMode",
    "EventSpike",
    "EventPacket",
    "ReflexConfig",
]
