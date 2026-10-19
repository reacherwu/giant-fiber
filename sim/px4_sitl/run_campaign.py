#!/usr/bin/env python3
"""Incoming-object campaign against PX4 SITL (SIH quadrotor), with the GiantFiber reflex in the loop.

Each trial:
  1. The vehicle hovers in offboard mode (position setpoints, 50 Hz) until it is settled.
  2. A sphere (radius R_OBJ) is launched in a straight line (no gravity, constant speed) from a
     point D metres away, inside the forward camera's field of view, aimed at the vehicle's
     ground-truth position at launch time plus a random aim error.
  3. A loop at ~500 Hz synthesizes camera events from the object geometry and the vehicle's
     ground-truth pose (PX4 HIL_STATE_QUATERNION, from SIH ground truth), feeds them to the Rust
     engine and, depending on the arm:
        reflex   : on an escape decision streams the bridge's acceleration setpoint
                   (SET_POSITION_TARGET_LOCAL_NED, encoded by giantfiber's own MAVLink codec)
        baseline : engine runs and is logged, but no command is sent (vehicle keeps hovering)
        legacy   : on an escape decision sends the original single SET_ATTITUDE_TARGET packet
  4. Outcome from ground truth: minimum centre distance, hit if < R_OBJ + R_VEHICLE.

Control trials (no threat): `no_object` (hover, sensor noise only) and `flyby` (object passes
the vehicle at 2.5-4 m lateral distance). Any escape decision there is a false trigger.

Requires PX4 SITL running: HEADLESS=1 PX4_SIM_MODEL=sihsim_quadx PX4_SYS_AUTOSTART=10040 build/px4_sitl_default/bin/px4 -d
"""
import argparse
import csv
import gzip
import json
import math
import os
import random
import sys
import time

import numpy as np
from pymavlink import mavutil

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, ROOT)
from giantfiber import ReflexConfig  # noqa: E402
from giantfiber.integrations.px4_mavlink import PX4ReflexBridge, PX4BridgeConfig  # noqa: E402
from giantfiber.sim.events import EventCamera, world_to_cam  # noqa: E402

R_OBJ = 0.15        # object radius (m)
R_VEHICLE = 0.25    # vehicle radius used for the collision test (m)
HIT_DIST = R_OBJ + R_VEHICLE
HOVER_ALT = 10.0
LOOP_DT = 0.002
EARTH_R = 6378137.0

PRESETS = {
    # Engine defaults (fly-scale angular thresholds, 90 deg camera)
    "fly": dict(),
    # Same circuit, thresholds scaled down for a 60 deg camera so it fires earlier (documented in README)
    "drone": dict(fov_deg=60.0, lplc2_half_size_deg=4.0, lc4_half_velocity_dps=20.0),
}


