"""
CAN-FD reflex frame codec and Linux SocketCAN transport.

- 24-byte payload (a valid CAN-FD length, DLC 12) with CRC-16-CCITT.
- Byte-identical to the Rust `giantfiber_core::canfd` encoder (no_std), checked by tests.
- `SocketCANFDBus` sends/receives frames on a Linux CAN-FD interface (e.g. `vcan0`, `can0`)
  using the standard library's AF_CAN socket. It has been tested on `vcan` only when such an
  interface exists on the test machine (the test is skipped otherwise); it has not been tested
  on physical CAN hardware.
"""

from __future__ import annotations
import math
import socket
import struct
import sys
import time
from dataclasses import dataclass
from typing import Optional, Tuple

from giantfiber.types import ReflexAction, ReflexDecision, ReflexVector


def crc16_ccitt(data: bytes) -> int:
    """Computes CRC-16-CCITT (polynomial 0x1021)."""
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte << 8)
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


@dataclass
class CANFDReflexFrame:
    """High-speed physical CAN-FD reflex packet."""
    can_id: int = 0x18F02001  # Priority 6 (High), Source 0x20 (GiantFiber), Dest 0x01 (FC)
    is_extended: bool = True
    action: ReflexAction = ReflexAction.CRUISE
    confidence: float = 0.0
    roll_rate_deg_s: float = 0.0
    pitch_rate_deg_s: float = 0.0
    yaw_rate_deg_s: float = 0.0
    thrust: float = 0.0
    compute_latency_us: int = 0
    timestamp_ms: int = 0
    sequence: int = 0
    escape_mode: int = 0


def _round_sat(v: float, lo: int, hi: int) -> int:
    """Round half up and saturate (same rule as the Rust encoder)."""
    if not math.isfinite(v):
        return 0
    return int(min(hi, max(lo, math.floor(v + 0.5))))


def _f32(v: float) -> float:
    """Round to float32 so the encoder sees the same value as the Rust (f32) side."""
    return struct.unpack("<f", struct.pack("<f", v))[0]


class CANFDCodec:
    """
    CAN-FD Frame Codec.
    
    Payload layout (24 bytes):
      [0..1]   Magic: b'GF' (0x47, 0x46)
      [2..3]   Sequence: uint16
      [4]      Action: uint8 (ReflexAction enum)
      [5]      Confidence: uint8 (0..255 -> 0.0..1.0)
      [6..7]   Roll Rate: int16 (0.1 deg/s)
      [8..9]   Pitch Rate: int16 (0.1 deg/s)
      [10..11] Yaw Rate: int16 (0.1 deg/s)
      [12..13] Thrust: uint16 (0..1000 permille)
      [14..15] Compute Latency: uint16 (microseconds)
      [16..19] Timestamp: uint32 (millisecond timestamp)
      [20]     Escape mode: uint8 (0 none, 1 long, 2 short)
      [21]     Reserved: uint8 (0)
      [22..23] CRC16: uint16 (CRC-16-CCITT over bytes 0..21)
    """

    MAGIC = b"GF"
    FRAME_LEN = 24

    @staticmethod
    def encode(frame: CANFDReflexFrame) -> bytes:
        conf_u8 = _round_sat(_f32(_f32(frame.confidence) * 255.0), 0, 255)
        roll_i16 = _round_sat(_f32(_f32(frame.roll_rate_deg_s) * 10.0), -32768, 32767)
        pitch_i16 = _round_sat(_f32(_f32(frame.pitch_rate_deg_s) * 10.0), -32768, 32767)
        yaw_i16 = _round_sat(_f32(_f32(frame.yaw_rate_deg_s) * 10.0), -32768, 32767)
        thrust_u16 = _round_sat(_f32(_f32(frame.thrust) * 1000.0), 0, 1000)
        lat_u16 = min(65535, max(0, int(frame.compute_latency_us)))
        ts_u32 = frame.timestamp_ms & 0xFFFFFFFF

        header = struct.pack(
            "<2sHBBhhhHHIBB",
            CANFDCodec.MAGIC,
            frame.sequence % 65536,
            int(frame.action),
            conf_u8,
            roll_i16,
            pitch_i16,
            yaw_i16,
            thrust_u16,
            lat_u16,
            ts_u32,
            int(frame.escape_mode) & 0xFF,
            0,  # reserved
        )
        crc = crc16_ccitt(header)
        return header + struct.pack("<H", crc)

    @staticmethod
    def decode(payload: bytes, can_id: int = 0x18F02001) -> Optional[CANFDReflexFrame]:
        if len(payload) < CANFDCodec.FRAME_LEN:
            return None
        if payload[:2] != CANFDCodec.MAGIC:
            return None

        # Checksum check
        received_crc = struct.unpack_from("<H", payload, 22)[0]
        expected_crc = crc16_ccitt(payload[:22])
        if received_crc != expected_crc:
            return None

        (
            magic,
            seq,
            action_raw,
            conf_u8,
            roll_i16,
            pitch_i16,
            yaw_i16,
            thrust_u16,
            lat_u16,
            ts_u32,
            escape_mode,
            _reserved,
        ) = struct.unpack("<2sHBBhhhHHIBB", payload[:22])

        action_enum = ReflexAction(action_raw) if action_raw in ReflexAction._value2member_map_ else ReflexAction.CRUISE
        conf = float(conf_u8) / 255.0

        return CANFDReflexFrame(
            can_id=can_id,
            is_extended=True,
            action=action_enum,
            confidence=conf,
            roll_rate_deg_s=float(roll_i16) / 10.0,
            pitch_rate_deg_s=float(pitch_i16) / 10.0,
            yaw_rate_deg_s=float(yaw_i16) / 10.0,
            thrust=float(thrust_u16) / 1000.0,
            compute_latency_us=lat_u16,
            timestamp_ms=ts_u32,
            sequence=seq,
            escape_mode=escape_mode,
        )

    @staticmethod
    def from_decision(decision: ReflexDecision, seq: int = 0, timestamp_ms: Optional[int] = None) -> CANFDReflexFrame:
        """Converts high-level ReflexDecision into physical CAN-FD frame setpoint."""
        roll_rate = 0.0
        pitch_rate = 0.0
        thrust = 0.7

        if decision.action == ReflexAction.ROLL_RIGHT_90:
            roll_rate = 300.0
            thrust = 0.95
        elif decision.action == ReflexAction.ROLL_LEFT_90:
            roll_rate = -300.0
            thrust = 0.95
        elif decision.action == ReflexAction.PITCH_UP:
            pitch_rate = 200.0
            thrust = 0.90
        elif decision.action == ReflexAction.BRAKE:
            pitch_rate = -200.0
            thrust = 1.0

        ts_ms = (int(time.time() * 1000) if timestamp_ms is None else timestamp_ms) & 0xFFFFFFFF
        return CANFDReflexFrame(
            action=decision.action,
            confidence=decision.confidence,
            roll_rate_deg_s=roll_rate,
            pitch_rate_deg_s=pitch_rate,
            thrust=thrust,
            compute_latency_us=decision.compute_latency_us,
            timestamp_ms=ts_ms,
            sequence=seq,
            escape_mode=int(decision.escape_mode),
        )


