#!/usr/bin/env python3
"""PX4 SITL campaign v3: IMX636 dual-camera rig + far-field TTC arming + Giant Fiber near-field stage.

Differences from run_campaign.py (v1/v2):
- Sensor: two simulated Prophesee/Sony IMX636 (1280x720) event cameras on the nose, an 8 mm lens
  (42.5 x 24.6 deg) and a 25 mm lens (14.2 x 8.0 deg), background rate 0.1 Hz/px, 1 ms pixel dead
  time and a 2 ms sensor-to-host delivery latency (`DelayLine`).
- Detection: the far-field TTC stage (crates/giantfiber-core/src/farfield.rs) runs on each camera,
  on events derotated with PX4's own attitude estimate (ATTITUDE_QUATERNION, EKF2, not ground truth).
  The 64x64 LPLC2/LC4 -> Giant Fiber model runs on the 8 mm stream (after a 2 ms nearest-neighbour
  background-activity filter) as the near-field stage; its decision counts only while a far-field
  track shows significant expansion. Either stage triggers the escape.
- Escape: thrust-axis (vertical) acceleration setpoint, held for `--escape-s`; it descends instead of
  climbing when the predicted miss vector says the object will pass above.
- Collision test: minimum distance on the continuous relative trajectory between loop steps
  (segment closest-approach), so objects at 120 m/s (0.24 m per 2 ms step) cannot tunnel.
- No return-to-origin between trials: the vehicle holds where the previous trial ended (altitude
  reset to 10 m) and each object is launched relative to the vehicle's current pose.
"""
import argparse, csv, gzip, json, math, os, random, sys, time
import numpy as np

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(__file__))
from pymavlink import mavutil  # noqa: E402
from run_campaign import Vehicle, unit_from_az_el, HIT_DIST, R_OBJ, HOVER_ALT  # noqa: E402
from giantfiber import GiantFiberCoprocessor, ReflexConfig  # noqa: E402
from giantfiber.farfield import FarFieldTTC, FarFieldConfig, Derotator  # noqa: E402
from giantfiber.sim.events import imx636_camera, world_to_cam, quat_to_rot, mount_rotation  # noqa: E402
from giantfiber.filters import BackgroundActivityFilter  # noqa: E402

LOOP_DT = 0.002
LENSES_MM = (8.0, 25.0)
# Camera rigs: list of (lens_mm, yaw_deg, pitch_deg). "dual" was the first held-out run; "tiled" adds
# overlapping 25 mm cameras (yaw -12/0/+12, pitch +-3.5) covering about +-19 x +-7.5 deg.
RIGS = {"dual": [(8.0, 0.0, 0.0), (25.0, 0.0, 0.0)],
        "tiled": [(8.0, 0.0, 0.0)] + [(25.0, y, p) for p in (3.5, -3.5) for y in (-12.0, 0.0, 12.0)]}
FF_CFG = dict(tau_arm_s=1.0, min_fit_span_us=150000, min_t_stat=8.0, confirm_evals=15, max_miss_radii=6.0, tau_min_s=0.15)
GF_PRESET = dict(fov_deg=42.5, lplc2_half_size_deg=1.5, lc4_half_velocity_dps=5.0)
LATENCY_US = 2000
BAF_US = 2000


class V3Vehicle(Vehicle):
    def __init__(self, url):
        super().__init__(url)
        self.att_q = None
        # Keep the simulated battery full. In the first v3 run, PX4's simulated battery failsafe
        # landed two of the three vehicles after ~2 h (those trials are excluded in analyze_v3.py).
        self.m.mav.param_set_send(self.tsys, 1, b"SIM_BAT_MIN_PCT", 100.0, mavutil.mavlink.MAV_PARAM_TYPE_REAL32)
        self.m.mav.command_long_send(self.tsys, 1, mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0, 31, int(1e6 / 250), 0, 0, 0, 0, 0)

    def poll(self):
        while True:
            msg = self.m.recv_match(blocking=False)
            if msg is None:
                return
            ty = msg.get_type()
            if ty == "ATTITUDE_QUATERNION":
                self.att_q = (msg.q1, msg.q2, msg.q3, msg.q4)
            elif ty == "HIL_STATE_QUATERNION":
                now = time.perf_counter()
                lat, lon, alt = msg.lat * 1e-7, msg.lon * 1e-7, msg.alt * 1e-3
                if self.ref is None:
                    self.ref = (lat, lon, alt)
                n = math.radians(lat - self.ref[0]) * 6378137.0
                e = math.radians(lon - self.ref[1]) * 6378137.0 * math.cos(math.radians(self.ref[0]))
                d = -(alt - self.ref[2])
                self.gt = dict(p=np.array([n, e, d]), v=np.array([msg.vx, msg.vy, msg.vz]) * 0.01,
                               q=tuple(msg.attitude_quaternion), t=now)
                self.gt_samples += 1
            elif ty == "LOCAL_POSITION_NED":
                self.local = (msg.x, msg.y, msg.z)
            elif ty == "HEARTBEAT" and msg.get_srcComponent() == 1 and msg.get_srcSystem() == self.tsys:
                self.armed = bool(msg.base_mode & 128)