class Vehicle:
    def __init__(self, url):
        self.m = mavutil.mavlink_connection(url, source_system=255, source_component=191)
        self.m.wait_heartbeat(timeout=30)
        self.ref = None
        self.gt = None          # dict(p, v, q, t)
        self.gt_samples = 0
        self.local = None       # EKF local NED (x, y, z)
        self.armed = False
        self.hover_sp = (0.0, 0.0, -HOVER_ALT)
        self.last_sp = 0.0
        for mid, hz in [(115, 250), (32, 50), (0, 2)]:
            self.m.mav.command_long_send(1, 1, mavutil.mavlink.MAV_CMD_SET_MESSAGE_INTERVAL, 0, mid, int(1e6 / hz), 0, 0, 0, 0, 0)

    def poll(self):
        now = time.perf_counter()
        while True:
            msg = self.m.recv_match(blocking=False)
            if msg is None:
                return
            ty = msg.get_type()
            if ty == "HIL_STATE_QUATERNION":
                lat, lon, alt = msg.lat * 1e-7, msg.lon * 1e-7, msg.alt * 1e-3
                if self.ref is None:
                    self.ref = (lat, lon, alt)
                n = math.radians(lat - self.ref[0]) * EARTH_R
                e = math.radians(lon - self.ref[1]) * EARTH_R * math.cos(math.radians(self.ref[0]))
                d = -(alt - self.ref[2])
                self.gt = dict(p=np.array([n, e, d]), v=np.array([msg.vx, msg.vy, msg.vz]) * 0.01,
                               q=tuple(msg.attitude_quaternion), t=now)
                self.gt_samples += 1
            elif ty == "LOCAL_POSITION_NED":
                self.local = (msg.x, msg.y, msg.z)
            elif ty == "HEARTBEAT" and msg.get_srcComponent() == 1:
                self.armed = bool(msg.base_mode & 128)

    def pose_now(self):
        g = self.gt
        dt = time.perf_counter() - g["t"]
        return g["p"] + g["v"] * dt, g["v"], g["q"]

    def send_hover(self, force=False):
        now = time.perf_counter()
        if force or now - self.last_sp > 0.02:
            x, y, z = self.hover_sp
            self.m.mav.set_position_target_local_ned_send(0, 1, 1, 1, 0b110111111000, x, y, z, 0, 0, 0, 0, 0, 0, 0, 0)
            self.last_sp = now

    def arm_offboard(self):
        t0 = time.perf_counter()
        while not self.armed:
            for _ in range(10):
                self.send_hover(force=True)
                self.poll()
                time.sleep(0.02)
            self.m.mav.command_long_send(1, 1, 176, 0, 1, 6, 0, 0, 0, 0, 0)  # offboard
            self.m.mav.command_long_send(1, 1, 400, 0, 1, 0, 0, 0, 0, 0, 0)  # arm
            if time.perf_counter() - t0 > 60:
                raise RuntimeError("could not arm")
        print("armed + offboard", flush=True)

    def wait(self, seconds):
        t_end = time.perf_counter() + seconds
        while time.perf_counter() < t_end:
            self.poll()
            self.send_hover()
            time.sleep(0.005)

    def settle(self, min_s=3.0, max_s=20.0):
        t0 = time.perf_counter()
        still_since = None
        while True:
            self.poll()
            self.send_hover()
            now = time.perf_counter()
            if self.gt is not None and self.local is not None:
                speed = float(np.linalg.norm(self.gt["v"]))
                err = math.dist(self.local, self.hover_sp)
                if speed < 0.15 and err < 0.3:
                    still_since = still_since or now
                else:
                    still_since = None
                if now - t0 > min_s and still_since and now - still_since > 1.0:
                    return True
            if now - t0 > max_s:
                return False
            time.sleep(0.005)


def unit_from_az_el(az_deg, el_deg):
    az, el = math.radians(az_deg), math.radians(el_deg)
    # NED with vehicle heading north: az to the right (east), el up (negative down)
    return np.array([math.cos(el) * math.cos(az), math.cos(el) * math.sin(az), -math.sin(el)])


