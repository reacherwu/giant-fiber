"""
GiantFiber Integrations Package
==============================
Bridges and hardware adapters for PX4-Autopilot, CAN-FD, MAVLink, and companion boards.
"""

from giantfiber.integrations.can_fd import (
    CANFDCodec,
    CANFDReflexFrame,
    crc16_ccitt,
)
from giantfiber.integrations.px4_mavlink import (
    MAVLinkMessage,
    MAVLinkV2Codec,
    PX4BridgeConfig,
    PX4ReflexBridge,
)

__all__ = [
    "CANFDCodec",
    "CANFDReflexFrame",
    "MAVLinkMessage",
    "MAVLinkV2Codec",
    "PX4BridgeConfig",
    "PX4ReflexBridge",
    "crc16_ccitt",
]
