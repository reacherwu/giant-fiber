"""
PX4-Autopilot MAVLink v2 bridge for GiantFiber
==============================================
Companion-computer bridge between PX4 (offboard mode) and the GiantFiber escape reflex.

- Pure-Python MAVLink v2 encoder/decoder for the handful of messages used here
  (HEARTBEAT, SET_ATTITUDE_TARGET, SET_POSITION_TARGET_LOCAL_NED, HIGHRES_IMU).
- HIGHRES_IMU ingestion for the heading integrator.
- On an escape decision, streams an acceleration setpoint (SET_POSITION_TARGET_LOCAL_NED)
  away from the threat for `evade_duration_s`. This is the command path that was exercised
  against PX4 v1.15.4 SITL (see docs/sim/). The older single SET_ATTITUDE_TARGET body-rate
  packet is kept as `dispatch_evasion_override` for comparison; in SITL it did not produce an
  evasive maneuver (see README).

PX4 SITL "Onboard" MAVLink instance: PX4 listens on UDP 14580 and sends to UDP 14540.
"""

from __future__ import annotations
import math
import socket
import struct
import time
from dataclasses import dataclass, field
from typing import Optional, Tuple, Dict, Any, Callable

from giantfiber.types import ReflexAction, ReflexDecision, ReflexConfig
from giantfiber.engine import GiantFiberCoprocessor


# ============================================================================
# Pure-Python MAVLink v2 Frame Codec (Zero-Dependency, Microsecond Speed)
# ============================================================================

def _crc_accumulate(b: int, crc: int) -> int:
    """X.25 / MCRF4XX CRC16 accumulator."""
    tmp = b ^ (crc & 0xFF)
    tmp = (tmp ^ (tmp << 4)) & 0xFF
    crc = ((crc >> 8) ^ (tmp << 8) ^ (tmp << 3) ^ (tmp >> 4)) & 0xFFFF
    return crc


def calculate_mavlink_crc(data: bytes, crc_extra: int) -> int:
    """Computes MAVLink v2 checksum with msg-specific CRC seed."""
    crc = 0xFFFF
    for byte in data:
        crc = _crc_accumulate(byte, crc)
    crc = _crc_accumulate(crc_extra, crc)
    return crc


# Standard MAVLink CRC Extra Seeds for relevant message IDs
MAVLINK_CRC_EXTRA: Dict[int, int] = {
    0: 50,    # HEARTBEAT
    82: 49,   # SET_ATTITUDE_TARGET
    84: 143,  # SET_POSITION_TARGET_LOCAL_NED
    105: 93,  # HIGHRES_IMU
}

# Standard MAVLink Full Payload Lengths (before MAVLink 2 zero-truncation)
MAVLINK_MESSAGE_LENGTHS: Dict[int, int] = {
    0: 9,     # HEARTBEAT
    82: 39,   # SET_ATTITUDE_TARGET
    84: 53,   # SET_POSITION_TARGET_LOCAL_NED
    105: 62,  # HIGHRES_IMU
}

# SET_POSITION_TARGET_LOCAL_NED type_mask: use only the acceleration fields
POSITION_TARGET_TYPEMASK_ACCEL_ONLY = 0b0000110000111111  # ignore pos, vel, yaw, yaw_rate (3135)
MAV_FRAME_LOCAL_NED = 1

# Standard MAVLink ATTITUDE_TARGET_TYPEMASK Bitflags
ATTITUDE_TARGET_TYPEMASK_BODY_ROLL_RATE_IGNORE = 1 << 0   # 1
ATTITUDE_TARGET_TYPEMASK_BODY_PITCH_RATE_IGNORE = 1 << 1  # 2
ATTITUDE_TARGET_TYPEMASK_BODY_YAW_RATE_IGNORE = 1 << 2    # 4
ATTITUDE_TARGET_TYPEMASK_THROTTLE_IGNORE = 1 << 6         # 64
ATTITUDE_TARGET_TYPEMASK_ATTITUDE_IGNORE = 1 << 7         # 128 (0x80)


@dataclass
class MAVLinkMessage:
    msgid: int
    sysid: int = 1
    compid: int = 191
    seq: int = 0
    payload: bytes = b""
    fields: Dict[str, Any] = field(default_factory=dict)


