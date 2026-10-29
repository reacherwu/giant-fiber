#!/usr/bin/env python3
"""HIL-prep: replay recorded EVT3 events through the far-field + Giant Fiber detection stack.

This runs the same detection components as run_campaign.py (FarFieldTTC on derotated events
plus the 64x64 GF stage) but without a PX4 vehicle, so it can be exercised on captured
recordings before hardware-in-the-loop.

Usage:
    python3 sim/px4_sitl/replay_events.py --dataset path/to/dataset --out docs/replay_sitl
"""
import argparse
import csv
import json
import os
import sys

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from giantfiber import GiantFiberCoprocessor, ReflexConfig
from giantfiber.farfield import FarFieldTTC, FarFieldConfig
from giantfiber.sensors.prophesee_evt import PropheseeEVT3Decoder


def run(path, sensor_w, sensor_h, rig_ff_cfg):
    dec = PropheseeEVT3Decoder(sensor_w=sensor_w, sensor_h=sensor_h)
    with open(path, "rb") as f:
        raw = f.read()
    spikes = dec.decode_buffer(raw)
    gf = GiantFiberCoprocessor(ReflexConfig())
    ff = FarFieldTTC(FarFieldConfig(**rig_ff_cfg))
    triggers = []
    last = 0
    armed_seen = False
    for sp in spikes:
        gf.feed_spike(sp.x, sp.y, sp.timestamp_us, sp.polarity, raw_w=sensor_w, raw_h=sensor_h)
        ev = np.array([(sp.x, sp.y, sp.timestamp_us)], dtype=np.dtype([("x", "<u2"), ("y", "<u2"), ("timestamp_us", "<u8")]))
        ff.feed(ev)
        if sp.timestamp_us - last >= 10000:
            out = ff.eval(sp.timestamp_us)
            if out["armed"]:
                armed_seen = True
            d = gf.step_eval(sp.timestamp_us)
            if d.triggered:
                triggers.append((sp.timestamp_us, d, out))
            last = sp.timestamp_us
    gf.close()
    return len(spikes), triggers, armed_seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", default="docs/replay_sitl")
    ap.add_argument("--sensor-w", type=int, default=1280)
    ap.add_argument("--sensor-h", type=int, default=720)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    rows = list(csv.DictReader(open(os.path.join(args.dataset, "labels.csv"))))
    ff_cfg = dict(tau_arm_s=1.0, min_fit_span_us=150000, min_t_stat=8.0, confirm_evals=15, max_miss_radii=6.0, tau_min_s=0.15)
    results = []
    ft = 0
    nt = 0
    for r in rows:
        path = os.path.join(args.dataset, r["file"])
        n, triggers, armed = run(path, args.sensor_w, args.sensor_h, ff_cfg)
        triggered = len(triggers) > 0
        if r.get("kind") != "threat":
            nt += 1
            if triggered:
                ft += 1
        results.append({"file": r["file"], "kind": r.get("kind"), "n_spikes": n, "triggered": triggered, "armed": armed, "n_triggers": len(triggers)})
    summary = {
        "n_trials": len(results),
        "false_trigger_rate": (ft / nt) if nt else 0.0,
        "false_triggers": ft,
        "non_threat_trials": nt,
    }
    with open(os.path.join(args.out, "replay_summary.json"), "w") as f:
        json.dump({"summary": summary, "trials": results}, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()