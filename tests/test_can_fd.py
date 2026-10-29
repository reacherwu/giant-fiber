"""
Unit tests for CAN-FD Frame serialization and codec
"""

import unittest

from giantfiber import ReflexAction, ReflexDecision, ReflexVector
from giantfiber.integrations.can_fd import (
    CANFDCodec,
    CANFDReflexFrame,
)


class TestCANFDIntegration(unittest.TestCase):
    def test_can_fd_roundtrip_encoding(self):
        frame = CANFDReflexFrame(
            action=ReflexAction.ROLL_RIGHT_90,
            confidence=0.965,
            roll_rate_deg_s=300.0,
            pitch_rate_deg_s=-15.0,
            yaw_rate_deg_s=0.0,
            thrust=0.95,
            compute_latency_us=12,
            timestamp_ms=1234567,
            sequence=42,
        )

        payload = CANFDCodec.encode(frame)
        self.assertEqual(len(payload), 24)
        self.assertEqual(payload[:2], b"GF")

        decoded = CANFDCodec.decode(payload)
        self.assertIsNotNone(decoded)
        self.assertEqual(decoded.action, ReflexAction.ROLL_RIGHT_90)
        self.assertAlmostEqual(decoded.confidence, 0.965, places=2)
        self.assertAlmostEqual(decoded.roll_rate_deg_s, 300.0, places=1)
        self.assertAlmostEqual(decoded.pitch_rate_deg_s, -15.0, places=1)
        self.assertAlmostEqual(decoded.thrust, 0.95, places=2)
        self.assertEqual(decoded.compute_latency_us, 12)
        self.assertEqual(decoded.sequence, 42)

    def test_corrupted_crc_rejected(self):
        frame = CANFDReflexFrame(action=ReflexAction.BRAKE, confidence=0.88)
        payload = bytearray(CANFDCodec.encode(frame))

        # Corrupt one data byte
        payload[6] ^= 0xFF

        # Decoder must safely reject corrupted CAN-FD frame
        decoded = CANFDCodec.decode(bytes(payload))
        self.assertIsNone(decoded)

    def test_from_decision_conversion(self):
        decision = ReflexDecision(
            action=ReflexAction.ROLL_RIGHT_90,
            confidence=0.99,
            vector=ReflexVector(x=1.0, y=0.0, z=0.0),
            compute_latency_us=2,
            timestamp_us=50000,
            triggered=True,
        )

        frame = CANFDCodec.from_decision(decision, seq=1)
        self.assertEqual(frame.action, ReflexAction.ROLL_RIGHT_90)
        self.assertEqual(frame.roll_rate_deg_s, 300.0)
        self.assertEqual(frame.thrust, 0.95)


    def test_escape_mode_field(self):
        frame = CANFDReflexFrame(action=ReflexAction.ROLL_LEFT_90, confidence=0.9, escape_mode=2)
        self.assertEqual(CANFDCodec.decode(CANFDCodec.encode(frame)).escape_mode, 2)

    def test_rust_and_python_encoders_are_byte_identical(self):
        """The no_std Rust encoder (via the C ABI) and the Python codec produce the same bytes."""
        from giantfiber.binding import C_ReflexOutput, GiantFiberCoreBinding
        from giantfiber.types import EscapeMode
        b = GiantFiberCoreBinding()
        cases = [
            (ReflexAction.ROLL_RIGHT_90, 0.95, 7, EscapeMode.SHORT),
            (ReflexAction.ROLL_LEFT_90, 0.8731, 13, EscapeMode.LONG),
            (ReflexAction.PITCH_UP, 1.0, 65535, EscapeMode.SHORT),
            (ReflexAction.BRAKE, 0.5, 0, EscapeMode.NONE),
            (ReflexAction.CRUISE, 0.0, 99999, EscapeMode.NONE),
        ]
        for i, (action, conf, lat, mode) in enumerate(cases):
            out = C_ReflexOutput()
            out.action = int(action)
            out.confidence = conf
            out.compute_latency_us = lat
            out.escape_mode = int(mode)
            rust = b.canfd_encode(out, sequence=1000 + i, timestamp_ms=123456789 + i)
            dec = ReflexDecision(action=action, confidence=conf, compute_latency_us=lat, timestamp_us=1,
                                 escape_mode=mode)
            py = CANFDCodec.encode(CANFDCodec.from_decision(dec, seq=1000 + i, timestamp_ms=123456789 + i))
            self.assertEqual(rust, py, f"case {i}: {rust.hex()} != {py.hex()}")

    def test_socketcan_frame_packing(self):
        from giantfiber.integrations.can_fd import (
            CANFD_MTU,
            pack_canfd_frame,
            unpack_canfd_frame,
        )
        payload = CANFDCodec.encode(CANFDReflexFrame(action=ReflexAction.BRAKE, confidence=0.5))
        raw = pack_canfd_frame(0x18F02001, payload)
        self.assertEqual(len(raw), CANFD_MTU)
        self.assertEqual(raw[4], 24)  # len
        cid, data = unpack_canfd_frame(raw)
        self.assertEqual(cid, 0x18F02001)
        self.assertEqual(data, payload)

    def test_socketcan_vcan_loopback(self):
        """Runs only if a CAN-FD capable `vcan0` exists (e.g. in CI with the vcan module)."""
        import os
        import socket
        if not hasattr(socket, "AF_CAN") or not os.path.exists("/sys/class/net/vcan0"):
            self.skipTest("vcan0 not available")
        from giantfiber.integrations.can_fd import SocketCANFDBus
        with SocketCANFDBus("vcan0") as rx, SocketCANFDBus("vcan0") as tx:
            frame = CANFDReflexFrame(action=ReflexAction.ROLL_RIGHT_90, confidence=0.9, sequence=5, escape_mode=2)
            tx.send_frame(frame)
            got = rx.recv_frame(timeout_s=1.0)
        self.assertIsNotNone(got)
        self.assertEqual(got.sequence, 5)
        self.assertEqual(got.escape_mode, 2)


if __name__ == "__main__":
    unittest.main()
