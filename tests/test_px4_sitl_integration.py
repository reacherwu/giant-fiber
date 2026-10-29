"""
Integration tests for PX4-Autopilot MAVLink Bridge and SITL Evasion
"""

import math
import struct
import unittest

from _stimuli import run_loom

from giantfiber import EscapeMode, ReflexAction, ReflexDecision, ReflexVector
from giantfiber.integrations.px4_mavlink import (
    POSITION_TARGET_TYPEMASK_ACCEL_ONLY,
    MAVLinkMessage,
    MAVLinkV2Codec,
    PX4BridgeConfig,
    PX4ReflexBridge,
    escape_acceleration_ned,
)


class TestPX4MAVLinkIntegration(unittest.TestCase):
    """Verifies bit-exact MAVLink v2 encoding, PX4 telemetry parsing, and evasion dispatch."""

    def test_mavlink_heartbeat_codec(self):
        """Tests standard MAVLink HEARTBEAT serialization and parsing."""
        payload = MAVLinkV2Codec.pack_heartbeat(autopilot=12, vehicle_type=2)
        msg = MAVLinkMessage(msgid=0, sysid=1, compid=1, seq=42, payload=payload)
        packet = MAVLinkV2Codec.encode(msg)

        self.assertEqual(packet[0], 0xFD)  # MAVLink v2 magic byte
        decoded = MAVLinkV2Codec.decode(packet)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.msgid, 0)
        self.assertEqual(decoded.sysid, 1)
        self.assertEqual(decoded.compid, 1)
        self.assertEqual(decoded.seq, 42)
        self.assertEqual(decoded.fields.get("autopilot"), 12)
        self.assertEqual(decoded.fields.get("type"), 2)

    def test_mavlink_highres_imu_ingestion(self):
        """Tests packing and parsing PX4 HIGHRES_IMU telemetry."""
        # 62 bytes payload
        # Q (time_usec uint64), 13 floats (xacc..temperature), H (fields_updated uint16)
        time_usec = 1000200
        xacc, yacc, zacc = 0.12, -0.05, 9.81
        xgyro, ygyro, zgyro = 0.01, -0.02, 0.05
        xmag, ymag, zmag = 0.2, 0.1, -0.4
        abs_pressure, diff_pressure, pressure_alt, temp = 1013.25, 0.0, 50.0, 24.5
        fields_updated = 0xFFFF

        payload = struct.pack(
            "<QfffffffffffffH",
            time_usec,
            xacc, yacc, zacc,
            xgyro, ygyro, zgyro,
            xmag, ymag, zmag,
            abs_pressure, diff_pressure, pressure_alt, temp,
            fields_updated,
        )
        msg = MAVLinkMessage(msgid=105, sysid=1, compid=1, seq=1, payload=payload)
        packet = MAVLinkV2Codec.encode(msg)

        bridge = PX4ReflexBridge(PX4BridgeConfig())
        bridge.handle_incoming_bytes(packet)

        self.assertEqual(bridge.total_imu_ingested, 1)
        bridge.close()

    def test_evasion_acceleration_setpoint(self):
        """A looming stimulus on the left triggers an escape that streams an
        acceleration-only SET_POSITION_TARGET_LOCAL_NED (84) pointing right."""
        bridge = PX4ReflexBridge(PX4BridgeConfig())
        # no connect(): packets are built but not sent
        decisions, first = run_loom(
            lambda t: bridge.step_eval(t, wall_time_s=t * 1e-6),
            bridge.coprocessor.feed_raw_spikes,
            rv_ms=10.0, offset_x=-0.1,
        )
        self.assertIsNotNone(first, "loom must trigger an escape")
        self.assertIn(first.escape_mode, (EscapeMode.SHORT, EscapeMode.LONG))
        self.assertGreater(first.vector.x, 0.0, "object on the left -> escape to the right")
        self.assertTrue(bridge.evasion_active(wall_time_s=first.timestamp_us * 1e-6 + 0.1))
        self.assertFalse(bridge.evasion_active(wall_time_s=first.timestamp_us * 1e-6 + 1.0))

        pkt = bridge.evasion_packet()
        dec = MAVLinkV2Codec.decode(pkt)
        self.assertIsNotNone(dec)
        self.assertEqual(dec.msgid, 84)
        self.assertEqual(dec.fields["type_mask"], POSITION_TARGET_TYPEMASK_ACCEL_ONLY)
        # yaw = 0 (facing north): body right is +East = +y in NED
        afx, afy, afz = dec.fields["acc"]
        self.assertGreater(afy, 0.0)
        self.assertAlmostEqual(afx, 0.0, places=5)
        mag = (afx ** 2 + afy ** 2 + afz ** 2) ** 0.5
        last = [d for d in decisions if d.triggered][-1]  # a long episode may be upgraded to short
        expected = (bridge.config.accel_short_mode if last.escape_mode == EscapeMode.SHORT
                    else bridge.config.accel_long_mode)
        self.assertAlmostEqual(mag, expected, places=3)
        bridge.close()

    def test_escape_acceleration_respects_yaw(self):
        cfg = PX4BridgeConfig()
        d = ReflexDecision(action=ReflexAction.ROLL_RIGHT_90, confidence=0.9,
                           vector=ReflexVector(x=1.0, y=0.0, z=0.0), compute_latency_us=1,
                           timestamp_us=1, triggered=True, escape_mode=EscapeMode.SHORT)
        ax, ay, az = escape_acceleration_ned(d, math.pi / 2, cfg)  # facing east -> right is south
        self.assertAlmostEqual(ax, -cfg.accel_short_mode, places=4)
        self.assertAlmostEqual(ay, 0.0, places=4)
        self.assertAlmostEqual(az, 0.0, places=4)

    def test_set_position_target_matches_pymavlink(self):
        """Our hand-written encoder must agree with the reference pymavlink parser."""
        try:
            from pymavlink.dialects.v20 import common as mavlink2
        except ImportError:
            self.skipTest("pymavlink not installed")
        payload = MAVLinkV2Codec.pack_set_position_target_local_ned(
            time_boot_ms=1234, target_sys=1, target_comp=1,
            type_mask=POSITION_TARGET_TYPEMASK_ACCEL_ONLY, acc=(0.5, -9.0, 1.25))
        pkt = MAVLinkV2Codec.encode(MAVLinkMessage(msgid=84, sysid=1, compid=191, seq=7, payload=payload))
        mav = mavlink2.MAVLink(None)
        m = mav.parse_char(pkt)
        self.assertIsNotNone(m)
        self.assertEqual(m.get_type(), "SET_POSITION_TARGET_LOCAL_NED")
        self.assertEqual(m.type_mask, POSITION_TARGET_TYPEMASK_ACCEL_ONLY)
        self.assertAlmostEqual(m.afy, -9.0, places=5)
        self.assertEqual(m.coordinate_frame, 1)  # MAV_FRAME_LOCAL_NED

    def test_legacy_attitude_override_packet(self):
        """The legacy single-packet SET_ATTITUDE_TARGET is still well-formed."""
        bridge = PX4ReflexBridge(PX4BridgeConfig())
        d = ReflexDecision(action=ReflexAction.ROLL_RIGHT_90, confidence=0.9,
                           vector=ReflexVector(x=1.0), compute_latency_us=1, timestamp_us=1,
                           triggered=True, escape_mode=EscapeMode.SHORT)
        dec = MAVLinkV2Codec.decode(bridge.dispatch_evasion_override(d))
        self.assertEqual(dec.msgid, 82)
        self.assertNotEqual(dec.fields["body_roll_rate"], 0.0)
        payload = dec.payload
        self.assertEqual(len(payload), 39)
        self.assertEqual(payload[38], 0x80)
        bridge.close()

    def test_mavlink_v2_zero_truncation_handling(self):
        """Verifies MAVLink 2 legal zero-byte truncation is safely padded on reception."""
        # Create a HEARTBEAT message whose payload has trailing zero bytes truncated
        full_payload = MAVLinkV2Codec.pack_heartbeat(autopilot=0, vehicle_type=0)
        # Strip trailing zeros (legal in MAVLink 2)
        truncated_payload = full_payload.rstrip(b"\x00")
        msg = MAVLinkMessage(msgid=0, sysid=1, compid=1, seq=5, payload=truncated_payload)
        packet = MAVLinkV2Codec.encode(msg)

        decoded = MAVLinkV2Codec.decode(packet)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.msgid, 0)
        self.assertEqual(decoded.fields.get("autopilot"), 0)
        self.assertEqual(decoded.fields.get("type"), 0)

    def test_imu_missing_fields_safe(self):
        """Verifies malformed IMU messages do not crash the bridge."""
        bridge = PX4ReflexBridge(PX4BridgeConfig())
        # Message with empty dictionary payload
        dummy_msg = MAVLinkMessage(msgid=105, sysid=1, compid=1, seq=1, payload=b"\x00" * 10, fields={})
        res = bridge.handle_mavlink_message(dummy_msg)
        self.assertIsNone(res)
        self.assertEqual(bridge.total_imu_ingested, 0)
        bridge.close()


if __name__ == "__main__":
    unittest.main()
