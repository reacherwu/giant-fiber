"""GiantFiber (GF-1): Sub-5ms, Sub-1W Bio-Reflex Coprocessor for Autonomous Machines.

Powered by Drosophila Connectome (FlyWire/MaleCNS) and Calibrated Discrete Decision Theory.
"""

from .types import (
    ReflexAction,
    ReflexVector,
    ReflexDecision,
    CircuitTrace,
    EventSpike,
    EventPacket,
    ReflexConfig,
)
from .engine import GiantFiberCoprocessor

__version__ = "0.1.0"
__all__ = [
    "GiantFiberCoprocessor",
    "ReflexAction",
    "ReflexVector",
    "ReflexDecision",
    "CircuitTrace",
    "EventSpike",
    "EventPacket",
    "ReflexConfig",
]
