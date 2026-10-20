#!/usr/bin/env python3
"""Offline development harness for the far-field TTC stage (no PX4): IMX636 camera model on a
hovering vehicle with attitude jitter. Reports arming time-to-contact for threats and false arms
for fly-bys and object-free noise. Development scenarios use seeds < 10000; the final SITL campaign
uses different seeds and randomised geometry."""
import argparse, json, math, sys, os
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from giantfiber.sim.events import imx636_camera, world_to_cam, quat_to_rot, mount_rotation  # noqa: E402
from giantfiber.farfield import FarFieldTTC, FarFieldConfig, Derotator  # noqa: E402

HIT = 0.40
DT = 2000


def quat(roll, pitch, yaw):
    cr, sr = math.cos(roll / 2), math.sin(roll / 2)
    cp, sp = math.cos(pitch / 2), math.sin(pitch / 2)
    cy, sy = math.cos(yaw / 2), math.sin(yaw / 2)
    return (cr * cp * cy + sr * sp * sy, sr * cp * cy - cr * sp * sy, cr * sp * cy + sr * cp * sy, cr * cp * sy - sr * sp * cy)


def run(kind, v, dist, az, el, offset, R, noise, seed, cfg, jitter_deg=0.5, latency_us=2000, lens_mm=8.0, drift=0.0):
    rng = np.random.default_rng(seed)
    vd = rng.normal(size=3); vd *= drift / max(1e-9, np.linalg.norm(vd))  # unsettled-drone drift (m/s)
    lenses = lens_mm if isinstance(lens_mm, (list, tuple)) else [lens_mm]
    # lens spec "25@yaw:pitch" = 25 mm camera yawed / pitched (deg)
    specs = []
    for L in lenses:
        Ls = str(L)
        if "@" in Ls:
            f_, m = Ls.split("@"); yw, pt = (float(z) for z in m.split(":"))
            specs.append((float(f_), mount_rotation(yw, pt)))
        else:
            specs.append((float(Ls), mount_rotation(0, 0)))
    cams = [imx636_camera(noise_rate_hz=noise, seed=seed + 7919 * j, lens_mm=L) for j, (L, _) in enumerate(specs)]
    mounts = [m for _, m in specs]
    ffs = [FarFieldTTC(cfg) for _ in cams]
    ders = [Derotator(c.width, c.height, c.fov_deg, R_cb=m) for c, m in zip(cams, mounts)]
    for d_ in ders:
        d_.set_reference(np.eye(3))
    u = np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), -math.sin(el)])
    start = dist * u
    perp = np.cross(u, [0, 0, 1.0]); perp /= np.linalg.norm(perp)
    up = np.cross(perp, u)
    a = rng.uniform(0, 2 * math.pi)
    target = offset * (math.cos(a) * perp + math.sin(a) * up) if kind != "flyby" else offset * perp * rng.choice([-1, 1])
    vel = (target - start) / max(1e-9, np.linalg.norm(target - start)) * v
    ph = rng.uniform(0, 2 * math.pi, 3)
    fr = rng.uniform(0.5, 3.0, 3)
    T = dist / v + 0.5 if kind != "noise" else 6.0
    bufs = [[] for _ in cams]
    first = None
    if kind != "noise":
        for c, m in zip(cams, mounts):
            c.prime(world_to_cam(start, np.zeros(3), quat(0, 0, 0), m), R)
    for k in range(int(T * 1e6 / DT)):
        t = k * DT
        ang = [math.radians(jitter_deg) * math.sin(2 * math.pi * fr[i] * t * 1e-6 + ph[i]) for i in range(3)]
        q = quat(*ang)
        p_d = np.array([0.05 * math.sin(t * 1e-6 * 1.3 + ph[0]), 0.05 * math.sin(t * 1e-6 * 0.9 + ph[1]), 0.0]) + vd * t * 1e-6
        if kind == "noise":
            p_cams, d = [None] * len(cams), float("inf")
        else:
            p_o = start + vel * t * 1e-6
            d = float(np.linalg.norm(p_o - p_d))
            p_cams = [world_to_cam(p_o, p_d, q, m) for m in mounts]
        now = t + DT
        Rq = quat_to_rot(q)
        outs = []
        for c, ff, der, buf, p_cam in zip(cams, ffs, ders, bufs, p_cams):
            buf.append(der.apply(c.step(p_cam, R, t + 1, DT), Rq))
            while buf and (len(buf[0]) == 0 or int(buf[0]["timestamp_us"].max()) + latency_us <= now):
                ff.feed(buf.pop(0))
            outs.append(ff.eval(now))
        o = next((x for x in outs if x["armed"]), outs[0])
        if o["armed"] and first is None:
            ttc = (d - HIT) / v if kind != "noise" else float("nan")
            first = dict(t=now * 1e-6, dist=d, true_ttc=ttc, tau=o["tau_s"], miss=o["miss_radii"], r=o["r_px"])
            if kind != "threat":
                break
        if kind == "threat" and d < HIT:
            break
    return first


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=8)
    ap.add_argument("--speeds", default="6,10,14,20")
    ap.add_argument("--noise", type=float, default=0.1)
    ap.add_argument("--R", type=float, default=0.15)
    ap.add_argument("--jitter", type=float, default=0.5)
    ap.add_argument("--cfg", default="{}")
    ap.add_argument("--seed0", type=int, default=0)
    ap.add_argument("--json", default="")
    ap.add_argument("--dist", default="15,30")
    ap.add_argument("--lens", default="8", help="lens focal length(s) in mm, comma list = one IMX636 per lens")
    ap.add_argument("--az", type=float, default=15.0)
    ap.add_argument("--el", type=float, default=8.0)
    ap.add_argument("--drift", type=float, default=0.0, help="constant drone drift speed (m/s), random direction")
    ap.add_argument("--nfly", type=int, default=-1, help="number of flyby controls (default: --n)")
    a = ap.parse_args()
    cfg = FarFieldConfig(**eval(a.cfg))
    D = [float(x) for x in a.dist.split(",")]
    a.lens = a.lens.split(",")
    out = dict(cfg=cfg.__dict__, threats={}, flyby=[], noise=[])
    speeds = [float(x) for x in a.speeds.split(",")]
    for v in speeds:
        res = []
        for i in range(a.n):
            rng = np.random.default_rng(a.seed0 + 100 * i + int(v))
            res.append(run("threat", v, rng.uniform(*D), math.radians(rng.uniform(-a.az, a.az)), math.radians(rng.uniform(-a.el, a.el)),
                           rng.uniform(0, 0.15), a.R, a.noise, a.seed0 + i, cfg, a.jitter, lens_mm=a.lens, drift=a.drift))
        ttc = [r["true_ttc"] for r in res if r]
        out["threats"][v] = res
        print(f"v={v:4.0f} armed {len(ttc)}/{len(res)} true TTC at arm (s): "
              f"min {min(ttc) if ttc else float('nan'):.3f} p10 {np.percentile(ttc, 10) if ttc else float('nan'):.3f} median {np.median(ttc) if ttc else float('nan'):.3f} "
              f"dist median {np.median([r['dist'] for r in res if r]) if ttc else float('nan'):.2f} m", flush=True)
    fb = 0
    for i in range(a.nfly if a.nfly >= 0 else a.n):
        rng = np.random.default_rng(a.seed0 + 7000 + i)
        v = float(rng.choice(speeds))
        r = run("flyby", v, rng.uniform(*D), math.radians(rng.uniform(-a.az, a.az)), math.radians(rng.uniform(-a.el, a.el)), rng.uniform(2.5, 6.0), a.R, a.noise, a.seed0 + 50 + i, cfg, a.jitter, lens_mm=a.lens, drift=a.drift)
        out["flyby"].append(r); fb += bool(r)
    nz = 0
    nn = max(2, a.n // 4)
    for i in range(nn):
        r = run("noise", 1, 0, 0, 0, 0, a.R, a.noise, a.seed0 + 90 + i, cfg, a.jitter, lens_mm=a.lens)
        out["noise"].append(r); nz += bool(r)
    print(f"flyby false arms {fb}/{a.nfly if a.nfly >= 0 else a.n}  noise false arms {nz}/{nn}", flush=True)
    if a.json:
        json.dump(out, open(a.json, "w"), indent=1, default=float)


if __name__ == "__main__":
    main()