class MAVLinkV2Codec:
    """High-speed binary MAVLink v2 packet encoder and decoder."""
    MAGIC = 0xFD

    @staticmethod
    def encode(msg: MAVLinkMessage) -> bytes:
        payload_len = len(msg.payload)
        incompat_flags = 0
        compat_flags = 0
        header = struct.pack(
            "<BBBBBBB3s",
            MAVLinkV2Codec.MAGIC,
            payload_len,
            incompat_flags,
            compat_flags,
            msg.seq % 256,
            msg.sysid,
            msg.compid,
            (msg.msgid).to_bytes(3, "little"),
        )
        data_to_crc = header[1:] + msg.payload
        crc_extra = MAVLINK_CRC_EXTRA.get(msg.msgid, 0)
        crc = calculate_mavlink_crc(data_to_crc, crc_extra)
        return header + msg.payload + struct.pack("<H", crc)

    @staticmethod
    def decode(packet: bytes) -> Optional[MAVLinkMessage]:
        if len(packet) < 12 or packet[0] != MAVLinkV2Codec.MAGIC:
            return None
        payload_len, incompat, compat, seq, sysid, compid = struct.unpack_from("<BBBBBB", packet, 1)
        msgid = int.from_bytes(packet[7:10], "little")
        total_len = 10 + payload_len + 2
        if len(packet) < total_len:
            return None
        payload = packet[10:10 + payload_len]
        received_crc = struct.unpack_from("<H", packet, 10 + payload_len)[0]
        data_to_crc = packet[1:10 + payload_len]
        crc_extra = MAVLINK_CRC_EXTRA.get(msgid, 0)
        expected_crc = calculate_mavlink_crc(data_to_crc, crc_extra)
        if received_crc != expected_crc:
            return None  # Checksum mismatch
        
        # MAVLink 2 zero-byte truncation handling: pad payload to expected full length
        expected_full_len = MAVLINK_MESSAGE_LENGTHS.get(msgid, len(payload))
        if len(payload) < expected_full_len:
            payload = payload.ljust(expected_full_len, b"\x00")

        parsed_fields = MAVLinkV2Codec.parse_payload(msgid, payload)
        return MAVLinkMessage(
            msgid=msgid,
            sysid=sysid,
            compid=compid,
            seq=seq,
            payload=payload,
            fields=parsed_fields,
        )

    @staticmethod
    def parse_payload(msgid: int, payload: bytes) -> Dict[str, Any]:
        fields: Dict[str, Any] = {}
        if msgid == 105 and len(payload) >= 62:  # HIGHRES_IMU
            (
                time_usec,
                xacc, yacc, zacc,
                xgyro, ygyro, zgyro,
                xmag, ymag, zmag,
                abs_pressure, diff_pressure, pressure_alt, temperature,
                fields_updated,
            ) = struct.unpack("<QfffffffffffffH", payload[:62])
            fields = {
                "time_usec": time_usec,
                "xacc": xacc, "yacc": yacc, "zacc": zacc,
                "xgyro": xgyro, "ygyro": ygyro, "zgyro": zgyro,
                "temperature": temperature,
                "fields_updated": fields_updated,
            }
        elif msgid == 0 and len(payload) >= 9:  # HEARTBEAT
            custom_mode, m_type, autopilot, base_mode, system_status, mavlink_version = struct.unpack(
                "<IBBBBB", payload[:9]
            )
            fields = {
                "custom_mode": custom_mode,
                "type": m_type,
                "autopilot": autopilot,
                "base_mode": base_mode,
                "system_status": system_status,
                "mavlink_version": mavlink_version,
            }
        elif msgid == 84 and len(payload) >= 53:  # SET_POSITION_TARGET_LOCAL_NED
            vals = struct.unpack("<I11fHBBB", payload[:53])
            fields = {
                "time_boot_ms": vals[0],
                "pos": vals[1:4], "vel": vals[4:7], "acc": vals[7:10],
                "yaw": vals[10], "yaw_rate": vals[11],
                "type_mask": vals[12], "target_sys": vals[13], "target_comp": vals[14], "frame": vals[15],
            }
        elif msgid == 82 and len(payload) >= 39:  # SET_ATTITUDE_TARGET (Wire layout: <I8fBBB)
            (
                time_boot_ms,
                q0, q1, q2, q3,
                body_roll_rate, body_pitch_rate, body_yaw_rate,
                thrust,
                target_sys,
                target_comp,
                type_mask,
            ) = struct.unpack("<I8fBBB", payload[:39])
            fields = {
                "time_boot_ms": time_boot_ms,
                "q": (q0, q1, q2, q3),
                "body_roll_rate": body_roll_rate,
                "body_pitch_rate": body_pitch_rate,
                "body_yaw_rate": body_yaw_rate,
                "thrust": thrust,
                "target_sys": target_sys,
                "target_comp": target_comp,
                "type_mask": type_mask,
            }
        return fields

    @staticmethod
    def pack_set_attitude_target(
        time_boot_ms: int,
        target_sys: int,
        target_comp: int,
        type_mask: int,
        q: Tuple[float, float, float, float],
        body_roll_rate: float,
        body_pitch_rate: float,
        body_yaw_rate: float,
        thrust: float,
    ) -> bytes:
        """Packs a MAVLink SET_ATTITUDE_TARGET payload according to official MAVLink wire format (<I8fBBB, 39 bytes)."""
        return struct.pack(
            "<I8fBBB",
            time_boot_ms,
            q[0], q[1], q[2], q[3],
            body_roll_rate,
            body_pitch_rate,
            body_yaw_rate,
            thrust,
            target_sys,
            target_comp,
            type_mask,
        )

    @staticmethod
    def pack_set_position_target_local_ned(
        time_boot_ms: int,
        target_sys: int,
        target_comp: int,
        type_mask: int,
        pos: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        vel: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        acc: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        yaw: float = 0.0,
        yaw_rate: float = 0.0,
        frame: int = MAV_FRAME_LOCAL_NED,
    ) -> bytes:
        """SET_POSITION_TARGET_LOCAL_NED payload (wire order <I11fHBBB, 53 bytes)."""
        return struct.pack(
            "<I11fHBBB",
            time_boot_ms & 0xFFFFFFFF,
            pos[0], pos[1], pos[2],
            vel[0], vel[1], vel[2],
            acc[0], acc[1], acc[2],
            yaw, yaw_rate,
            type_mask, target_sys, target_comp, frame,
        )

    @staticmethod
    def pack_heartbeat(autopilot: int = 12, vehicle_type: int = 2) -> bytes:
        """Packs standard MAVLink HEARTBEAT payload (9 bytes)."""
        return struct.pack("<IBBBBB", 0, vehicle_type, autopilot, 0, 4, 3)


