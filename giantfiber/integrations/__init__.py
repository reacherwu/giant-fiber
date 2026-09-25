"""
GiantFiber Integrations Package
==============================
Bridges and hardware adapters for PX4-Autopilot, CAN-FD, MAVLink, and companion boards.
"""

from giantfiber.integrations.px4_mavlink import (
    PX4ReflexBridge,
    PX4BridgeConfig,
    MAVLinkMessage,
    MAVLinkV2Codec,
)
from giantfiber.integrations.can_fd import (
    CANFDReflexFrame,
    CANFDCodec,
    crc16_ccitt,
)

__all__ = [
    "PX4ReflexBridge",
    "PX4BridgeConfig",
    "MAVLinkMessage",
    "MAVLinkV2Codec",
    "CANFDReflexFrame",
    "CANFDCodec",
    "crc16_ccitt",
]
