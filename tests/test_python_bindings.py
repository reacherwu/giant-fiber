"""Unit and integration tests for GiantFiber Python SDK and C-ABI bindings."""

import unittest
import math
import pydantic
from giantfiber import (
    GiantFiberCoprocessor,
    ReflexConfig,
    ReflexAction,
    ReflexDecision,
    ReflexVector,
    CircuitTrace,
    EventPacket,
    EventSpike,
)


class TestPydanticContracts(unittest.TestCase):
    """Verify strict type contracts and schema enforcement."""

    def test_reflex_decision_valid(self):
        decision = ReflexDecision(
            action=ReflexAction.ROLL_LEFT_90,
            confidence=0.9642,
            vector=ReflexVector(x=-1.0, y=0.2, z=1.0),
            compute_latency_us=480,
            circuit_trace=CircuitTrace.GIANT_FIBER_L1,
            timestamp_us=120000,
            triggered=True,
            power_estimated_mw=620.0,
        )
        self.assertEqual(decision.action, ReflexAction.ROLL_LEFT_90)
        self.assertEqual(decision.confidence, 0.9642)
        self.assertTrue(decision.triggered)

        # JSON serialization
        json_str = decision.model_dump_json()
        self.assertEqual(decision.action.name, "ROLL_LEFT_90")
        self.assertIn('"action":2', json_str)
        self.assertIn("0.9642", json_str)

    def test_invalid_confidence_raises(self):
        with self.assertRaises(pydantic.ValidationError):
            ReflexDecision(
                action=ReflexAction.CRUISE,
                confidence=1.45,  # Out of [0.0, 1.0] bound
                compute_latency_us=200,
                timestamp_us=1000,
            )


class TestCoprocessorEngine(unittest.TestCase):
    """Verify real native Rust execution through C-ABI ctypes layer."""

    def setUp(self):
        self.config = ReflexConfig(
            confidence_threshold=0.80,
            temperature=1.0,
            looming_threshold=0.5,
            decay_tau_us=25000.0,
            refractory_period_us=30000,
        )
        self.coprocessor = GiantFiberCoprocessor(self.config)

    def tearDown(self):
        self.coprocessor.close()

    def test_nominal_cruise(self):
        decision = self.coprocessor.step_eval(now_us=1000)
        self.assertEqual(decision.action, ReflexAction.CRUISE)
        self.assertFalse(decision.triggered)
        self.assertLess(decision.compute_latency_us, 1500)

    def test_looming_evasion_trigger(self):
        # Simulate an approaching high-speed obstacle expanding on the left
        # Spikes moving outward radially
        now_us = 10000
        # Establish baseline before stimulus onset
        self.coprocessor.step_eval(now_us=now_us)

        for step in range(1, 15):
            t = now_us + step * 250
            radius = step * 4
            for deg in range(0, 360, 45):
                rad = math.radians(deg)
                cx = 70 + int(radius * math.cos(rad))
                cy = 160 + int(radius * math.sin(rad))
                self.coprocessor.feed_spike(x=cx, y=cy, timestamp_us=t, polarity=1)

        # Trigger evaluation
        decision = self.coprocessor.step_eval(now_us=now_us + 4000)

        # Should fire evasion reflex
        self.assertTrue(decision.triggered, "High-speed looming must trigger reflex")
        self.assertGreaterEqual(decision.confidence, 0.80)
        # Threat on left -> Evasion rolls RIGHT
        self.assertEqual(decision.action, ReflexAction.ROLL_RIGHT_90)
        self.assertEqual(decision.circuit_trace, CircuitTrace.GIANT_FIBER_L1)
        self.assertLess(decision.compute_latency_us, 1000, "Compute must be under 1ms")

    def test_imu_heading_stabilization(self):
        # Yaw rate 1.5 rad/s
        self.coprocessor.update_imu(gyro=(0.0, 0.0, 1.5), timestamp_us=1000)
        self.coprocessor.update_imu(gyro=(0.0, 0.0, 1.5), timestamp_us=500000)

        decision = self.coprocessor.step_eval(now_us=500000)
        # Compass correction torque in vector.z should oppose yaw
        self.assertLess(decision.vector.z, 0.0)

    def test_microsecond_snapshot_persistence(self):
        # Feed event
        self.coprocessor.feed_spike(x=100, y=100, timestamp_us=99000, polarity=1)
        self.coprocessor.step_eval(now_us=99000)

        snapshot_bytes = self.coprocessor.save_snapshot()
        self.assertTrue(len(snapshot_bytes) > 0)
        self.assertEqual(snapshot_bytes[:4], b"GF1\0")

        # Create new coprocessor and restore
        with GiantFiberCoprocessor(self.config) as fresh:
            fresh.restore_snapshot(snapshot_bytes)
            # Eval restored state
            dec = fresh.step_eval(now_us=99000)
            self.assertEqual(dec.timestamp_us, 99000)


if __name__ == "__main__":
    unittest.main()
