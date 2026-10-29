"""Python wrapper for the far-field time-to-contact stage (crates/giantfiber-core/src/farfield.rs)."""
from __future__ import annotations

import ctypes
from ctypes import (
    POINTER,
    Structure,
    c_float,
    c_size_t,
    c_uint16,
    c_uint32,
    c_uint64,
    c_void_p,
)
from dataclasses import asdict, dataclass

import numpy as np

from .binding import _find_library


class C_FarFieldConfig(Structure):
    _fields_ = [("width", c_uint16), ("height", c_uint16), ("tau_arm_s", c_float), ("max_miss_radii", c_float),
                ("min_t_stat", c_float), ("fit_window_us", c_uint32), ("min_fit_span_us", c_uint32),
                ("confirm_evals", c_uint32), ("min_radius_px", c_float), ("tau_min_s", c_float)]


class C_FarFieldOutput(Structure):
    _fields_ = [("tracking", c_uint32), ("armed", c_uint32), ("tau_s", c_float), ("r_px", c_float),
                ("r_dot_pps", c_float), ("t_stat", c_float), ("cx", c_float), ("cy", c_float),
                ("miss_radii", c_float), ("miss_dir_x", c_float), ("miss_dir_y", c_float), ("fit_events", c_uint32)]


@dataclass
class FarFieldConfig:
    width: int = 1280
    height: int = 720
    tau_arm_s: float = 1.0
    max_miss_radii: float = 6.0
    min_t_stat: float = 8.0
    fit_window_us: int = 250_000
    min_fit_span_us: int = 150_000
    confirm_evals: int = 15
    min_radius_px: float = 2.0
    tau_min_s: float = 0.15


_LIB = None


def _lib():
    global _LIB
    if _LIB is None:
        lib = ctypes.CDLL(_find_library())
        lib.gf_ff_new.argtypes = [POINTER(C_FarFieldConfig)]
        lib.gf_ff_new.restype = c_void_p
        lib.gf_ff_free.argtypes = [c_void_p]
        lib.gf_ff_feed.argtypes = [c_void_p, c_void_p, c_size_t]
        lib.gf_ff_eval.argtypes = [c_void_p, c_uint64, POINTER(C_FarFieldOutput)]
        lib.gf_ff_reset.argtypes = [c_void_p]
        _LIB = lib
    return _LIB


class Derotator:
    """Maps sensor events into a rotation-stabilised image (the camera frame at a reference attitude).

    `R_cb`: camera->body rotation (forward camera: image x = body y, image y = body z, optical axis = body x).
    Each call to `apply` uses the current body->world attitude (e.g. PX4 ATTITUDE / gyro integration).
    """
    R_CB = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])  # columns: cam x,y,z in body

    def __init__(self, width, height, fov_deg, R_cb=None):
        import math
        if R_cb is not None:
            self.R_CB = np.asarray(R_cb)
        self.w, self.h = width, height
        self.f = (width / 2) / math.tan(math.radians(fov_deg) / 2)
        self.cx, self.cy = width / 2, height / 2
        self.R_ref = None

    def set_reference(self, R_wb):
        """Reference: level camera with the current heading."""
        import math
        yaw = math.atan2(R_wb[1, 0], R_wb[0, 0])
        c, s = math.cos(yaw), math.sin(yaw)
        self.R_ref = np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])

    def apply(self, ev, R_wb):
        if len(ev) == 0:
            return ev
        if self.R_ref is None:
            self.set_reference(R_wb)
        # ray in camera -> body -> world -> reference body -> reference camera
        M = self.R_CB.T @ self.R_ref.T @ R_wb @ self.R_CB
        x = (ev["x"].astype(np.float64) + 0.5 - self.cx) / self.f
        y = (ev["y"].astype(np.float64) + 0.5 - self.cy) / self.f
        rays = np.stack([x, y, np.ones_like(x)])
        r = M @ rays
        ok = r[2] > 1e-6
        u = self.cx + self.f * r[0] / np.where(ok, r[2], 1) - 0.5
        v = self.cy + self.f * r[1] / np.where(ok, r[2], 1) - 0.5
        ui, vi = np.rint(u), np.rint(v)
        ok &= (ui >= 0) & (ui < self.w) & (vi >= 0) & (vi < self.h)
        out = ev[ok].copy()
        out["x"] = ui[ok].astype(np.uint16)
        out["y"] = vi[ok].astype(np.uint16)
        return out


class FarFieldTTC:
    def __init__(self, config: FarFieldConfig | None = None):
        self.config = config or FarFieldConfig()
        self._c = C_FarFieldConfig(**asdict(self.config))
        self._h = _lib().gf_ff_new(ctypes.byref(self._c))
        self._out = C_FarFieldOutput()

    def feed(self, events: np.ndarray):
        """events: numpy array with giantfiber.sim.events.EVENT_DTYPE (sensor coordinates)."""
        if len(events) == 0:
            return
        if not (hasattr(events, "dtype") and events.dtype.names
                and {"x", "y", "timestamp_us"} <= set(events.dtype.names)):
            raise TypeError("events must be a structured array with x, y, timestamp_us fields")
        ev = np.ascontiguousarray(events)
        _lib().gf_ff_feed(self._h, ev.ctypes.data, len(ev))

    def eval(self, now_us: int) -> dict:
        _lib().gf_ff_eval(self._h, now_us, ctypes.byref(self._out))
        return {k: getattr(self._out, k) for k, *_ in C_FarFieldOutput._fields_}

    def reset(self):
        _lib().gf_ff_reset(self._h)

    def __del__(self):
        if getattr(self, "_h", None):
            _lib().gf_ff_free(self._h)
            self._h = None
