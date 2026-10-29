"""GiantFiber Bio-Reflex Coprocessor High-Level Python Engine.

Provides an intuitive, zero-copy, type-safe interface for robotics and autonomous systems.
"""
from __future__ import annotations

import ctypes

from .binding import (
    C_EngineConfig,
    C_EventSpike,
    C_GfDebugState,
    C_ImuData,
    C_ReflexOutput,
    GiantFiberCoreBinding,
)
from .types import (
    CircuitTrace,
    EscapeMode,
    EventPacket,
    ReflexAction,
    ReflexConfig,
    ReflexDecision,
    ReflexVector,
)


class GiantFiberCoprocessor:
    """Python handle to the Rust looming-escape engine."""

    def __init__(self, config: ReflexConfig | None = None, lib_path: str | None = None):
        self.config = config or ReflexConfig()
        self._binding = GiantFiberCoreBinding(lib_path)

        c_cfg = C_EngineConfig(**{name: getattr(self.config, name) for name, *_ in C_EngineConfig._fields_})
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
        import contextlib
        with contextlib.suppress(Exception):
            self.close()

    def reset(self):
        """Reset internal accumulator and neuronal states to zero."""
        if self._engine_ptr:
            self._binding.lib.gf_engine_reset(self._engine_ptr)

    def feed_spike(self, x: int, y: int, timestamp_us: int, polarity: int = 1, raw_w: int = 320, raw_h: int = 320):
        """Feed a single asynchronous DVS event spike."""
        pol = -1 if polarity <= 0 else 1
        spike = C_EventSpike(
            timestamp_us=timestamp_us,
            x=x,
            y=y,
            polarity=pol,
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
            spike_arr[i].polarity = -1 if ev.polarity <= 0 else 1

        self._binding.lib.gf_feed_events_batch(
            self._engine_ptr,
            ctypes.cast(spike_arr, ctypes.POINTER(C_EventSpike)),
            count,
            packet.sensor_width,
            packet.sensor_height,
        )

    def feed_raw_spikes(self, buffer: bytes, raw_w: int = 320, raw_h: int = 320):
        """Zero-overhead ingestion of raw binary spike buffer from DVS camera driver.

        Directly maps bytes to C_EventSpike without allocating intermediate Python objects.
        """
        spike_size = ctypes.sizeof(C_EventSpike)
        count = len(buffer) // spike_size
        if count == 0:
            return
        if isinstance(buffer, (bytes, bytearray, memoryview)):
            if isinstance(buffer, bytes):
                # Zero-copy: the bytes object stays alive for the duration of the call.
                ptr = ctypes.cast(ctypes.c_char_p(buffer), ctypes.POINTER(C_EventSpike))
                self._binding.lib.gf_feed_events_batch(self._engine_ptr, ptr, count, raw_w, raw_h)
                return
            try:
                raw_char_arr = (ctypes.c_char * len(buffer)).from_buffer(buffer)
            except (TypeError, ValueError):
                raw_char_arr = (ctypes.c_char * len(buffer)).from_buffer_copy(buffer)
            ptr = ctypes.cast(raw_char_arr, ctypes.POINTER(C_EventSpike))
            self._binding.lib.gf_feed_events_batch(self._engine_ptr, ptr, count, raw_w, raw_h)
            return
        raw_char_arr = (ctypes.c_char * len(buffer)).from_buffer_copy(buffer)
        ptr = ctypes.cast(raw_char_arr, ctypes.POINTER(C_EventSpike))
        self._binding.lib.gf_feed_events_batch(self._engine_ptr, ptr, count, raw_w, raw_h)

    def update_imu(
        self,
        gyro: tuple[float, float, float],
        accel: tuple[float, float, float] = (0.0, 0.0, 0.0),
        timestamp_us: int = 0,
    ):
        """Feed an IMU sample (yaw rate drives the heading integrator)."""
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
        """Run one evaluation step and return the discrete decision."""
        out = C_ReflexOutput()
        res = self._binding.lib.gf_step_eval(self._engine_ptr, now_us, ctypes.byref(out))
        if res != 0:
            raise RuntimeError(f"Engine evaluation failed with code {res}")

        action_enum = ReflexAction(out.action)
        circuit_enum = CircuitTrace(out.circuit_id) if out.circuit_id in CircuitTrace._value2member_map_ else CircuitTrace.UNKNOWN
        vector = ReflexVector(x=out.vector.x, y=out.vector.y, z=out.vector.z)

        return ReflexDecision(
            action=action_enum,
            confidence=round(float(out.confidence), 4),
            vector=vector,
            compute_latency_us=int(out.compute_latency_us),
            circuit_trace=circuit_enum,
            timestamp_us=int(out.timestamp_us),
            triggered=bool(out.triggered),
            escape_mode=EscapeMode(out.escape_mode),
        )

    def save_snapshot(self) -> bytes:
        """Serialize the engine state (scalar state; the time surface is not included)."""
        snap_size = self._binding.lib.gf_get_snapshot_size()
        buf = (ctypes.c_uint8 * snap_size)()
        written = self._binding.lib.gf_save_snapshot(self._engine_ptr, buf, snap_size)
        if written < 0:
            raise RuntimeError(f"Failed to save engine snapshot: code {written}")
        return bytes(buf[:written])

    def restore_snapshot(self, data: bytes):
        """Restore engine state from a snapshot (Adler-32 checked)."""
        buf_len = len(data)
        buf = (ctypes.c_uint8 * buf_len)(*data)
        res = self._binding.lib.gf_restore_snapshot(self._engine_ptr, buf, buf_len)
        if res != 0:
            raise RuntimeError(f"Failed to restore engine snapshot: code {res}")

        c_cfg = C_EngineConfig()
        if self._binding.lib.gf_get_config(self._engine_ptr, ctypes.byref(c_cfg)) == 0:
            self.config = ReflexConfig(**{name: getattr(c_cfg, name) for name, *_ in C_EngineConfig._fields_})
    def debug_state(self) -> dict:
        """Internal looming estimate, VPN activity and membrane states (for analysis/plots)."""
        st = C_GfDebugState()
        self._binding.lib.gf_get_debug_state(self._engine_ptr, ctypes.byref(st))
        return {
            "theta_deg": st.theta_deg, "theta_dot_dps": st.theta_dot_dps,
            "centroid_x": st.centroid_x, "centroid_y": st.centroid_y,
            "left_fraction": st.left_fraction, "radial_cv": st.radial_cv,
            "lplc2": tuple(st.lplc2), "lc4": tuple(st.lc4),
            "v_gf": tuple(st.v_gf), "v_nongf": tuple(st.v_nongf),
            "edge_cells": st.edge_cells, "valid": bool(st.valid),
            "escape_mode": st.escape_mode, "last_gf_spike_us": st.last_gf_spike_us,
        }