def run_trial(veh, arm, cfg_name, trial, out_dir, rng, noise_hz, trace=True):
    cfg = ReflexConfig(**PRESETS[cfg_name])
    bridge = PX4ReflexBridge(PX4BridgeConfig(reflex_config=cfg))
    cam = EventCamera(fov_deg=cfg.fov_deg, noise_rate_hz=noise_hz, seed=rng.randrange(1 << 30))
    settled = veh.settle()
    p_launch_drone, _, q0 = veh.pose_now()
    # yaw from quaternion (w, x, y, z)
    w, x, y, z = q0
    yaw = math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))
    bridge.yaw_rad = yaw

    kind = trial["kind"]
    if kind in ("threat", "flyby"):
        u = unit_from_az_el(trial["az_deg"] + math.degrees(yaw), trial["el_deg"])
        start = p_launch_drone + trial["dist_m"] * u
        # random aim error / fly-by offset perpendicular to the line of sight
        perp = np.cross(u, np.array([0.0, 0.0, 1.0]))
        perp /= np.linalg.norm(perp)
        up = np.cross(perp, u)
        ang = rng.uniform(0, 2 * math.pi)
        if kind == "threat":
            mag = trial["aim_err_m"]
        else:
            mag = trial["miss_m"]
            ang = 0.0 if rng.random() < 0.5 else math.pi  # pass left or right
        target = p_launch_drone + mag * (math.cos(ang) * perp + math.sin(ang) * up)
        vel = (target - start) / np.linalg.norm(target - start) * trial["speed_mps"]
        duration = trial["dist_m"] / trial["speed_mps"] + 1.0
    else:
        start = vel = None
        duration = trial["duration_s"]

    rows = []
    t0 = time.perf_counter()
    trig = []
    resp = None
    min_d, t_min = float("inf"), None
    send_t = None
    acc_dir = None
    v_at_trig = None
    evasion_sent = 0
    last_ev_sp = 0.0
    k = 0
    while True:
        loop_start = time.perf_counter()
        t = loop_start - t0
        if t > duration:
            break
        veh.poll()
        p_d, v_d, q_d = veh.pose_now()
        if start is not None:
            p_o = start + vel * t
            d = float(np.linalg.norm(p_o - p_d))
            if d < min_d:
                min_d, t_min = d, t
            p_cam = world_to_cam(p_o, p_d, q_d)
        else:
            p_o, d, p_cam = None, float("nan"), None
        ts = int(t * 1e6)
        ev = cam.step(p_cam, R_OBJ, ts + 1, int(LOOP_DT * 1e6))
        if len(ev):
            bridge.coprocessor.feed_raw_spikes(ev.tobytes())
        t_eval0 = time.perf_counter()
        if arm == "reflex":
            dec = bridge.step_eval(ts + int(LOOP_DT * 1e6), wall_time_s=t_eval0)
        else:
            dec = bridge.coprocessor.step_eval(ts + int(LOOP_DT * 1e6))
            bridge.last_decision = dec
        if dec.triggered:
            if arm == "reflex":
                from giantfiber.integrations.px4_mavlink import escape_acceleration_ned
                acc = np.array(escape_acceleration_ned(dec, yaw, bridge.config))
                veh.m.write(bridge.evasion_packet())
                send_t = send_t or time.perf_counter()
                evasion_sent += 1
                acc_dir = acc / (np.linalg.norm(acc) or 1.0)
                v_at_trig = v_at_trig if v_at_trig is not None else v_d.copy()
            elif arm == "legacy":
                veh.m.write(bridge.dispatch_evasion_override(dec))
                send_t = send_t or time.perf_counter()
            closing = trial.get("speed_mps", float("nan"))
            trig.append(dict(t=t, mode=int(dec.escape_mode), action=dec.action.name,
                             dist=d, ttc_ms=(d - HIT_DIST) / closing * 1e3 if start is not None else float("nan"),
                             compute_us=dec.compute_latency_us,
                             detect_to_send_us=(time.perf_counter() - t_eval0) * 1e6))
        # Command streaming
        if arm == "reflex" and bridge.evasion_active(time.perf_counter()):
            if time.perf_counter() - last_ev_sp > 0.01:
                veh.m.write(bridge.evasion_packet())
                last_ev_sp = time.perf_counter()
        else:
            if arm == "reflex" and evasion_sent and veh.local is not None and veh.hover_sp == trial["_hover"]:
                veh.hover_sp = tuple(veh.local)  # hold where the escape ended
            veh.send_hover()
        # Response detection on ground truth
        if acc_dir is not None and resp is None and veh.gt is not None and veh.gt["t"] >= send_t:
            dv = float(np.dot(veh.gt["v"] - v_at_trig, acc_dir))
            if dv >= 0.2:
                resp = veh.gt["t"] - t0
        if trace and k % 5 == 0:
            st = bridge.coprocessor.debug_state()
            rows.append([round(t, 4), *np.round(p_d, 4), *(np.round(p_o, 4) if p_o is not None else ["", "", ""]),
                         len(ev), round(st["theta_deg"], 3), round(st["theta_dot_dps"], 1),
                         round(max(st["v_gf"]), 4), round(max(st["v_nongf"]), 4), st["escape_mode"]])
        k += 1
        sl = LOOP_DT - (time.perf_counter() - loop_start)
        if sl > 0:
            time.sleep(sl)
    loop_hz = k / max(1e-6, time.perf_counter() - t0)
    veh.hover_sp = trial["_hover"]
    bridge.close()

    first = trig[0] if trig else None
    res = dict(trial)
    res.pop("_hover", None)
    res.update(
        arm=arm, config=cfg_name, settled=settled, noise_hz=noise_hz,
        triggered=int(bool(trig)), n_decisions=len(trig),
        first_mode={0: "", 1: "long", 2: "short"}[first["mode"]] if first else "",
        modes="+".join({1: "L", 2: "S"}[x["mode"]] for x in trig),
        trigger_t_s=round(first["t"], 4) if first else "",
        trigger_dist_m=round(first["dist"], 3) if first else "",
        trigger_ttc_ms=round(first["ttc_ms"], 1) if first and start is not None else "",
        compute_latency_us=first["compute_us"] if first else "",
        detect_to_send_us=round(first["detect_to_send_us"], 1) if first else "",
        response_latency_ms=round((resp - first["t"]) * 1e3, 1) if (first and resp) else "",
        min_dist_m=round(min_d, 3) if start is not None else "",
        t_min_s=round(t_min, 3) if t_min is not None else "",
        hit=int(min_d < HIT_DIST) if start is not None else "",
        clearance_m=round(min_d - HIT_DIST, 3) if start is not None else "",
        loop_hz=round(loop_hz, 1),
    )
    if trace:
        os.makedirs(os.path.join(out_dir, "traces"), exist_ok=True)
        with gzip.open(os.path.join(out_dir, "traces", f"{trial['id']}.csv.gz"), "wt", newline="") as f:
            w = csv.writer(f)
            w.writerow(["t", "drone_n", "drone_e", "drone_d", "obj_n", "obj_e", "obj_d", "events",
                        "theta_deg", "theta_dot_dps", "v_gf", "v_nongf", "escape_mode"])
            w.writerows(rows)
    return res


