"""
Unit tests for CAN-FD Frame serialization and codec
"""

import unittest
from giantfiber import ReflexAction, ReflexDecision, ReflexVector
from giantfiber.integrations.can_fd import (
    CANFDReflexFrame,
    CANFDCodec,
    crc16_ccitt,
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


if __name__ == "__main__":
    unittest.main()
