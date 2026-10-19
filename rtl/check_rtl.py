#!/usr/bin/env python3
"""Check rtl/gf_descending.v against the Rust engine (needs Icarus Verilog: iverilog, vvp).

For each stimulus the Rust engine is run with 1 ms steps; its LPLC2/LC4 population activities
(debug state) are quantised to Q16.16 and fed to the RTL. We compare, step by step, the escape
mode and the GF / non-GF membrane values.

  python3 rtl/check_rtl.py [--json rtl/rtl_check.json]
"""
import argparse
import json
import math
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
from giantfiber import GiantFiberCoprocessor, ReflexConfig  # noqa: E402
from giantfiber.sim.events import EventCamera  # noqa: E402

Q = 65536


def check_params():
    data = open(os.path.join(ROOT, "crates/giantfiber-core/src/connectome/data.rs")).read()
    v = {k: int(n) for k, n in re.findall(r"pub const (\w+_SYN): u32 = (\d+);", data)}
    rtl = open(os.path.join(HERE, "gf_descending.v")).read()
    p = {k: int(n) for k, n in re.findall(r"(\w+)\s*=\s*32'd(\d+)", rtl)}
    errs = []
    for side, s in (("LEFT", "L"), ("RIGHT", "R")):
        for tgt, t in (("GF", "GF"), ("NONGF_DN", "NG")):
            a, b = v[f"LPLC2_{side}_TO_{tgt}_SYN"], v[f"LC4_{side}_TO_{tgt}_SYN"]
            for name, num in ((f"W_{t}_LPLC2_{s}", a), (f"W_{t}_LC4_{s}", b)):
                want = round(num / (a + b) * Q)
                if p[name] != want:
                    errs.append(f"{name}={p[name]} expected {want}")
    cfg = ReflexConfig()
    for name, want in (("A_GF", round(1000 / cfg.gf_tau_us * Q)), ("A_NG", round(1000 / cfg.nongf_tau_us * Q)),
                       ("THR_GF", round(cfg.looming_threshold * Q)), ("THR_NG", round(cfg.nongf_threshold * Q))):
        if p[name] != want:
            errs.append(f"{name}={p[name]} expected {want}")
    return errs


def run_engine(kind, rv_ms=None, seed=0, noise=0.3):
    gf = GiantFiberCoprocessor(ReflexConfig())
    cam = EventCamera(noise_rate_hz=noise, seed=seed)
    R = 0.15
    rows = []
    n = 10000
    for k in range(n):
        t = k * 1e-3
        if kind == "loom":
            v = R / (rv_ms * 1e-3)
            Z = R / math.tan(math.radians(1.0)) - v * t
            if Z < R * 1.05:
                break
            p = (0.03, 0.0, Z)
        else:
            p = None
            if k >= 1000:
                break
        ev = cam.step(p, R, k * 1000 + 1, 1000)
        if len(ev):
            gf.feed_raw_spikes(ev.tobytes())
        gf.step_eval(k * 1000 + 1000)
        s = gf.debug_state()
        rows.append((s["lplc2"], s["lc4"], s["v_gf"], s["v_nongf"], s["escape_mode"]))
    gf.close()
    return rows


def run_rtl(rows, work):
    with open(os.path.join(work, "stimulus.txt"), "w") as f:
        for lp, lc, *_ in rows:
            f.write(f"{round(lp[0] * Q)} {round(lp[1] * Q)} {round(lc[0] * Q)} {round(lc[1] * Q)}\n")
    exe = os.path.join(work, "tb.vvp")
    subprocess.run(["iverilog", "-g2005", "-o", exe, os.path.join(HERE, "gf_descending.v"),
                    os.path.join(HERE, "tb_gf_descending.v")], check=True)
    subprocess.run(["vvp", "-n", exe], cwd=work, check=True, capture_output=True)
    return [list(map(int, line.split())) for line in open(os.path.join(work, "rtl_out.txt"))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", default=None)
    a = ap.parse_args()
    errs = check_params()
    print("parameter check:", "OK" if not errs else errs)
    cases = [("loom", rv, s) for rv in (5, 10, 20, 40, 80) for s in (0, 1)] + [("noise", None, 0)]
    results = []
    with tempfile.TemporaryDirectory() as work:
        for kind, rv, seed in cases:
            rows = run_engine(kind, rv, seed)
            out = run_rtl(rows, work)
            assert len(out) == len(rows), (len(out), len(rows))
            mode_mismatch = sum(1 for r, o in zip(rows, out) if r[4] != o[4])
            dv_gf = max(abs(r[2][i] - o[i] / Q) for r, o in zip(rows, out) for i in (0, 1))
            dv_ng = max(abs(r[3][i] - o[2 + i] / Q) for r, o in zip(rows, out) for i in (0, 1))
            first_rust = next((k for k, r in enumerate(rows) if r[4]), None)
            first_rtl = next((k for k, o in enumerate(out) if o[4]), None)
            res = dict(stimulus=kind, rv_ms=rv, seed=seed, steps=len(rows), mode_mismatch_steps=mode_mismatch,
                       first_escape_step_rust=first_rust, first_escape_step_rtl=first_rtl,
                       max_abs_err_v_gf=round(dv_gf, 6), max_abs_err_v_nongf=round(dv_ng, 6))
            results.append(res)
            print(res)
    # Fixed point vs float32: a membrane that lands within ~1e-4 of a threshold can cross one step
    # earlier/later. Pass criterion: same first-escape outcome within 1 step, <= 2 mismatching steps.
    def close(r):
        a, b = r["first_escape_step_rust"], r["first_escape_step_rtl"]
        same = (a is None and b is None) or (a is not None and b is not None and abs(a - b) <= 1)
        return same and r["mode_mismatch_steps"] <= 2
    ok = not errs and all(close(r) for r in results)
    summary_exact = sum(1 for r in results if r["mode_mismatch_steps"] == 0)
    print(f"{summary_exact}/{len(results)} stimuli with bit-for-bit identical mode sequences")
    summary = dict(parameter_errors=errs, cases=results, passed=ok, exact_mode_match=summary_exact, n_cases=len(results))
    if a.json:
        json.dump(summary, open(a.json, "w"), indent=2)
    print("RESULT:", "PASS" if ok else "FAIL")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
