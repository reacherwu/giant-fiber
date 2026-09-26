"""
Integration tests for PX4-Autopilot MAVLink Bridge and SITL Evasion
"""

import math
import struct
import unittest
from giantfiber import ReflexAction, ReflexConfig
from giantfiber.integrations.px4_mavlink import (
    MAVLinkV2Codec,
    MAVLinkMessage,
    PX4ReflexBridge,
    PX4BridgeConfig,
    calculate_mavlink_crc,
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

    def test_evasion_override_dispatch(self):
        """Tests that a looming trigger dispatches an immediate SET_ATTITUDE_TARGET override."""
        config = PX4BridgeConfig(
            reflex_config=ReflexConfig(looming_threshold=0.5, confidence_threshold=0.5)
        )
        bridge = PX4ReflexBridge(config)
        bridge.connect()

        # Feed an accelerating, expanding looming stimulus (e.g. projectile heading towards eye center)
        t_base = 50000
        for step in range(12):
            t = t_base + step * 1000
            radius = int(2 + step * 2.5)
            cx, cy = 32, 32
            for angle_deg in range(0, 360, 20):
                rad = math.radians(angle_deg)
                px = int(cx + radius * math.cos(rad))
                py = int(cy + radius * math.sin(rad))
                if 0 <= px < 64 and 0 <= py < 64:
                    bridge.feed_visual_spike(px, py, t, polarity=1)

            decision = bridge.step_eval(t)
            if decision.triggered:
                break

        # Must have triggered a reflex override
        self.assertIsNotNone(bridge.last_decision)
        self.assertTrue(bridge.last_decision.triggered)
        self.assertIn(
            bridge.last_decision.action,
            [ReflexAction.ROLL_RIGHT_90, ReflexAction.ROLL_LEFT_90, ReflexAction.PITCH_UP],
        )
        self.assertGreater(bridge.total_overrides_dispatched, 0)

        # Verify the generated MAVLink packet format
        override_packet = bridge.dispatch_evasion_override(bridge.last_decision)
        decoded_override = MAVLinkV2Codec.decode(override_packet)
        self.assertIsNotNone(decoded_override)
        self.assertEqual(decoded_override.msgid, 82)  # SET_ATTITUDE_TARGET
        self.assertIn("body_roll_rate", decoded_override.fields)
        self.assertIn("thrust", decoded_override.fields)
        # Body roll rate should be non-zero for roll avoidance
        self.assertNotEqual(decoded_override.fields["body_roll_rate"], 0.0)
        self.assertGreater(decoded_override.fields["thrust"], 0.8)

        # Wire layout verification: target_sys (offset 36), target_comp (37), type_mask (38)
        payload = decoded_override.payload
        self.assertEqual(len(payload), 39)
        self.assertEqual(payload[36], 1)   # target_sys
        self.assertEqual(payload[37], 1)   # target_comp
        self.assertEqual(payload[38], 0x80) # ATTITUDE_TARGET_TYPEMASK_ATTITUDE_IGNORE (0x80)

        # Ensure body rate ignore bits (0, 1, 2) are NOT set!
        self.assertEqual(payload[38] & 0b111, 0)

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
