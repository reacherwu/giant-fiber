#!/usr/bin/env python3
"""GiantFiber benchmarks. Every figure printed is measured in this run; PASS/FAIL is computed
by comparing the measurement with the target listed next to it.

  python3 benchmarks/run_benchmarks.py [--quick] [--json out.json]

Sections:
  1. native   : Rust engine without FFI (cargo example `bench`), looming stimulus
  2. ffi      : Python -> C ABI -> Rust step_eval round trip (includes pydantic model creation)
  3. ingest   : raw event buffers through the C ABI
  4. protocol : MAVLink SET_POSITION_TARGET_LOCAL_NED encode, CAN-FD encode/decode, EVT2 decode
  5. noise    : false triggers on sensor noise only (synthetic Poisson noise, 1 event/px/s)
No power figure is reported: power has not been measured on any hardware.
"""
import argparse
import json
import os
import platform
import subprocess
import sys
import time

import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from giantfiber import GiantFiberCoprocessor, ReflexConfig
from giantfiber.integrations.can_fd import CANFDCodec, CANFDReflexFrame
from giantfiber.integrations.px4_mavlink import (
    POSITION_TARGET_TYPEMASK_ACCEL_ONLY,
    MAVLinkMessage,
    MAVLinkV2Codec,
)
from giantfiber.sensors.prophesee_evt import PropheseeEVT2Decoder
from giantfiber.sim.events import EVENT_DTYPE, EventCamera

RESULTS = {"checks": []}


def check(name, value, op, target, unit):
    ok = value < target if op == "<" else value > target if op == ">" else value == target
    RESULTS["checks"].append({"name": name, "value": value, "op": op, "target": target, "unit": unit, "passed": bool(ok)})
    print(f"  {'PASS' if ok else 'FAIL'}  {name}: {value:,.3f} {unit} (target {op} {target:,} {unit})")
    return ok


def pcts(a):
    a = np.asarray(a, dtype=float)
    return {"mean": float(a.mean()), "p50": float(np.percentile(a, 50)), "p99": float(np.percentile(a, 99)),
                "max": float(a.max()), "n": int(a.size)}


def machine():
    cpu = platform.processor()
    try:
        for line in open("/proc/cpuinfo"):
            if line.startswith("model name"):
                cpu = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    rustc = subprocess.run(["rustc", "--version"], capture_output=True, text=True).stdout.strip()
    return {"cpu": cpu, "cores": os.cpu_count(), "os": platform.platform(), "python": platform.python_version(), "rustc": rustc}


def native(quick):
    print("[1] native Rust engine (cargo run --release --example bench)")
    cmd = ["cargo", "run", "--release", "-q", "--example", "bench", "-p", "giantfiber-core"] + (["--", "--quick"] if quick else [])
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip().splitlines()[-1]
    r = json.loads(out)
    RESULTS["native"] = r
    s = r["step_eval_ns"]
    print(f"  step_eval: mean {s['mean'] / 1e3:.2f} us, p50 {s['p50'] / 1e3:.2f} us, p99 {s['p99'] / 1e3:.2f} us, max {s['max'] / 1e3:.2f} us over {r['steps']} steps")
    print(f"  ingest: {r['ingest_events_per_s'] / 1e6:.1f} M events/s; snapshot {r['snapshot_bytes']} B, save {r['snapshot_save_ns']} ns, restore {r['snapshot_restore_ns']} ns; engine struct {r['engine_struct_bytes']} B")
    check("native step_eval p99", s["p99"] / 1e3, "<", 1000, "us")
    check("native triggers per loom", r["triggers_per_loom"], ">", 0, "")


def loom_buffers(n_rep=1):
    cam = EventCamera()
    R, v = 0.15, 15.0
    bufs = []
    for k in range(4000):
        Z = 8.0 - v * k * 1e-3
        if Z < 0.2:
            break
        bufs.append(cam.step((0.02, 0.0, Z), R, k * 1000 + 1, 1000).tobytes())
    return bufs * n_rep


def ffi(quick):
    print("[2] Python -> C ABI round trip (feed events + step_eval + pydantic ReflexDecision)")
    bufs = loom_buffers(1 if quick else 5)
    lat, comp = [], []
    with GiantFiberCoprocessor(ReflexConfig()) as gf:
        t_base = 0
        for i, b in enumerate(bufs):
            if i and i % 533 == 0:
                gf.reset()
            t0 = time.perf_counter_ns()
            gf.feed_raw_spikes(b)
            d = gf.step_eval((i % 533 + 1) * 1000 + t_base)
            lat.append((time.perf_counter_ns() - t0) / 1e3)
            comp.append(d.compute_latency_us)
    RESULTS["ffi_roundtrip_us"] = pcts(lat)
    RESULTS["ffi_reported_compute_us"] = pcts(comp)
    p = RESULTS["ffi_roundtrip_us"]
    print(f"  round trip: mean {p['mean']:.1f} us, p50 {p['p50']:.1f} us, p99 {p['p99']:.1f} us (n={p['n']})")
    check("FFI round trip p99", p["p99"], "<", 2000, "us")


