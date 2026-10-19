#!/usr/bin/env python3
"""Offline check: at what distance does the engine trigger for a 0.3 m sphere, per preset?

Head-on approach from 25 m at 6/10/14 m/s (2 noise seeds each, 0.3 Hz/px), plus fly-bys at
2.5-3 m lateral offset (any trigger there is a false trigger). Prints distance / time-to-contact /
mode for each run.
  python3 tools/validation/detection_range.py
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from giantfiber import GiantFiberCoprocessor, ReflexConfig  # noqa: E402
from giantfiber.sim.events import EventCamera  # noqa: E402

R = 0.15
PRESETS = {
    "drone (SITL preset)": dict(fov_deg=60.0, lplc2_half_size_deg=4.0, lc4_half_velocity_dps=20.0),
    "lower thresholds": dict(fov_deg=60.0, lplc2_half_size_deg=1.5, lc4_half_velocity_dps=5.0, min_edge_cells=4),
    "30 deg FOV": dict(fov_deg=30.0, lplc2_half_size_deg=2.0, lc4_half_velocity_dps=10.0),
}


def run(cfg, v, d0=25.0, lat=0.0, seed=0, dt=2000):
    g = GiantFiberCoprocessor(ReflexConfig(**cfg))
    cam = EventCamera(fov_deg=cfg["fov_deg"], noise_rate_hz=0.3, seed=seed)
    try:
        for k in range(int((d0 / v + 0.5) * 1e6 / dt)):
            Z = d0 - v * k * dt * 1e-6
            if Z < 0.3:
                return None
            ev = cam.step((lat, 0.0, Z), R, k * dt + 1, dt)
            if len(ev):
                g.feed_raw_spikes(ev.tobytes())
            d = g.step_eval(k * dt + dt)
            if d.triggered:
                return round(Z, 2), round((Z - 0.4) / v * 1e3), int(d.escape_mode)
    finally:
        g.close()
    return None


for name, c in PRESETS.items():
    hits = [(v, run(c, v, seed=s)) for v in (6, 10, 14) for s in (0, 1)]
    fb = [run(c, v, lat=lat, seed=s) for v, lat in ((10, 3.0), (14, 2.5)) for s in (0, 1, 2)]
    print(f"{name}: head-on (speed, (trigger dist m, TTC ms, mode)) = {hits}; fly-by triggers = {sum(x is not None for x in fb)}/{len(fb)}")