# ============================================================================
# PX4 Bridge Configuration & Evasion Guardian
# ============================================================================

@dataclass
class PX4BridgeConfig:
    """Configuration for connecting GiantFiber to PX4 Autopilot."""
    px4_ip: str = "127.0.0.1"
    px4_port: int = 14580       # PX4 SITL onboard instance listens here
    local_port: int = 14540     # ... and sends telemetry here
    companion_sysid: int = 1
    companion_compid: int = 191 # MAV_COMP_ID_ONBOARD_COMPUTER
    target_sysid: int = 1
    target_compid: int = 1      # MAV_COMP_ID_AUTOPILOT1
    evasion_roll_rate_rads: float = 5.23599   # legacy body-rate command (300 deg/s)
    evasion_thrust_boost: float = 0.95        # legacy collective thrust
    accel_short_mode: float = 9.0             # m/s^2, GF short-mode escape
    accel_long_mode: float = 5.0              # m/s^2, non-GF long-mode escape
    evade_duration_s: float = 0.5             # how long the acceleration setpoint is streamed
    reflex_config: ReflexConfig = field(default_factory=ReflexConfig)


def escape_acceleration_ned(decision: ReflexDecision, yaw_rad: float, cfg: "PX4BridgeConfig") -> Tuple[float, float, float]:
    """Acceleration setpoint (NED, m/s^2) for an escape decision.

    The reflex vector gives the direction in the body frame (x: -1 left / +1 right,
    y: +1 climb / -1 descend); short mode uses `accel_short_mode`, long mode `accel_long_mode`.
    """
    from giantfiber.types import EscapeMode
    a = cfg.accel_short_mode if decision.escape_mode == EscapeMode.SHORT else cfg.accel_long_mode
    lat = decision.vector.x
    vert = decision.vector.y
    n = math.hypot(lat, vert) or 1.0
    lat, vert = lat / n, vert / n
    # body right axis in NED for heading yaw: (-sin(yaw), cos(yaw), 0)
    ax = -math.sin(yaw_rad) * lat * a
    ay = math.cos(yaw_rad) * lat * a
    az = -vert * a  # NED: climb is negative z
    return (ax, ay, az)


