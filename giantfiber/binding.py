"""Low-overhead C-ABI ctypes bindings for libgiantfiber_core.

Thin ctypes wrapper over the Rust C ABI (no NumPy/PyTorch dependency).
"""

import ctypes
import os
import sys
from ctypes import (
    c_uint8, c_int8, c_uint16, c_uint32, c_uint64, c_float, c_void_p, c_size_t, c_int32, Structure, POINTER
)
from pathlib import Path


class C_Vector3D(Structure):
    _fields_ = [
        ("x", c_float),
        ("y", c_float),
        ("z", c_float),
    ]


class C_EventSpike(Structure):
    _fields_ = [
        ("timestamp_us", c_uint64),
        ("x", c_uint16),
        ("y", c_uint16),
        ("polarity", c_int8),
        ("_pad", c_uint8 * 7),
    ]


class C_ImuData(Structure):
    _fields_ = [
        ("timestamp_us", c_uint64),
        ("gyro_x", c_float),
        ("gyro_y", c_float),
        ("gyro_z", c_float),
        ("accel_x", c_float),
        ("accel_y", c_float),
        ("accel_z", c_float),
    ]


class C_ReflexOutput(Structure):
    _fields_ = [
        ("action", c_uint32),
        ("confidence", c_float),
        ("vector", C_Vector3D),
        ("compute_latency_us", c_uint32),
        ("circuit_id", c_uint32),
        ("timestamp_us", c_uint64),
        ("triggered", c_uint8),
        ("escape_mode", c_uint8),
        ("_pad", c_uint8 * 6),
    ]


class C_EngineConfig(Structure):
    _fields_ = [
        ("confidence_threshold", c_float),
        ("temperature", c_float),
        ("looming_threshold", c_float),
        ("decay_tau_us", c_float),
        ("refractory_period_us", c_uint64),
        ("fov_deg", c_float),
        ("gf_tau_us", c_float),
        ("nongf_threshold", c_float),
        ("nongf_tau_us", c_float),
        ("lplc2_half_size_deg", c_float),
        ("lc4_half_velocity_dps", c_float),
        ("long_mode_window_us", c_float),
        ("min_edge_cells", c_uint32),
    ]


class C_GfDebugState(Structure):
    _fields_ = [
        ("theta_deg", c_float),
        ("theta_dot_dps", c_float),
        ("centroid_x", c_float),
        ("centroid_y", c_float),
        ("left_fraction", c_float),
        ("radial_cv", c_float),
        ("lplc2", c_float * 2),
        ("lc4", c_float * 2),
        ("v_gf", c_float * 2),
        ("v_nongf", c_float * 2),
        ("edge_cells", c_uint32),
        ("valid", c_uint32),
        ("escape_mode", c_uint32),
        ("_pad", c_uint32),
        ("last_gf_spike_us", c_uint64),
    ]


def _find_library() -> str:
    """Locate libgiantfiber_core compiled dynamic library."""
    env_path = os.environ.get("GIANTFIBER_LIB")
    if env_path:
        return env_path
    pkg_dir = Path(__file__).parent.resolve()
    repo_root = pkg_dir.parent.resolve()

    candidates = [
        repo_root / "target" / "release" / "libgiantfiber_core.dylib",
        repo_root / "target" / "release" / "libgiantfiber_core.so",
        repo_root / "target" / "debug" / "libgiantfiber_core.dylib",
        repo_root / "target" / "debug" / "libgiantfiber_core.so",
        pkg_dir / "libgiantfiber_core.dylib",
        pkg_dir / "libgiantfiber_core.so",
    ]

    for cand in candidates:
        if cand.exists():
            return str(cand)

    raise FileNotFoundError(
        f"Could not find compiled libgiantfiber_core library. Run `cargo build --release` first. Checked: {candidates}"
    )


class GiantFiberCoreBinding:
    """Direct FFI wrapper around libgiantfiber_core."""

    def __init__(self, lib_path: str = None):
        path = lib_path or _find_library()
        self.lib = ctypes.CDLL(path)

        # gf_engine_new(config: *const EngineConfig) -> *mut GiantFiberEngine
        self.lib.gf_engine_new.argtypes = [POINTER(C_EngineConfig)]
        self.lib.gf_engine_new.restype = c_void_p

        # gf_engine_free(engine: *mut GiantFiberEngine)
        self.lib.gf_engine_free.argtypes = [c_void_p]
        self.lib.gf_engine_free.restype = None

        # gf_engine_reset(engine: *mut GiantFiberEngine)
        self.lib.gf_engine_reset.argtypes = [c_void_p]
        self.lib.gf_engine_reset.restype = None

        # gf_feed_event(engine, spike, raw_w, raw_h)
        self.lib.gf_feed_event.argtypes = [c_void_p, POINTER(C_EventSpike), c_uint16, c_uint16]
        self.lib.gf_feed_event.restype = None

        # gf_feed_events_batch(engine, spikes, count, raw_w, raw_h)
        self.lib.gf_feed_events_batch.argtypes = [c_void_p, POINTER(C_EventSpike), c_size_t, c_uint16, c_uint16]
        self.lib.gf_feed_events_batch.restype = None

        # gf_update_imu(engine, imu)
        self.lib.gf_update_imu.argtypes = [c_void_p, POINTER(C_ImuData)]
        self.lib.gf_update_imu.restype = None

        # gf_step_eval(engine, now_us, out_result) -> i32
        self.lib.gf_step_eval.argtypes = [c_void_p, c_uint64, POINTER(C_ReflexOutput)]
        self.lib.gf_step_eval.restype = c_int32

        # gf_get_snapshot_size() -> usize
        self.lib.gf_get_snapshot_size.argtypes = []
        self.lib.gf_get_snapshot_size.restype = c_size_t

        # gf_save_snapshot(engine, buffer, buf_len) -> i32
        self.lib.gf_save_snapshot.argtypes = [c_void_p, POINTER(c_uint8), c_size_t]
        self.lib.gf_save_snapshot.restype = c_int32

        # gf_restore_snapshot(engine, buffer, buf_len) -> i32
        self.lib.gf_restore_snapshot.argtypes = [c_void_p, POINTER(c_uint8), c_size_t]
        self.lib.gf_restore_snapshot.restype = c_int32

        self.lib.gf_get_debug_state.argtypes = [c_void_p, POINTER(C_GfDebugState)]
        self.lib.gf_get_debug_state.restype = c_int32

        # gf_canfd_encode(output, sequence, timestamp_ms, out[24]) -> i32
        self.lib.gf_canfd_encode.argtypes = [POINTER(C_ReflexOutput), c_uint16, c_uint32, POINTER(c_uint8)]
        self.lib.gf_canfd_encode.restype = c_int32

    def canfd_encode(self, output: "C_ReflexOutput", sequence: int, timestamp_ms: int) -> bytes:
        """24-byte CAN-FD reflex frame built by the Rust (no_std) encoder."""
        buf = (c_uint8 * 24)()
        n = self.lib.gf_canfd_encode(ctypes.byref(output), sequence & 0xFFFF, timestamp_ms & 0xFFFFFFFF, buf)
        if n != 24:
            raise RuntimeError("gf_canfd_encode failed")
        return bytes(buf)
