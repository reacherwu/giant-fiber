#!/usr/bin/env python3
"""Qualitative validation of the LPLC2/LC4 -> GF escape model against known fly behaviour.

Stimuli (synthetic events from geometry, see giantfiber/sim/events.py; camera static):
  * looming: a sphere of radius R approaching head-on at constant speed, r/v = R/v swept
  * controls: receding sphere, laterally translating sphere at constant distance,
              static sphere, sensor-noise only

Checks reported (no pass/fail thresholds are hard-coded into the conclusions printed):
  1. Angular-size tuning: angular size at GF spike vs r/v, and the linear fit of
     time-before-contact at the spike vs r/v (a fixed angular threshold predicts a line,
     cf. the LGMD/GF literature; von Reyn et al. 2017; Ache et al. 2019).
  2. Threshold: no escape for controls.
  3. Mode selection: fraction of short-mode escapes vs r/v (von Reyn et al. 2014: fast
     looms -> GF-driven short mode, slow looms -> non-GF long mode).

Usage: python3 tools/validation/validate_escape_model.py [--out docs/model] [--config fly|drone]
"""
import argparse
import csv
import json
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from giantfiber import GiantFiberCoprocessor, ReflexConfig  # noqa: E402
from giantfiber.sim.events import EventCamera  # noqa: E402

CONFIGS = {
    # Engine defaults
    "default": {},
}


def cfg_window_s(cfg):
    return ReflexConfig(**cfg).refractory_period_us * 1e-6


