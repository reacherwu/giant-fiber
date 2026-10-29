#!/usr/bin/env python3
"""Minimal example: feed a synthetic looming stimulus to the engine and print what it does.

A 0.15 m sphere approaches a 320x320, 90 deg event camera head-on at a constant speed.
Events are occupancy changes of the projected disk (see giantfiber/sim/events.py); this is a
geometric stand-in for an event camera, not a sensor model.

  python3 examples/looming_demo.py [--rv-ms 10] [--noise-hz 0.3]
"""
import argparse
import math
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from giantfiber import EscapeMode, GiantFiberCoprocessor, ReflexConfig
from giantfiber.sim.events import EventCamera


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rv-ms", type=float, default=10.0, help="radius/speed ratio in ms (smaller = faster)")
    ap.add_argument("--noise-hz", type=float, default=0.3, help="background events per pixel per second")
    a = ap.parse_args()
    R = 0.15
    v = R / (a.rv_ms * 1e-3)
    z0 = R / math.tan(math.radians(1.0))
    cam = EventCamera(noise_rate_hz=a.noise_hz)
    print(f"sphere r={R} m, speed {v:.1f} m/s, start {z0:.1f} m")
    print(f"{'t_ms':>7} {'TTC_ms':>7} {'theta_true':>10} {'theta_est':>9} {'v_GF':>6} {'v_nonGF':>7}  event")
    with GiantFiberCoprocessor(ReflexConfig()) as gf:
        k = 0
        while True:
            z = z0 - v * k * 1e-3
            if z < R * 1.2:
                break
            ev = cam.step((0.02, 0.0, z), R, k * 1000 + 1, 1000)
            if len(ev):
                gf.feed_raw_spikes(ev.tobytes())
            d = gf.step_eval((k + 1) * 1000)
            s = gf.debug_state()
            ttc = (z - R) / v * 1e3
            th = 2 * math.degrees(math.asin(R / z))
            msg = ""
            if d.triggered:
                msg = f"ESCAPE {EscapeMode(d.escape_mode).name} -> {d.action.name} (compute {d.compute_latency_us} us)"
            if msg or k % 50 == 0:
                print(f"{k:7d} {ttc:7.1f} {th:10.2f} {s['theta_deg']:9.2f} {max(s['v_gf']):6.2f} {max(s['v_nongf']):7.2f}  {msg}")
            k += 1


if __name__ == "__main__":
    main()
