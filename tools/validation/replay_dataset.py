#!/usr/bin/env python3
"""Offline replay of a labelled event-camera dataset through the GiantFiber engine.

Expected dataset layout:
    dataset/
      labels.csv   # columns: file, kind, speed_mps, contact_time_s, (optional) notes
      *.evt3       # Prophesee EVT3 recordings (or any file readable as bytes)

`kind` is one of: threat, flyby, hover, noise. A trial is a false trigger if a non-threat
recording produces any triggered decision. For threats, the trigger time and (if
`contact_time_s` is provided) the time-to-contact at trigger are reported.

Usage:
    python3 tools/validation/replay_dataset.py --dataset path/to/dataset --out docs/replay
"""
import argparse
import csv
import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from giantfiber import GiantFiberCoprocessor, ReflexConfig
from giantfiber.sensors.prophesee_evt import PropheseeEVT3Decoder


def run_one(path, sensor_w, sensor_h):
    dec = PropheseeEVT3Decoder(sensor_w=sensor_w, sensor_h=sensor_h)
    with open(path, "rb") as f:
        raw = f.read()
    spikes = dec.decode_buffer(raw)
    triggers = []
    with GiantFiberCoprocessor(ReflexConfig()) as gf:
        # Feed in time order and evaluate at each unique timestamp (downsampled).
        last = 0
        for sp in spikes:
            gf.feed_spike(sp.x, sp.y, sp.timestamp_us, sp.polarity, raw_w=sensor_w, raw_h=sensor_h)
            if sp.timestamp_us - last >= 10000:
                d = gf.step_eval(sp.timestamp_us)
                if d.triggered:
                    triggers.append((sp.timestamp_us, d))
                last = sp.timestamp_us
        if spikes:
            d = gf.step_eval(spikes[-1].timestamp_us)
            if d.triggered:
                triggers.append((spikes[-1].timestamp_us, d))
    return spikes, triggers


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", default="docs/replay")
    ap.add_argument("--sensor-w", type=int, default=1280)
    ap.add_argument("--sensor-h", type=int, default=720)
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    labels_path = os.path.join(args.dataset, "labels.csv")
    rows = list(csv.DictReader(open(labels_path)))
    results = []
    false_triggers = 0
    non_threat = 0
    threat_ttc = []
    for r in rows:
        path = os.path.join(args.dataset, r["file"])
        spikes, triggers = run_one(path, args.sensor_w, args.sensor_h)
        triggered = len(triggers) > 0
        ttc_ms = None
        if r.get("kind") == "threat" and triggered and r.get("contact_time_s"):
            contact = float(r["contact_time_s"])
            trig_t = triggers[0][0] * 1e-6
            speed = float(r.get("speed_mps", 0) or 0)
            if speed > 0:
                ttc_ms = (contact - trig_t) * 1000.0
                threat_ttc.append(ttc_ms)
        if r.get("kind") != "threat":
            non_threat += 1
            if triggered:
                false_triggers += 1
        results.append({
            "file": r["file"],
            "kind": r.get("kind"),
            "n_spikes": len(spikes),
            "triggered": triggered,
            "n_triggers": len(triggers),
            "first_trigger_us": triggers[0][0] if triggers else None,
            "ttc_ms_at_trigger": ttc_ms,
        })

    summary = {
        "n_trials": len(results),
        "false_trigger_rate": (false_triggers / non_threat) if non_threat else 0.0,
        "false_triggers": false_triggers,
        "non_threat_trials": non_threat,
        "threat_trials": sum(r.get("kind") == "threat" for r in rows),
        "threat_triggered": sum(r["triggered"] for r in results if r["kind"] == "threat"),
        "median_ttc_ms_at_trigger": (sorted(threat_ttc)[len(threat_ttc) // 2] if threat_ttc else None),
    }
    with open(os.path.join(args.out, "replay_results.json"), "w") as f:
        json.dump({"summary": summary, "trials": results}, f, indent=2)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()