# ----------------------------------------------------------------------------
# Linux SocketCAN (CAN-FD) transport
# ----------------------------------------------------------------------------

CAN_EFF_FLAG = 0x80000000
CAN_EFF_MASK = 0x1FFFFFFF
_CANFD_FRAME_FMT = "<IBBBB64s"  # struct canfd_frame
CANFD_MTU = struct.calcsize(_CANFD_FRAME_FMT)  # 72


def pack_canfd_frame(can_id: int, data: bytes, extended: bool = True, flags: int = 0) -> bytes:
    """Builds a Linux `struct canfd_frame` (72 bytes)."""
    if len(data) > 64:
        raise ValueError("CAN-FD payload is at most 64 bytes")
    cid = (can_id & CAN_EFF_MASK) | (CAN_EFF_FLAG if extended else 0)
    return struct.pack(_CANFD_FRAME_FMT, cid, len(data), flags, 0, 0, data.ljust(64, b"\x00"))


def unpack_canfd_frame(raw: bytes) -> Tuple[int, bytes]:
    cid, length, _flags, _r0, _r1, data = struct.unpack(_CANFD_FRAME_FMT, raw[:CANFD_MTU])
    return cid & CAN_EFF_MASK, data[:length]


class SocketCANFDBus:
    """Minimal CAN-FD raw socket (Linux only). Use `vcan0` for testing:
    `sudo ip link add dev vcan0 type vcan && sudo ip link set vcan0 mtu 72 up`."""

    def __init__(self, interface: str = "vcan0", can_id: int = 0x18F02001):
        if not sys.platform.startswith("linux") or not hasattr(socket, "AF_CAN"):
            raise OSError("SocketCAN is only available on Linux")
        self.can_id = can_id
        self.sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
        self.sock.setsockopt(socket.SOL_CAN_RAW, socket.CAN_RAW_FD_FRAMES, 1)
        self.sock.bind((interface,))

    def send_frame(self, frame: CANFDReflexFrame) -> None:
        self.sock.send(pack_canfd_frame(frame.can_id, CANFDCodec.encode(frame)))

    def send_decision(self, decision: ReflexDecision, seq: int) -> CANFDReflexFrame:
        frame = CANFDCodec.from_decision(decision, seq)
        frame.can_id = self.can_id
        self.send_frame(frame)
        return frame

    def recv_frame(self, timeout_s: float = 1.0) -> Optional[CANFDReflexFrame]:
        self.sock.settimeout(timeout_s)
        try:
            raw = self.sock.recv(CANFD_MTU)
        except socket.timeout:
            return None
        cid, data = unpack_canfd_frame(raw)
        return CANFDCodec.decode(data, can_id=cid)

    def close(self) -> None:
        self.sock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
