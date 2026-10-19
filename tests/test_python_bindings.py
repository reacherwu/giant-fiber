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
    EscapeMode,
)

from _stimuli import run_loom


class TestPydanticContracts(unittest.TestCase):
    """Verify strict type contracts and schema enforcement."""

    def test_reflex_decision_valid(self):
        decision = ReflexDecision(
            action=ReflexAction.ROLL_LEFT_90,
            confidence=0.9642,
            vector=ReflexVector(x=-1.0, y=0.2, z=1.0),
            compute_latency_us=480,
            circuit_trace=CircuitTrace.GIANT_FIBER,
            timestamp_us=120000,
            triggered=True,
            escape_mode=EscapeMode.SHORT,
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
        self.config = ReflexConfig()
        self.coprocessor = GiantFiberCoprocessor(self.config)

    def tearDown(self):
        self.coprocessor.close()

    def _loom(self, **kw):
        return run_loom(self.coprocessor.step_eval, self.coprocessor.feed_raw_spikes, **kw)

    def test_nominal_cruise(self):
        decision = self.coprocessor.step_eval(now_us=1000)
        self.assertEqual(decision.action, ReflexAction.CRUISE)
        self.assertFalse(decision.triggered)
        self.assertEqual(decision.escape_mode, EscapeMode.NONE)

    def test_fast_loom_triggers_short_mode_escape_away_from_object(self):
        # object approaching slightly left of centre, r/v = 10 ms (fast)
        decisions, first = self._loom(rv_ms=10.0, offset_x=-0.1)
        self.assertIsNotNone(first, "fast loom must trigger")
        self.assertTrue(any(d.escape_mode == EscapeMode.SHORT for d in decisions),
                        "fast loom must reach the Giant Fiber (short) mode")
        self.assertGreaterEqual(first.confidence, self.config.confidence_threshold)
        self.assertEqual(first.action, ReflexAction.ROLL_RIGHT_90)
        st = self.coprocessor.debug_state()
        self.assertGreater(st["last_gf_spike_us"], 0)

    def test_slow_loom_uses_long_mode(self):
        decisions, first = self._loom(rv_ms=80.0, z0=6.0, max_s=4.0)
        self.assertIsNotNone(first, "slow loom must trigger")
        self.assertEqual(first.escape_mode, EscapeMode.LONG)
        self.assertEqual(first.circuit_trace, CircuitTrace.NON_GF_DESCENDING)

    def test_no_trigger_on_noise_only(self):
        from giantfiber.sim.events import EventCamera
        cam = EventCamera(noise_rate_hz=1.0, seed=3)
        for k in range(1000):
            ev = cam.step(None, 0.15, k * 1000 + 1, 1000)
            if len(ev):
                self.coprocessor.feed_raw_spikes(ev.tobytes())
            d = self.coprocessor.step_eval(k * 1000 + 1000)
            self.assertFalse(d.triggered, f"noise triggered at step {k}")

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
            self.assertEqual(fresh.save_snapshot(), snapshot_bytes)
            # Eval restored state
            dec = fresh.step_eval(now_us=99000)
            self.assertEqual(dec.timestamp_us, 99000)

    def test_imu_non_finite_input_handling(self):
        """Verifies that non-finite floats (inf, -inf, nan) in IMU do not hang or crash."""
        self.coprocessor.update_imu(gyro=(0.0, 0.0, float("inf")), timestamp_us=1000)
        self.coprocessor.update_imu(gyro=(0.0, 0.0, float("nan")), timestamp_us=2000)
        self.coprocessor.update_imu(gyro=(0.0, 0.0, float("-inf")), timestamp_us=3000)
        dec = self.coprocessor.step_eval(now_us=4000)
        self.assertIsNotNone(dec)
        self.assertTrue(math.isfinite(dec.vector.z))

    def test_reset_clears_compass_torque(self):
        """Verifies that engine reset clears residual compass correction torque."""
        self.coprocessor.update_imu(gyro=(0.0, 0.0, 2.0), timestamp_us=1000)
        self.coprocessor.update_imu(gyro=(0.0, 0.0, 2.0), timestamp_us=200000)
        dec_before = self.coprocessor.step_eval(now_us=200000)
        self.assertLess(dec_before.vector.z, -0.01)

        self.coprocessor.reset()
        dec_after = self.coprocessor.step_eval(now_us=200001)
        self.assertAlmostEqual(dec_after.vector.z, 0.0, places=4)


if __name__ == "__main__":
    unittest.main()