def seg_min_dist(a, b):
    """Minimum |r(s)| for r linear from a to b, s in [0, 1]."""
    d = b - a
    dd = float(d @ d)
    s = 0.0 if dd < 1e-12 else min(1.0, max(0.0, -float(a @ d) / dd))
    return float(np.linalg.norm(a + s * d))


def accel_sp(climb, a):
    return (0.0, 0.0, -a if climb else a)


def run_trial(veh, trial, rng, args, out_dir):
    kind, arm = trial["kind"], trial["arm"]
    noise = trial.get("noise_hz", args.noise_hz)
    rig = RIGS[args.rig]
    cams = [imx636_camera(noise_rate_hz=noise, refractory_us=1000, lens_mm=L, seed=rng.randrange(1 << 30)) for L, _, _ in rig]
    mounts = [mount_rotation(y, p) for _, y, p in rig]
    ffs = [FarFieldTTC(FarFieldConfig(**FF_CFG)) for _ in cams]
    ders = [Derotator(c.width, c.height, c.fov_deg, R_cb=m) for c, m in zip(cams, mounts)]
    bufs = [[] for _ in cams]
    gf = GiantFiberCoprocessor(ReflexConfig(**GF_PRESET))
    baf = BackgroundActivityFilter(1280, 720, BAF_US)
    x, y, _ = veh.local if veh.local else (0.0, 0.0, 0.0)
    veh.hover_sp = (x, y, -HOVER_ALT)
    settled = veh.settle(min_s=args.settle_min, max_s=args.settle_max)
    p0, _, q0 = veh.pose_now()
    w, qx, qy, qz = q0
    yaw = math.atan2(2 * (w * qz + qx * qy), 1 - 2 * (qy * qy + qz * qz))
    R_est0 = quat_to_rot(veh.att_q or q0)
    for d_ in ders:
        d_.set_reference(R_est0)
    start = vel = None
    if kind in ("threat", "flyby"):
        u = unit_from_az_el(trial["az_deg"] + math.degrees(yaw), trial["el_deg"])
        start = p0 + trial["dist_m"] * u
        perp = np.cross(u, np.array([0.0, 0.0, 1.0])); perp /= np.linalg.norm(perp)
        up = np.cross(perp, u)
        if kind == "threat":
            ang = rng.uniform(0, 2 * math.pi); mag = trial["aim_err_m"]
        else:
            ang = 0.0 if rng.random() < 0.5 else math.pi; mag = trial["miss_m"]
        target = p0 + mag * (math.cos(ang) * perp + math.sin(ang) * up)
        vel = (target - start) / np.linalg.norm(target - start) * trial["speed_mps"]
        duration = trial["dist_m"] / trial["speed_mps"] + 1.0
        for c, m in zip(cams, mounts):
            c.prime(world_to_cam(start, p0, q0, m), R_OBJ)
    else:
        duration = trial["duration_s"]
    t0 = time.perf_counter()
    trig = None
    escape_until = 0.0
    acc = None
    last_sp = 0.0
    min_d, t_min = float("inf"), None
    prev_rel = None
    rows = []
    k = 0
    resp = None
    v_trig = None
    max_tau_seen = 0.0
    while True:
        ls = time.perf_counter()
        t = ls - t0
        if t > duration:
            break
        veh.poll()
        p_d, v_d, q_d = veh.pose_now()
        ts = int(t * 1e6)
        if start is not None:
            p_o = start + vel * t
            rel = p_o - p_d
            d = seg_min_dist(prev_rel, rel) if prev_rel is not None else float(np.linalg.norm(rel))
            prev_rel = rel
            if d < min_d:
                min_d, t_min = d, t
            p_cams = [world_to_cam(p_o, p_d, q_d, m) for m in mounts]
            d_now = float(np.linalg.norm(rel))
        else:
            p_cams, d_now = [None] * len(cams), float("nan")
        R_est = quat_to_rot(veh.att_q or q_d)
        now_us = ts + int(LOOP_DT * 1e6)
        outs = []
        compute_us = 0.0
        for j, (c, ff, der, buf) in enumerate(zip(cams, ffs, ders, bufs)):
            ev = c.step(p_cams[j], R_OBJ, ts + 1, int(LOOP_DT * 1e6))
            if j == 0 and len(ev):
                gev = baf.apply(ev)
                if len(gev):
                    gf.feed_raw_spikes(gev.tobytes(), raw_w=1280, raw_h=720)
            buf.append(der.apply(ev, R_est))
            while buf and (len(buf[0]) == 0 or int(buf[0]["timestamp_us"].max()) + LATENCY_US <= now_us):
                te = time.perf_counter(); ff.feed(buf.pop(0)); compute_us += (time.perf_counter() - te) * 1e6
            te = time.perf_counter(); outs.append(ff.eval(now_us)); compute_us += (time.perf_counter() - te) * 1e6
        te = time.perf_counter()
        dec = gf.step_eval(now_us)
        compute_us += (time.perf_counter() - te) * 1e6  # detection compute only (feed + eval incl. ctypes), not event synthesis
        armed = [o for o in outs if o["armed"]]
        # GF (near-field) decisions count only while a far-field track shows significant expansion
        gf_ok = dec.triggered and any(o["t_stat"] > 4.0 and 0.0 < o["tau_s"] < 2.0 for o in outs)
        if trig is None and (armed or gf_ok):
            src = "farfield" if armed else "gf"
            if armed:
                o = armed[0]
                climb = not (o["miss_dir_y"] < -0.5 and o["miss_radii"] > 1.0)
            else:
                o = outs[0]
                climb = dec.vector.y >= 0.0
            trig = dict(t=t, dist=d_now, src=src, tau=o["tau_s"], miss=o["miss_radii"], climb=int(climb),
                        cam=outs.index(o) if armed else 0, compute_us=compute_us)
            if arm == "reflex":
                acc = accel_sp(climb, args.accel)
                escape_until = t + args.escape_s
                v_trig = v_d.copy()
        if arm == "reflex" and acc is not None and t < escape_until:
            if time.perf_counter() - last_sp > 0.004 or last_sp == 0.0:
                veh.m.mav.set_position_target_local_ned_send(0, veh.tsys, 1, 1, 3135, 0, 0, 0, 0, 0, 0, *acc, 0, 0)
                last_sp = time.perf_counter()
            if resp is None and veh.gt is not None and v_trig is not None:
                if abs(float(veh.gt["v"][2] - v_trig[2])) >= 0.2:
                    resp = t - trig["t"]
        else:
            if acc is not None and t >= escape_until and veh.local is not None and escape_until > 0:
                veh.hover_sp = tuple(veh.local); escape_until = -1.0
            veh.send_hover()
        for o in outs:
            if o["tau_s"] == o["tau_s"] and o["tau_s"] < 1e6:
                max_tau_seen = max(max_tau_seen, 0.0)
        if k % 5 == 0:
            rows.append([round(t, 4), *np.round(p_d, 3), *(np.round(p_o, 3) if start is not None else ["", "", ""]),
                         round(outs[0]["tau_s"], 3), round(min(o["tau_s"] for o in outs[1:]), 3), round(outs[0]["r_px"], 2),
                         round(max(o["r_px"] for o in outs[1:]), 2), int(outs[0]["armed"]), int(any(o["armed"] for o in outs[1:])), int(dec.triggered), round(compute_us, 1)])
        k += 1
        sl = LOOP_DT - (time.perf_counter() - ls)
        if sl > 0:
            time.sleep(sl)
    loop_hz = k / max(1e-6, time.perf_counter() - t0)
    res = {kk: v for kk, v in trial.items()}
    v = trial.get("speed_mps")
    res.update(settled=int(settled), triggered=int(trig is not None),
               trigger_src=trig["src"] if trig else "", trigger_cam=trig["cam"] if trig else "", rig=args.rig,
               trigger_t_s=round(trig["t"], 4) if trig else "",
               trigger_dist_m=round(trig["dist"], 3) if trig and start is not None else "",
               trigger_ttc_ms=round((trig["dist"] - HIT_DIST) / v * 1e3, 1) if trig and start is not None else "",
               tau_at_trigger_s=round(trig["tau"], 3) if trig else "", miss_radii=round(trig["miss"], 2) if trig else "",
               escape_climb=trig["climb"] if trig else "", compute_us=round(trig["compute_us"], 1) if trig else "",
               response_ms=round(resp * 1e3, 1) if resp is not None else "",
               min_dist_m=round(min_d, 3) if start is not None else "", t_min_s=round(t_min, 3) if t_min is not None else "",
               hit=int(min_d < HIT_DIST) if start is not None else "", loop_hz=round(loop_hz, 1))
    os.makedirs(os.path.join(out_dir, "traces"), exist_ok=True)
    with gzip.open(os.path.join(out_dir, "traces", f"{trial['id']}.csv.gz"), "wt", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["t", "drone_n", "drone_e", "drone_d", "obj_n", "obj_e", "obj_d", "tau_8mm", "tau_25mm_min",
                     "r_8mm", "r_25mm_max", "armed_8mm", "armed_25mm_any", "gf_trig", "compute_us"])
        wr.writerows(rows)
    return res


