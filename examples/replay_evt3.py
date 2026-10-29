#!/usr/bin/env python3
"""Replay a Prophesee EVT3 recording through the GiantFiber engine.

Usage:
    python3 examples/replay_evt3.py recording.evt3 [--sensor-w 1280] [--sensor-h 720] [--step-us 10000]

This is a software scaffold for real-sensor validation: it exercises the EVT3 decoder
and the engine on a captured stream. It does not, by itself, constitute validation
against ground truth; that requires labelled approach/miss annotations.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from giantfiber import GiantFiberCoprocessor, ReflexConfig
from giantfiber.sensors.prophesee_evt import PropheseeEVT3Decoder


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--sensor-w", type=int, default=1280)
    ap.add_argument("--sensor-h", type=int, default=720)
    ap.add_argument("--step-us", type=int, default=10000)
    args = ap.parse_args()

    dec = PropheseeEVT3Decoder(sensor_w=args.sensor_w, sensor_h=args.sensor_h)
    with open(args.path, "rb") as f:
        raw = f.read()

    # Decode then feed in time order.
    spikes = dec.decode_buffer(raw)
    print(f"decoded {len(spikes)} spikes")

    with GiantFiberCoprocessor(ReflexConfig()) as gf:
        # Feed all spikes; evaluate at the last timestamp.
        if spikes:
            last_ts = spikes[-1].timestamp_us
            for sp in spikes:
                gf.feed_spike(sp.x, sp.y, sp.timestamp_us, sp.polarity, raw_w=args.sensor_w, raw_h=args.sensor_h)
            d = gf.step_eval(last_ts)
            print(d)


if __name__ == "__main__":
    main()