def run(stim, cfg, R=0.15, dt_us=1000, seed=0, noise_hz=0.0, max_s=12.0, offset_x=0.03, **kw):
    gf = GiantFiberCoprocessor(ReflexConfig(**cfg))
    cam = EventCamera(noise_rate_hz=noise_hz, seed=seed, fov_deg=ReflexConfig(**cfg).fov_deg)
    trace = []
    first = {}
    n = int(max_s * 1e6 / dt_us)
    for k in range(n):
        t = k * dt_us * 1e-6
        if stim == "loom":
            v = R / (kw["rv_ms"] * 1e-3)
            Z = kw["z0"] - v * t
            if Z < R * 1.05:
                break
            p = (offset_x, 0.0, Z)
            ttc_ms = (Z - R) / v * 1e3
        elif stim == "recede":
            # object is present (static) for 100 ms, then recedes at 2 m/s
            p = (offset_x, 0.0, 0.6 + 2.0 * max(0.0, t - 0.1))
            ttc_ms = float("nan")
            if t > 3.0:
                break
        elif stim == "lateral":
            p = (-3.0 + 3.0 * t, 0.0, 3.0)  # 3 m/s sideways at 3 m
            ttc_ms = float("nan")
            if t > 2.0:
                break
        elif stim == "static":
            p = (offset_x, 0.0, 1.0)
            ttc_ms = float("nan")
            if t > 2.0:
                break
        elif stim == "noise":
            p = None
            ttc_ms = float("nan")
            if t > 2.0:
                break
        ev = cam.step(p, R, k * dt_us + 1, dt_us)
        if len(ev):
            gf.feed_raw_spikes(ev.tobytes())
        d = gf.step_eval(k * dt_us + dt_us)
        s = gf.debug_state()
        theta_true = 2 * math.degrees(math.asin(min(1.0, R / math.dist((0, 0, 0), p)))) if p else 0.0
        trace.append((t, theta_true, s["theta_deg"], s["theta_dot_dps"], max(s["v_gf"]), max(s["v_nongf"]), int(d.escape_mode), ttc_ms))
        rec = dict(t=t, ttc_ms=ttc_ms, theta_true=theta_true, theta_est=s["theta_deg"])
        if s["last_gf_spike_us"] and "gf_spike" not in first:
            first["gf_spike"] = rec
        if d.triggered:
            if "episode1" not in first:
                first["episode1"] = dict(rec, mode="long" if int(d.escape_mode) == 1 else "short")
            elif first["episode1"].get("upgraded") is None and int(d.escape_mode) == 2 and t - first["episode1"]["t"] < cfg_window_s(cfg):
                first["episode1"]["upgraded"] = rec
                first["episode1"]["mode"] = "long->short"
            key = "short" if int(d.escape_mode) == 2 else "long"
            first.setdefault(key, rec)
    gf.close()
    return trace, first


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/model")
    ap.add_argument("--rv", default="5,10,20,40,80")
    ap.add_argument("--repeats", type=int, default=5)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    cfg = {}
    R = 0.15
    rows = []
    traces = {}
    for rv in [float(x) for x in args.rv.split(",")]:
        for rep in range(args.repeats):
            z0 = R / math.tan(math.radians(1.0))  # start at 2 deg angular size
            tr, first = run("loom", cfg, R=R, rv_ms=rv, z0=z0, seed=rep, offset_x=0.02 * (rep - 2))
            if rep == 0:
                traces[rv] = tr
            row = dict(stimulus="loom", rv_ms=rv, repeat=rep)
            ep = first.get("episode1")
            row["onset_t_before_contact_ms"] = round(ep["ttc_ms"], 2) if ep else ""
            row["onset_theta_true_deg"] = round(ep["theta_true"], 2) if ep else ""
            g = first.get("gf_spike")
            row["gf_spike_t_before_contact_ms"] = round(g["ttc_ms"], 2) if g else ""
            row["gf_spike_theta_true_deg"] = round(g["theta_true"], 2) if g else ""
            row["gf_spike_theta_est_deg"] = round(g["theta_est"], 2) if g else ""
            row["mode"] = ep["mode"] if ep else "none"
            rows.append(row)
    controls = []
    for stim, extra in [("recede", {}), ("lateral", {}), ("static", {}), ("noise", {"noise_hz": 0.1}), ("noise", {"noise_hz": 0.3}), ("noise", {"noise_hz": 1.0}), ("noise", {"noise_hz": 3.0})]:
        for rep in range(args.repeats):
            tr, first = run(stim, cfg, R=R, seed=100 + rep, **extra)
            label = stim + (f"_{extra['noise_hz']}Hz_per_px" if extra else "")
            controls.append(dict(stimulus=label, repeat=rep, escapes=int("episode1" in first), gf_spike=int("gf_spike" in first)))

    with open(os.path.join(args.out, "looming_validation.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    with open(os.path.join(args.out, "looming_controls.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(controls[0].keys()))
        w.writeheader()
        w.writerows(controls)

    # Summary
    summary = {"looming": [], "controls": {}}
    print(f"{'r/v ms':>7} {'n':>3} {'GF-spike theta deg (mean)':>26} {'GF-spike t-before-contact ms':>29} {'short-mode frac':>16} {'long only':>10} {'no escape':>10}")
    xs, ys = [], []
    for rv in sorted({r["rv_ms"] for r in rows}):
        rr = [r for r in rows if r["rv_ms"] == rv]
        th = [r["gf_spike_theta_true_deg"] for r in rr if r["gf_spike_theta_true_deg"] != ""]
        tc = [r["gf_spike_t_before_contact_ms"] for r in rr if r["gf_spike_t_before_contact_ms"] != ""]
        short_first = sum(r["mode"] == "short" for r in rr)
        upgraded = sum(r["mode"] == "long->short" for r in rr)
        long_only = sum(r["mode"] == "long" for r in rr)
        none = sum(r["mode"] == "none" for r in rr)
        for x in tc:
            xs.append(rv)
            ys.append(x)
        ent = dict(rv_ms=rv, n=len(rr), gf_theta_mean=float(np.mean(th)) if th else None,
                   gf_t_before_contact_mean=float(np.mean(tc)) if tc else None,
                   gf_short_first=short_first, long_then_short=upgraded, long_only=long_only, none=none)
        summary["looming"].append(ent)
        print(f"{rv:7.0f} {len(rr):3d} {ent['gf_theta_mean'] if th else float('nan'):26.1f} {ent['gf_t_before_contact_mean'] if tc else float('nan'):29.1f} "
              f"{(short_first + upgraded) / len(rr):16.2f} {long_only:10d} {none:10d}")
    if len(set(xs)) >= 2:
        slope, icpt = np.polyfit(xs, ys, 1)
        r = np.corrcoef(xs, ys)[0, 1]
        # slope of t_before_contact vs r/v for a fixed angular threshold theta*: t = (r/v)/tan(theta*/2)
        theta_star = 2 * math.degrees(math.atan(1.0 / slope)) if slope > 0 else float("nan")
        summary["fit"] = dict(slope=float(slope), intercept_ms=float(icpt), r=float(r), implied_threshold_deg=theta_star)
        print(f"Linear fit GF-spike time-before-contact = {slope:.2f} * r/v + {icpt:.1f} ms (r = {r:.3f}); implied angular threshold = {theta_star:.1f} deg")
    for c in controls:
        summary["controls"].setdefault(c["stimulus"], 0)
        summary["controls"][c["stimulus"]] += c["escapes"] > 0
    print("Controls (trials with any escape / trials):", {k: f"{v}/{args.repeats}" for k, v in summary["controls"].items()})
    with open(os.path.join(args.out, "looming_validation_summary.json"), "w") as f:
        json.dump(summary, f, indent=2)

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        fig, ax = plt.subplots(1, 3, figsize=(15, 4.2))
        for rv, tr in traces.items():
            a = np.array(tr)
            ttc = a[:, 7]
            ax[0].plot(-ttc, a[:, 4], label=f"r/v={rv:g} ms")
            ax[1].plot(-ttc, a[:, 5], label=f"r/v={rv:g} ms")
        p = ReflexConfig()
        ax[0].axhline(p.looming_threshold, color="k", ls="--", lw=0.8)
        ax[1].axhline(p.nongf_threshold, color="k", ls="--", lw=0.8)
        for a_, t in zip(ax[:2], ["Giant Fiber (DNp01) membrane", "non-GF descending pathway"]):
            a_.set_xlim(-1500, 0)
            a_.set_xlabel("time relative to contact (ms)")
            a_.set_title(t)
            a_.legend(fontsize=7)
        if xs:
            ax[2].scatter(xs, ys, s=12)
            xx = np.linspace(0, max(xs), 10)
            ax[2].plot(xx, slope * xx + icpt, "r-", lw=1)
            ax[2].set_xlabel("r/v (ms)")
            ax[2].set_ylabel("GF spike: time before contact (ms)")
            ax[2].set_title(f"fit r={r:.3f}, implied threshold {theta_star:.1f} deg")
        fig.tight_layout()
        fig.savefig(os.path.join(args.out, "looming_validation.png"), dpi=110)
    except ImportError:
        pass


if __name__ == "__main__":
    main()