TIER_DIST = {6: (15, 30), 10: (15, 30), 14: (15, 30), 20: (15, 30), 30: (30, 60), 60: (80, 150), 90: (80, 150), 120: (80, 150)}


def build_plan(args, rng):
    plan = []
    tiers = [int(x) for x in args.tiers.split(",")]
    for v in tiers:
        for arm, n in (("reflex", args.n_reflex), ("baseline", args.n_baseline)):
            for _ in range(n):
                lo, hi = TIER_DIST[v]
                plan.append(dict(kind="threat", arm=arm, speed_mps=float(v), dist_m=round(rng.uniform(lo, hi), 2),
                                 az_deg=round(rng.uniform(-15, 15), 2), el_deg=round(rng.uniform(-8, 8), 2),
                                 aim_err_m=round(rng.uniform(0, 0.15), 3)))
    for _ in range(args.controls):
        plan.append(dict(kind="no_object", arm="reflex", duration_s=8.0))
        plan.append(dict(kind="noise", arm="reflex", duration_s=8.0, noise_hz=args.noise_control_hz))
        v = float(rng.choice(tiers))
        lo, hi = TIER_DIST[int(v)]
        plan.append(dict(kind="flyby", arm="reflex", speed_mps=v, dist_m=round(rng.uniform(lo, hi), 2),
                         az_deg=round(rng.uniform(-15, 15), 2), el_deg=round(rng.uniform(-8, 8), 2),
                         miss_m=round(rng.uniform(2.5, 6.0), 2)))
    rng.shuffle(plan)
    for i, p in enumerate(plan):
        p["id"] = f"{args.shard}-{i:04d}"
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", type=int, default=0)
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "sim_v3"))
    ap.add_argument("--tiers", default="6,10,14,20,30,60,90,120")
    ap.add_argument("--n-reflex", type=int, default=20)
    ap.add_argument("--n-baseline", type=int, default=4)
    ap.add_argument("--controls", type=int, default=20)
    ap.add_argument("--noise-hz", type=float, default=0.1)
    ap.add_argument("--noise-control-hz", type=float, default=1.0)
    ap.add_argument("--accel", type=float, default=20.0)
    ap.add_argument("--escape-s", type=float, default=1.2)
    ap.add_argument("--settle-min", type=float, default=3.0)
    ap.add_argument("--settle-max", type=float, default=25.0)
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--shard", default="s0")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--rig", default="dual", choices=sorted(RIGS))
    args = ap.parse_args()
    rng = random.Random(args.seed)
    plan = build_plan(args, rng)
    if args.limit:
        plan = plan[: args.limit]
    out_dir = os.path.join(args.out, args.shard)
    os.makedirs(out_dir, exist_ok=True)
    print(f"{len(plan)} trials planned", flush=True)
    veh = V3Vehicle(f"udpin:0.0.0.0:{14540 + args.instance}")
    veh.wait(2.0)
    veh.arm_offboard()
    veh.wait(6.0)
    results = []
    t_start = time.time()
    for n, tr in enumerate(plan):
        res = run_trial(veh, tr, rng, args, out_dir)
        results.append(res)
        print(f"[{n + 1}/{len(plan)}] {res['kind']:9s} {res['arm']:8s} v={res.get('speed_mps', '')} trig={res['triggered']} "
              f"src={res['trigger_src']} ttc={res['trigger_ttc_ms']} resp={res['response_ms']} min={res['min_dist_m']} "
              f"hit={res['hit']} loop={res['loop_hz']}Hz", flush=True)
        keys = sorted({k for r in results for k in r})
        with open(os.path.join(out_dir, "trials.csv"), "w", newline="") as f:
            wr = csv.DictWriter(f, fieldnames=keys); wr.writeheader(); wr.writerows(results)
    with open(os.path.join(out_dir, "campaign_meta.json"), "w") as f:
        json.dump(dict(started_unix=t_start, finished_unix=time.time(), args=vars(args), rig=RIGS[args.rig],
                       ff_cfg=FF_CFG, gf_preset=GF_PRESET, baf_us=BAF_US, gf_gate='t_stat>4 and 0<tau<2 s on either camera', latency_us=LATENCY_US, loop_dt=LOOP_DT,
                       r_obj=R_OBJ, hit_dist=HIT_DIST), f, indent=2)


if __name__ == "__main__":
    main()