def build_plan(args, rng):
    plan = []
    i = 0
    speeds = [float(x) for x in args.speeds.split(",")]
    azs = [float(x) for x in args.az.split(",")]
    els = [float(x) for x in args.el.split(",")]
    dists = [float(x) for x in args.dist.split(",")]
    for arm, cfg_name, reps in [("reflex", "drone", args.reps), ("baseline", "drone", args.baseline_reps),
                                ("reflex", "fly", args.fly_reps), ("legacy", "drone", args.legacy_reps)]:
        for r in range(reps):
            for v in speeds:
                for az in azs:
                    for el in els:
                        for dist in dists:
                            plan.append(dict(id=f"{i:04d}", kind="threat", arm=arm, cfg=cfg_name, speed_mps=v,
                                             az_deg=az, el_deg=el, dist_m=dist,
                                             aim_err_m=round(math.sqrt(rng.random()) * args.aim_err, 3)))
                            i += 1
    for r in range(args.controls):
        plan.append(dict(id=f"{i:04d}", kind="no_object", arm="reflex", cfg="drone", duration_s=8.0)); i += 1
        plan.append(dict(id=f"{i:04d}", kind="flyby", arm="reflex", cfg="drone", speed_mps=rng.choice(speeds),
                         az_deg=rng.choice(azs), el_deg=0.0, dist_m=rng.choice(dists),
                         miss_m=round(rng.uniform(2.5, 4.0), 2))); i += 1
    rng.shuffle(plan)
    return plan


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="udpin:0.0.0.0:14540")
    ap.add_argument("--out", default=os.path.join(ROOT, "docs", "sim"))
    ap.add_argument("--speeds", default="6,10,14")
    ap.add_argument("--az", default="-15,0,15")
    ap.add_argument("--el", default="0,8")
    ap.add_argument("--dist", default="15,25")
    ap.add_argument("--aim-err", type=float, default=0.1)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--baseline-reps", type=int, default=2)
    ap.add_argument("--fly-reps", type=int, default=1)
    ap.add_argument("--legacy-reps", type=int, default=1)
    ap.add_argument("--controls", type=int, default=30)
    ap.add_argument("--noise-hz", type=float, default=0.3)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--limit", type=int, default=0, help="run only the first N planned trials (smoke test)")
    args = ap.parse_args()
    rng = random.Random(args.seed)
    os.makedirs(args.out, exist_ok=True)
    plan = build_plan(args, rng)
    if args.limit:
        plan = plan[: args.limit]
    print(f"{len(plan)} trials planned", flush=True)
    veh = Vehicle(args.url)
    veh.wait(2.0)
    veh.arm_offboard()
    veh.wait(8.0)
    out_csv = os.path.join(args.out, "trials.csv")
    results = []
    t_start = time.time()
    for n, tr in enumerate(plan):
        tr["_hover"] = (0.0, 0.0, -HOVER_ALT)
        arm, cfg_name = tr.pop("arm"), tr.pop("cfg")
        res = run_trial(veh, arm, cfg_name, tr, args.out, rng, args.noise_hz)
        results.append(res)
        print(f"[{n + 1}/{len(plan)}] {res['kind']:9s} {arm:8s} {cfg_name:5s} v={res.get('speed_mps', '')} "
              f"trig={res['triggered']} mode={res['first_mode']:5s} ttc={res['trigger_ttc_ms']} resp={res['response_latency_ms']} "
              f"min={res['min_dist_m']} hit={res['hit']} loop={res['loop_hz']}Hz", flush=True)
        keys = sorted({k for r in results for k in r})
        with open(out_csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=keys)
            w.writeheader()
            w.writerows(results)
    meta = dict(started_unix=t_start, finished_unix=time.time(), args=vars(args), r_obj=R_OBJ, r_vehicle=R_VEHICLE,
                hover_alt=HOVER_ALT, loop_dt=LOOP_DT, presets=PRESETS, gt_samples=veh.gt_samples)
    with open(os.path.join(args.out, "campaign_meta.json"), "w") as f:
        json.dump(meta, f, indent=2)


if __name__ == "__main__":
    main()