def ingest(quick):
    print("[3] raw event ingestion through the C ABI")
    n = 200_000 if quick else 2_000_000
    rng = np.random.default_rng(0)
    ev = np.zeros(n, dtype=EVENT_DTYPE)
    ev["timestamp_us"] = np.arange(n) // 10
    ev["x"] = rng.integers(0, 320, n)
    ev["y"] = rng.integers(0, 320, n)
    ev["polarity"] = 1
    buf = ev.tobytes()
    with GiantFiberCoprocessor(ReflexConfig()) as gf:
        t0 = time.perf_counter()
        gf.feed_raw_spikes(buf)
        dt = time.perf_counter() - t0
    eps = n / dt
    RESULTS["ingest_events_per_s"] = eps
    print(f"  {n:,} events in {dt * 1e3:.1f} ms -> {eps / 1e6:.1f} M events/s (engine state is a fixed-size struct, see native section)")
    check("C ABI ingestion", eps / 1e6, ">", 1.0, "M events/s")


def protocol(quick):
    print("[4] protocol codecs (pure Python)")
    n = 2000 if quick else 20000
    t0 = time.perf_counter()
    for i in range(n):
        p = MAVLinkV2Codec.pack_set_position_target_local_ned(i, 1, 1, POSITION_TARGET_TYPEMASK_ACCEL_ONLY, acc=(0.0, 9.0, 0.0))
        MAVLinkV2Codec.encode(MAVLinkMessage(msgid=84, sysid=1, compid=191, seq=i & 0xFF, payload=p))
    mav_us = (time.perf_counter() - t0) / n * 1e6
    f = CANFDReflexFrame(confidence=0.9, roll_rate_deg_s=300.0, thrust=0.95, escape_mode=2)
    t0 = time.perf_counter()
    for _ in range(n):
        CANFDCodec.decode(CANFDCodec.encode(f))
    can_us = (time.perf_counter() - t0) / n * 1e6
    words = np.zeros(50000, dtype="<u4")
    idx = np.arange(50000)
    words[:] = (0x1 << 28) | ((idx & 0x3F) << 22) | (((idx * 7) % 320) << 11) | ((idx * 13) % 320)
    words[::100] = (0x8 << 28) | (idx[::100] & 0x0FFFFFFF)
    dec = PropheseeEVT2Decoder()
    t0 = time.perf_counter()
    spikes = dec.decode_buffer(words.tobytes())
    evt_rate = len(spikes) / (time.perf_counter() - t0)
    RESULTS["protocol"] = {"mavlink84_encode_us": mav_us, "canfd_encode_decode_us": can_us, "evt2_decode_events_per_s": evt_rate}
    print(f"  MAVLink #84 pack+encode {mav_us:.2f} us; CAN-FD encode+decode {can_us:.2f} us; EVT2 decode {evt_rate / 1e6:.2f} M events/s (Python)")
    check("MAVLink setpoint encode", mav_us, "<", 100, "us")


def noise(quick):
    print("[5] false triggers on noise only")
    secs = 5 if quick else 60
    cam = EventCamera(noise_rate_hz=1.0, seed=7)
    trig = 0
    with GiantFiberCoprocessor(ReflexConfig()) as gf:
        for k in range(secs * 1000):
            ev = cam.step(None, 0.15, k * 1000 + 1, 1000)
            if len(ev):
                gf.feed_raw_spikes(ev.tobytes())
            trig += gf.step_eval(k * 1000 + 1000).triggered
    RESULTS["noise"] = {"seconds": secs, "rate_hz_per_px": 1.0, "triggers": trig}
    print(f"  {trig} triggers in {secs} s of 1 Hz/px noise (320x320 -> ~102k events/s)")
    check("noise-only triggers", trig, "==", 0, "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--json", default=None)
    ap.add_argument("--skip-native", action="store_true")
    a = ap.parse_args()
    RESULTS["machine"] = machine()
    RESULTS["quick"] = a.quick
    print("machine:", json.dumps(RESULTS["machine"]))
    if not a.skip_native:
        native(a.quick)
    ffi(a.quick)
    ingest(a.quick)
    protocol(a.quick)
    noise(a.quick)
    failed = [c["name"] for c in RESULTS["checks"] if not c["passed"]]
    print(f"\n{len(RESULTS['checks']) - len(failed)}/{len(RESULTS['checks'])} checks passed" + (f"; failed: {failed}" if failed else ""))
    if a.json:
        with open(a.json, "w") as f:
            json.dump(RESULTS, f, indent=2)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
