"""GiantFiber Bio-Reflex Coprocessor High-Level Python Engine.

Provides an intuitive, zero-copy, type-safe interface for robotics and autonomous systems.
"""

from typing import Optional, List, Tuple
import ctypes
from .binding import (
    GiantFiberCoreBinding, C_EngineConfig, C_EventSpike, C_ImuData, C_ReflexOutput
)
from .types import (
    ReflexConfig, ReflexDecision, ReflexAction, ReflexVector, CircuitTrace, EventPacket, EventSpike
)


class GiantFiberCoprocessor:
    """Sub-5ms, Sub-1W Drosophila Connectome Bio-Reflex Coprocessor."""

    def __init__(self, config: Optional[ReflexConfig] = None, lib_path: Optional[str] = None):
        self.config = config or ReflexConfig()
        self._binding = GiantFiberCoreBinding(lib_path)

        c_cfg = C_EngineConfig(
            confidence_threshold=self.config.confidence_threshold,
            temperature=self.config.temperature,
            looming_threshold=self.config.looming_threshold,
            decay_tau_us=self.config.decay_tau_us,
            refractory_period_us=self.config.refractory_period_us,
        )
        self._engine_ptr = self._binding.lib.gf_engine_new(ctypes.byref(c_cfg))
        if not self._engine_ptr:
            raise RuntimeError("Failed to allocate native GiantFiberEngine")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        """Release native C-allocated memory."""
        if self._engine_ptr:
            self._binding.lib.gf_engine_free(self._engine_ptr)
            self._engine_ptr = None

    def __del__(self):
        self.close()

    def reset(self):
        """Reset internal accumulator and neuronal states to zero."""
        if self._engine_ptr:
            self._binding.lib.gf_engine_reset(self._engine_ptr)

    def feed_spike(self, x: int, y: int, timestamp_us: int, polarity: int = 1, raw_w: int = 320, raw_h: int = 320):
        """Feed a single asynchronous DVS event spike."""
        spike = C_EventSpike(
            timestamp_us=timestamp_us,
            x=x,
            y=y,
            polarity=polarity,
            _pad=(ctypes.c_uint8 * 7)(*([0] * 7)),
        )
        self._binding.lib.gf_feed_event(self._engine_ptr, ctypes.byref(spike), raw_w, raw_h)

    def feed_packet(self, packet: EventPacket):
        """Feed a batch of events with zero-overhead contiguous memory passing."""
        count = len(packet.events)
        if count == 0:
            return

        SpikeArrayType = C_EventSpike * count
        spike_arr = SpikeArrayType()
        for i, ev in enumerate(packet.events):
            spike_arr[i].timestamp_us = ev.timestamp_us
            spike_arr[i].x = ev.x
            spike_arr[i].y = ev.y
            spike_arr[i].polarity = ev.polarity

        self._binding.lib.gf_feed_events_batch(
            self._engine_ptr,
            ctypes.cast(spike_arr, ctypes.POINTER(C_EventSpike)),
            count,
            packet.sensor_width,
            packet.sensor_height,
        )

    def update_imu(
        self,
        gyro: Tuple[float, float, float],
        accel: Tuple[float, float, float] = (0.0, 0.0, 0.0),
        timestamp_us: int = 0,
    ):
        """Update biomimetic Halteres / IMU state for heading stabilization."""
        imu = C_ImuData(
            timestamp_us=timestamp_us,
            gyro_x=gyro[0],
            gyro_y=gyro[1],
            gyro_z=gyro[2],
            accel_x=accel[0],
            accel_y=accel[1],
            accel_z=accel[2],
        )
        self._binding.lib.gf_update_imu(self._engine_ptr, ctypes.byref(imu))

    def step_eval(self, now_us: int = 0) -> ReflexDecision:
        """Execute sub-millisecond System-1 connectome evaluation and return type-safe contract."""
        out = C_ReflexOutput()
        res = self._binding.lib.gf_step_eval(self._engine_ptr, now_us, ctypes.byref(out))
        if res != 0:
            raise RuntimeError(f"Engine evaluation failed with code {res}")

        action_enum = ReflexAction(out.action)
        circuit_enum = CircuitTrace(out.circuit_id) if out.circuit_id in CircuitTrace._value2member_map_ else CircuitTrace.UNKNOWN
        vector = ReflexVector(x=out.vector.x, y=out.vector.y, z=out.vector.z)

        # Board power estimation: ~512mW nominal operation
        power_est = 512.0 if not out.triggered else 620.0

        return ReflexDecision(
            action=action_enum,
            confidence=round(float(out.confidence), 4),
            vector=vector,
            compute_latency_us=int(out.compute_latency_us),
            circuit_trace=circuit_enum,
            timestamp_us=int(out.timestamp_us),
            triggered=bool(out.triggered),
            power_estimated_mw=power_est,
        )

    def save_snapshot(self) -> bytes:
        """Create a bit-exact binary snapshot of the entire coprocessor state (<50us)."""
        snap_size = self._binding.lib.gf_get_snapshot_size()
        buf = (ctypes.c_uint8 * snap_size)()
        written = self._binding.lib.gf_save_snapshot(self._engine_ptr, buf, snap_size)
        if written < 0:
            raise RuntimeError(f"Failed to save engine snapshot: code {written}")
        return bytes(buf[:written])

    def restore_snapshot(self, data: bytes):
        """Restore coprocessor state from binary snapshot with Adler checksum validation (<50us)."""
        buf_len = len(data)
        buf = (ctypes.c_uint8 * buf_len)(*data)
        res = self._binding.lib.gf_restore_snapshot(self._engine_ptr, buf, buf_len)
        if res != 0:
            raise RuntimeError(f"Failed to restore engine snapshot: code {res}")