class PX4ReflexBridge:
    """
    Companion bridge connecting GiantFiber to PX4 (offboard mode).

    Subscribes to HIGHRES_IMU (105) for the heading integrator.
    On an escape decision `step_eval` starts an evasion; `evasion_packet()` returns the
    SET_POSITION_TARGET_LOCAL_NED (84) acceleration setpoint to stream while it is active.
    """

    def __init__(self, config: Optional[PX4BridgeConfig] = None):
        self.config = config or PX4BridgeConfig()
        self.coprocessor = GiantFiberCoprocessor(self.config.reflex_config)
        self.sock: Optional[socket.socket] = None
        self.seq = 0
        self.total_imu_ingested = 0
        self.total_overrides_dispatched = 0
        self.last_decision: Optional[ReflexDecision] = None
        self.is_connected = False
        self.yaw_rad = 0.0
        self.evasion_until_s = 0.0
        self.evasion_acc = (0.0, 0.0, 0.0)

    def connect(self) -> None:
        """Initializes non-blocking UDP socket to communicate with PX4."""
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        try:
            self.sock.bind(("0.0.0.0", self.config.local_port))
        except OSError:
            # Port may already be bound or testing on ephemeral port
            pass
        self.is_connected = True

    def close(self) -> None:
        """Closes UDP socket and tears down coprocessor."""
        if self.sock:
            self.sock.close()
            self.sock = None
        self.is_connected = False
        self.coprocessor.close()

    def __enter__(self) -> "PX4ReflexBridge":
        self.connect()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()

    def handle_message(self, msg: MAVLinkMessage) -> Optional[ReflexDecision]:
        """Processes a decoded MAVLinkMessage and updates internal state."""
        if msg.msgid == 105:  # HIGHRES_IMU
            f = msg.fields
            if "xgyro" in f and "ygyro" in f and "zgyro" in f and "xacc" in f and "yacc" in f and "zacc" in f:
                self.total_imu_ingested += 1
                # Ingest angular rates (rad/s) and linear accelerations (m/s^2)
                self.coprocessor.update_imu(
                    gyro=(f["xgyro"], f["ygyro"], f["zgyro"]),
                    accel=(f["xacc"], f["yacc"], f["zacc"]),
                    timestamp_us=f.get("time_usec", 0),
                )
        return None

    handle_mavlink_message = handle_message

    def handle_incoming_bytes(self, data: bytes) -> Optional[ReflexDecision]:
        """
        Parses incoming MAVLink bytes from PX4 and updates internal state.
        Returns latest reflex decision if an evaluation occurred.
        """
        msg = MAVLinkV2Codec.decode(data)
        if not msg:
            return None
        return self.handle_message(msg)

    def feed_visual_spike(self, x: int, y: int, timestamp_us: int, polarity: int = 1) -> None:
        """Feeds a microsecond DVS event spike into the bio-reflex coprocessor."""
        self.coprocessor.feed_spike(x, y, timestamp_us, polarity)

    def step_eval(self, now_us: int, wall_time_s: Optional[float] = None) -> ReflexDecision:
        """Runs one engine step. On an escape decision, starts (or upgrades) the evasion
        and sends the first acceleration setpoint immediately."""
        decision = self.coprocessor.step_eval(now_us)
        self.last_decision = decision
        if decision.triggered:
            now = time.monotonic() if wall_time_s is None else wall_time_s
            self.evasion_acc = escape_acceleration_ned(decision, self.yaw_rad, self.config)
            self.evasion_until_s = now + self.config.evade_duration_s
            self.send(self.evasion_packet())
        return decision

    def evasion_active(self, wall_time_s: Optional[float] = None) -> bool:
        now = time.monotonic() if wall_time_s is None else wall_time_s
        return now < self.evasion_until_s

    def evasion_packet(self) -> bytes:
        """Encoded SET_POSITION_TARGET_LOCAL_NED acceleration setpoint for the active evasion."""
        payload = MAVLinkV2Codec.pack_set_position_target_local_ned(
            time_boot_ms=int(time.monotonic() * 1000),
            target_sys=self.config.target_sysid,
            target_comp=self.config.target_compid,
            type_mask=POSITION_TARGET_TYPEMASK_ACCEL_ONLY,
            acc=self.evasion_acc,
        )
        self.seq += 1
        return MAVLinkV2Codec.encode(MAVLinkMessage(
            msgid=84, sysid=self.config.companion_sysid, compid=self.config.companion_compid,
            seq=self.seq, payload=payload))

    def send(self, packet: bytes) -> None:
        if self.sock and self.is_connected:
            try:
                self.sock.sendto(packet, (self.config.px4_ip, self.config.px4_port))
            except OSError:
                pass

    def dispatch_evasion_override(self, decision: ReflexDecision) -> bytes:
        """Legacy command: a single SET_ATTITUDE_TARGET body-rate packet (300 deg/s roll, thrust 0.95).

        Kept for comparison. In PX4 SITL a single packet is immediately superseded by the next
        offboard setpoint, so it does not move the vehicle away from the threat (see README).
        """
        # Determine body rate / quaternion target based on reflex action
        roll_rate = 0.0
        pitch_rate = 0.0
        yaw_rate = 0.0
        thrust = self.config.evasion_thrust_boost

        # Type mask: We command body angular rates (roll, pitch, yaw) and thrust,
        # so we set ATTITUDE_TARGET_TYPEMASK_ATTITUDE_IGNORE (0x80) to ignore quaternion attitude.
        # Bits 0, 1, 2 must be 0 so body roll rate, pitch rate, and yaw rate are NOT ignored!
        type_mask = ATTITUDE_TARGET_TYPEMASK_ATTITUDE_IGNORE  # 0x80 (128)

        if decision.action == ReflexAction.ROLL_RIGHT_90:
            roll_rate = self.config.evasion_roll_rate_rads
            # 90-degree right roll quaternion: roll=pi/2 -> cos(pi/4), sin(pi/4), 0, 0
            q = (0.7071, 0.7071, 0.0, 0.0)
        elif decision.action == ReflexAction.ROLL_LEFT_90:
            roll_rate = -self.config.evasion_roll_rate_rads
            q = (0.7071, -0.7071, 0.0, 0.0)
        elif decision.action == ReflexAction.PITCH_UP:
            pitch_rate = self.config.evasion_roll_rate_rads * 0.75
            q = (0.9238, 0.0, 0.3826, 0.0)
        elif decision.action == ReflexAction.BRAKE:
            pitch_rate = -self.config.evasion_roll_rate_rads * 0.75
            thrust = 1.0  # Max reverse counter-thrust
            q = (1.0, 0.0, 0.0, 0.0)
        else:
            q = (1.0, 0.0, 0.0, 0.0)

        time_boot_ms = int(time.time() * 1000) & 0xFFFFFFFF
        payload = MAVLinkV2Codec.pack_set_attitude_target(
            time_boot_ms=time_boot_ms,
            target_sys=self.config.target_sysid,
            target_comp=self.config.target_compid,
            type_mask=type_mask,
            q=q,
            body_roll_rate=roll_rate,
            body_pitch_rate=pitch_rate,
            body_yaw_rate=yaw_rate,
            thrust=thrust,
        )

        self.seq += 1
        msg = MAVLinkMessage(
            msgid=82,  # SET_ATTITUDE_TARGET
            sysid=self.config.companion_sysid,
            compid=self.config.companion_compid,
            seq=self.seq,
            payload=payload,
        )

        packet = MAVLinkV2Codec.encode(msg)
        self.total_overrides_dispatched += 1

        if self.sock and self.is_connected:
            try:
                self.sock.sendto(packet, (self.config.px4_ip, self.config.px4_port))
            except (OSError, socket.error):
                pass

        return packet
