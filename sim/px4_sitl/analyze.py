#!/usr/bin/env python3
"""Analyse a PX4 SITL campaign produced by run_campaign.py.

Inputs : <dir>/trials.csv, <dir>/traces/*.csv.gz, optional PX4 .ulg of the same session
Outputs: <dir>/summary.json, <dir>/summary_by_speed.csv, <dir>/*.png, <dir>/evasion.gif

Every number written here is computed from the input files; nothing is hard-coded.
"""
import argparse
import csv
import glob
import gzip
import json
import math
import os

import numpy as np
import pandas as pd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import animation  # noqa: E402

HIT_DIST = 0.40  # must match run_campaign.R_OBJ + R_VEHICLE


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def q(x, p):
    x = pd.to_numeric(x, errors="coerce").dropna()
    return float(np.percentile(x, p)) if len(x) else None


def stats(x):
    x = pd.to_numeric(x, errors="coerce").dropna()
    if not len(x):
        return dict(n=0)
    return dict(n=int(len(x)), mean=round(float(x.mean()), 3), median=round(float(x.median()), 3),
                p05=round(q(x, 5), 3), p95=round(q(x, 95), 3), min=round(float(x.min()), 3), max=round(float(x.max()), 3))


def group_label(arm, cfg):
    return {("reflex", "drone"): "reflex (drone preset)", ("baseline", "drone"): "no reflex (baseline)",
            ("reflex", "fly"): "reflex (fly preset)", ("legacy", "drone"): "legacy attitude cmd"}.get((arm, cfg), f"{arm}/{cfg}")


GROUP_ORDER = [("baseline", "drone"), ("reflex", "drone"), ("reflex", "fly"), ("legacy", "drone")]


def summarize(df):
    out = {}
    thr = df[df.kind == "threat"]
    for arm, cfg in GROUP_ORDER:
        g = thr[(thr.arm == arm) & (thr.config == cfg)]
        if not len(g):
            continue
        n = len(g)
        hits = int(g.hit.sum())
        trig = int(g.triggered.sum())
        tg = g[g.triggered == 1]
        out[group_label(arm, cfg)] = dict(
            arm=arm, config=cfg, n_trials=n, n_unsettled=int((~g.settled.astype(bool)).sum()),
            hits=hits, hit_rate=round(hits / n, 4), hit_rate_ci95=[round(v, 4) for v in wilson(hits, n)],
            evasion_success_rate=round(1 - hits / n, 4),
            evasion_success_ci95=[round(1 - v, 4) for v in reversed(wilson(hits, n))],
            detection_rate=round(trig / n, 4),
            first_mode_short=int((tg.first_mode == "short").sum()), first_mode_long=int((tg.first_mode == "long").sum()),
            reached_short=int(tg.modes.fillna("").str.contains("S").sum()),
            min_dist_m=stats(g.min_dist_m), clearance_m=stats(g.clearance_m),
            trigger_ttc_ms=stats(tg.trigger_ttc_ms), trigger_dist_m=stats(tg.trigger_dist_m),
            compute_latency_us=stats(tg.compute_latency_us), detect_to_send_us=stats(tg.detect_to_send_us),
            response_latency_ms=stats(tg.response_latency_ms),
            hits_given_triggered=int(tg.hit.sum()), n_triggered=trig,
            hits_given_not_triggered=int(g[g.triggered == 0].hit.sum()),
        )
    ctrl = {}
    for kind in ("no_object", "flyby"):
        g = df[df.kind == kind]
        if not len(g):
            continue
        k = int(g.triggered.sum())
        d = dict(n_trials=len(g), false_triggers=k, false_trigger_rate=round(k / len(g), 4),
                 false_trigger_rate_ci95=[round(v, 4) for v in wilson(k, len(g))])
        if kind == "no_object":
            dur = float(pd.to_numeric(g.duration_s, errors="coerce").sum())
            d["observed_seconds"] = dur
            d["false_triggers_per_hour"] = round(k / dur * 3600, 2) if dur else None
        else:
            d["miss_distance_m"] = stats(g.miss_m)
        ctrl[kind] = d
    out["controls"] = ctrl
    return out


def by_speed(df):
    rows = []
    thr = df[df.kind == "threat"]
    for arm, cfg in GROUP_ORDER:
        for v in sorted(thr.speed_mps.dropna().unique()):
            g = thr[(thr.arm == arm) & (thr.config == cfg) & (thr.speed_mps == v)]
            if not len(g):
                continue
            n, h = len(g), int(g.hit.sum())
            lo, hi = wilson(h, n)
            tg = g[g.triggered == 1]
            rows.append(dict(group=group_label(arm, cfg), speed_mps=v, n=n, hits=h, hit_rate=round(h / n, 4),
                             hit_ci_lo=round(lo, 4), hit_ci_hi=round(hi, 4),
                             detection_rate=round(len(tg) / n, 4),
                             median_trigger_ttc_ms=q(tg.trigger_ttc_ms, 50),
                             median_response_ms=q(tg.response_latency_ms, 50),
                             median_min_dist_m=q(g.min_dist_m, 50)))
    return pd.DataFrame(rows)


def plot_hit_rates(bs, path):
    fig, ax = plt.subplots(figsize=(8, 4.2))
    groups = [g for g in [group_label(*x) for x in GROUP_ORDER] if g in set(bs.group)]
    speeds = sorted(bs.speed_mps.unique())
    w = 0.8 / len(groups)
    for i, g in enumerate(groups):
        s = bs[bs.group == g].set_index("speed_mps").reindex(speeds)
        x = np.arange(len(speeds)) + (i - (len(groups) - 1) / 2) * w
        ax.bar(x, s.hit_rate * 100, w, label=g)
        ax.errorbar(x, s.hit_rate * 100, yerr=[(s.hit_rate - s.hit_ci_lo) * 100, (s.hit_ci_hi - s.hit_rate) * 100],
                    fmt="none", ecolor="k", capsize=3, lw=1)
        for xi, (n, h) in zip(x, zip(s.n, s.hits)):
            if pd.isna(n):
                continue
            ax.text(xi, 2, f"{int(h)}/{int(n)}", ha="center", va="bottom", fontsize=7, rotation=90, color="k")
    ax.set_xticks(np.arange(len(speeds)), [f"{v:g} m/s" for v in speeds])
    ax.set_ylabel("collision rate (%)  [Wilson 95% CI]")
    ax.set_ylim(0, 105)
    ax.set_title("PX4 SITL (SIH quad): collisions with an incoming 0.3 m sphere")
    ax.legend(fontsize=8, loc="upper left")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def plot_min_dist(df, path):
    thr = df[df.kind == "threat"]
    fig, ax = plt.subplots(figsize=(8, 4))
    data, labels = [], []
    for arm, cfg in GROUP_ORDER:
        g = thr[(thr.arm == arm) & (thr.config == cfg)]
        if len(g):
            data.append(pd.to_numeric(g.min_dist_m, errors="coerce").dropna().values)
            labels.append(f"{group_label(arm, cfg)}\n(n={len(g)})")
    rng = np.random.default_rng(0)
    for i, d in enumerate(data):
        ax.scatter(i + rng.uniform(-0.15, 0.15, len(d)), d, s=10, alpha=0.6)
    ax.boxplot(data, positions=range(len(data)), widths=0.5, showfliers=False)
    ax.axhline(HIT_DIST, color="r", ls="--", lw=1, label=f"collision threshold {HIT_DIST} m")
    ax.set_xticks(range(len(labels)), labels, fontsize=8)
    ax.set_ylabel("minimum centre distance (m)")
    ax.legend(fontsize=8)
    ax.set_title("Closest approach per trial (ground truth)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def plot_timing(df, path):
    thr = df[(df.kind == "threat") & (df.triggered == 1)]
    fig, axs = plt.subplots(1, 2, figsize=(10, 4))
    for arm, cfg in [("reflex", "drone"), ("baseline", "drone"), ("reflex", "fly")]:
        g = thr[(thr.arm == arm) & (thr.config == cfg)]
        if len(g):
            axs[0].scatter(g.speed_mps + {"baseline": -0.3, "reflex": 0.0}[arm] + (0.3 if cfg == "fly" else 0),
                           g.trigger_ttc_ms, s=12, alpha=0.6, label=group_label(arm, cfg))
    r = thr[thr.arm == "reflex"]
    resp = pd.to_numeric(r.response_latency_ms, errors="coerce")
    axs[0].axhline(float(resp.median()) if resp.notna().any() else 0, color="k", ls="--", lw=1,
                   label=f"median vehicle response {resp.median():.0f} ms")
    axs[0].set_xlabel("object speed (m/s)")
    axs[0].set_ylabel("time to contact at trigger (ms)")
    axs[0].legend(fontsize=7)
    axs[0].set_title("Warning time vs. speed")
    axs[1].hist(resp.dropna(), bins=20, color="tab:orange")
    axs[1].set_xlabel("trigger -> 0.2 m/s velocity change along escape direction (ms)")
    axs[1].set_ylabel("trials")
    axs[1].set_title("PX4 response latency (reflex arms)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def load_trace(d, tid):
    p = os.path.join(d, "traces", f"{tid}.csv.gz")
    if not os.path.exists(p):
        return None
    return pd.read_csv(p)


def pick_examples(df):
    thr = df[(df.kind == "threat")]
    ex = {}
    a = thr[(thr.arm == "reflex") & (thr.config == "drone") & (thr.triggered == 1) & (thr.hit == 0)]
    b = thr[(thr.arm == "reflex") & (thr.config == "drone") & (thr.hit == 1)]
    c = thr[(thr.arm == "baseline") & (thr.hit == 1)]
    for name, g in [("reflex_evaded", a), ("reflex_hit", b), ("baseline_hit", c)]:
        if len(g):
            ex[name] = g.sort_values("speed_mps").iloc[len(g) // 2].id
    return ex


def plot_trajectories(df, d, ex, path):
    fig, axs = plt.subplots(1, len(ex), figsize=(4.2 * len(ex), 4), squeeze=False)
    for ax, (name, tid) in zip(axs[0], ex.items()):
        tr = load_trace(d, tid)
        row = df[df.id == tid].iloc[0]
        if tr is None:
            continue
        # plot in a frame centred on the vehicle's position at t=0 (east/up)
        e0, d0 = tr.drone_e.iloc[0], tr.drone_d.iloc[0]
        n0 = tr.drone_n.iloc[0]
        ax.plot(tr.drone_e - e0, -(tr.drone_d - d0), "b-", label="vehicle")
        ob = tr.dropna(subset=["obj_e"])
        tmin = row.t_min_s
        near = ob[(ob.obj_n - n0).abs() < 4]
        ax.plot(near.obj_e - e0, -(near.obj_d - d0), "r.", ms=2, label="object (|dN|<4 m)")
        i = (tr.t - tmin).abs().idxmin()
        ax.add_patch(plt.Circle((tr.drone_e[i] - e0, -(tr.drone_d[i] - d0)), 0.25, fill=False, color="b"))
        if not pd.isna(tr.obj_e[i]):
            ax.add_patch(plt.Circle((tr.obj_e[i] - e0, -(tr.obj_d[i] - d0)), 0.15, fill=False, color="r"))
        ax.set_aspect("equal")
        ax.set_xlim(-2, 2)
        ax.set_ylim(-2, 2)
        ax.set_xlabel("east (m)")
        ax.set_ylabel("up (m)")
        ax.set_title(f"{name}: trial {tid}\n{row.speed_mps:g} m/s, min dist {row.min_dist_m:.2f} m, "
                     f"{'mode ' + row.first_mode if row.triggered else 'no trigger'}", fontsize=9)
        ax.legend(fontsize=7, loc="lower left")
    fig.suptitle("Ground-truth trajectories seen from behind the vehicle (circles: closest approach)", fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def make_gif(df, d, tid, path, fps=20):
    tr = load_trace(d, tid)
    row = df[df.id == tid].iloc[0]
    if tr is None:
        return False
    t_end = min(tr.t.iloc[-1], (row.t_min_s or tr.t.iloc[-1]) + 0.6)
    t_start = max(0.0, t_end - 2.2)
    tr = tr[(tr.t >= t_start) & (tr.t <= t_end)].reset_index(drop=True)
    n0, e0, d0 = tr.drone_n.iloc[0], tr.drone_e.iloc[0], tr.drone_d.iloc[0]
    fig = plt.figure(figsize=(9, 4.5))
    ax1 = fig.add_subplot(1, 2, 1)
    ax2 = fig.add_subplot(2, 2, 2)
    ax3 = fig.add_subplot(2, 2, 4)
    ax1.set_xlim(-3, 12)
    ax1.set_ylim(-3, 3)
    ax1.set_aspect("equal")
    ax1.set_xlabel("north (m)")
    ax1.set_ylabel("east (m)")
    ax1.set_title(f"trial {tid} (top view, PX4 SIH ground truth)", fontsize=9)
    veh, = ax1.plot([], [], "bo", ms=8)
    obj, = ax1.plot([], [], "ro", ms=5)
    vtrail, = ax1.plot([], [], "b-", lw=1)
    otrail, = ax1.plot([], [], "r-", lw=0.5)
    txt = ax1.text(-2.8, 2.5, "", fontsize=8)
    ax2.plot(tr.t, tr.theta_deg, "k-", lw=1)
    ax2.set_ylabel("θ est (deg)", fontsize=8)
    cur2 = ax2.axvline(tr.t.iloc[0], color="r")
    ax3.plot(tr.t, tr.v_gf, label="GF (DNp01)")
    ax3.plot(tr.t, tr.v_nongf, label="non-GF DNs")
    ax3.set_ylabel("membrane (norm.)", fontsize=8)
    ax3.set_xlabel("t (s)", fontsize=8)
    ax3.legend(fontsize=7)
    cur3 = ax3.axvline(tr.t.iloc[0], color="r")
    mode_name = {0: "-", 1: "LONG (non-GF)", 2: "SHORT (GF)"}

    def upd(i):
        r = tr.iloc[i]
        veh.set_data([r.drone_n - n0], [r.drone_e - e0])
        vtrail.set_data(tr.drone_n[: i + 1] - n0, tr.drone_e[: i + 1] - e0)
        if not pd.isna(r.obj_n):
            obj.set_data([r.obj_n - n0], [r.obj_e - e0])
            otrail.set_data(tr.obj_n[: i + 1] - n0, tr.obj_e[: i + 1] - e0)
        txt.set_text(f"t={r.t:.2f}s  escape mode: {mode_name.get(int(r.escape_mode), '?')}")
        cur2.set_xdata([r.t, r.t])
        cur3.set_xdata([r.t, r.t])
        return veh, obj, vtrail, otrail, txt, cur2, cur3

    step = max(1, int(round(len(tr) / (tr.t.iloc[-1] - tr.t.iloc[0]) / fps)))
    frames = list(range(0, len(tr), step))
    anim = animation.FuncAnimation(fig, upd, frames=frames, blit=False)
    fig.tight_layout()
    anim.save(path, writer=animation.PillowWriter(fps=fps // 2))  # rendered at half speed
    plt.close(fig)
    return True


def ulog_evasions(ulg_path, df, out_png, out_csv):
    """Independent check from PX4's own log: find acceleration-setpoint episodes (position fields NaN)
    in vehicle_local_position_setpoint and plot the ground-truth velocity response."""
    from pyulog import ULog
    u = ULog(ulg_path, ["vehicle_local_position_setpoint", "vehicle_local_position_groundtruth"])
    sp = u.get_dataset("vehicle_local_position_setpoint").data
    gt = u.get_dataset("vehicle_local_position_groundtruth").data
    t_sp = sp["timestamp"] * 1e-6
    accel_only = np.isnan(sp["x"]) & np.isfinite(sp["acceleration[1]"])
    idx = np.where(accel_only)[0]
    episodes = []
    for i in idx:
        if episodes and t_sp[i] - episodes[-1][1] < 2.0:
            episodes[-1][1] = t_sp[i]
            episodes[-1][2].append(i)
        else:
            episodes.append([t_sp[i], t_sp[i], [i]])
    t_gt = gt["timestamp"] * 1e-6
    rows = []
    fig, axs = plt.subplots(1, 2, figsize=(10, 4))
    for k, (ts, te, ii) in enumerate(episodes):
        a = np.array([[sp["acceleration[0]"][j], sp["acceleration[1]"][j], sp["acceleration[2]"][j]] for j in ii])
        a_mean = a.mean(axis=0)
        dirn = a_mean / (np.linalg.norm(a_mean) or 1)
        m = (t_gt >= ts - 0.3) & (t_gt <= ts + 1.2)
        v = np.stack([gt["vx"][m], gt["vy"][m], gt["vz"][m]], 1)
        v0 = v[np.argmin(np.abs(t_gt[m] - ts))]
        dv = (v - v0) @ dirn
        p = np.stack([gt["x"][m], gt["y"][m], gt["z"][m]], 1)
        p0 = p[np.argmin(np.abs(t_gt[m] - ts))]
        dp = (p - p0) @ dirn
        tt = t_gt[m] - ts
        axs[0].plot(tt, dv, lw=0.6, alpha=0.5)
        axs[1].plot(tt, dp, lw=0.6, alpha=0.5)
        after = tt > 0
        rows.append(dict(episode=k, t_start_s=round(ts, 3), logged_setpoints=len(ii),
                         accel_cmd_mps2=round(float(np.linalg.norm(a_mean)), 2),
                         dv_at_0p25s=round(float(np.interp(0.25, tt, dv)), 3),
                         dv_at_0p5s=round(float(np.interp(0.5, tt, dv)), 3),
                         disp_at_0p5s=round(float(np.interp(0.5, tt, dp)), 3),
                         t_dv_0p2=round(float(tt[after][np.argmax(dv[after] >= 0.2)]), 3) if (dv[after] >= 0.2).any() else None))
    for ax, lab in zip(axs, ["Δv along commanded direction (m/s)", "displacement along commanded direction (m)"]):
        ax.axvline(0, color="k", lw=0.8)
        ax.set_xlabel("time from first logged accel setpoint (s)")
        ax.set_ylabel(lab)
    axs[0].set_title(f"PX4 ulog: {len(episodes)} escape episodes", fontsize=9)
    fig.tight_layout()
    fig.savefig(out_png, dpi=120)
    plt.close(fig)
    pd.DataFrame(rows).to_csv(out_csv, index=False)
    n_cmd = int(((df.kind != "threat") | (df.arm == "reflex")).mul(df.triggered == 1).sum())
    return dict(ulog=os.path.basename(ulg_path), episodes_found=len(episodes),
                reflex_trials_with_trigger=n_cmd,
                setpoint_log_rate_hz=round(len(t_sp) / (t_sp[-1] - t_sp[0]), 1),
                dv_at_0p5s=stats(pd.Series([r["dv_at_0p5s"] for r in rows])),
                disp_at_0p5s=stats(pd.Series([r["disp_at_0p5s"] for r in rows])),
                t_dv_0p2_s=stats(pd.Series([r["t_dv_0p2"] for r in rows])))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(os.path.dirname(__file__), "..", "..", "docs", "sim"))
    ap.add_argument("--ulog", default=None, help="PX4 .ulg of the campaign session (optional)")
    ap.add_argument("--no-gif", action="store_true")
    args = ap.parse_args()
    d = os.path.abspath(args.dir)
    df = pd.read_csv(os.path.join(d, "trials.csv"), dtype={"id": str})
    df["settled"] = df.settled.astype(str).str.lower() == "true"
    s = summarize(df)
    bs = by_speed(df)
    bs.to_csv(os.path.join(d, "summary_by_speed.csv"), index=False)
    plot_hit_rates(bs, os.path.join(d, "collision_rate_by_speed.png"))
    plot_min_dist(df, os.path.join(d, "min_distance.png"))
    plot_timing(df, os.path.join(d, "timing.png"))
    ex = pick_examples(df)
    if ex:
        plot_trajectories(df, d, ex, os.path.join(d, "trajectories.png"))
        s["example_trials"] = ex
        if not args.no_gif and "reflex_evaded" in ex:
            make_gif(df, d, ex["reflex_evaded"], os.path.join(d, "evasion.gif"))
        if not args.no_gif and "baseline_hit" in ex:
            make_gif(df, d, ex["baseline_hit"], os.path.join(d, "baseline_hit.gif"))
    if args.ulog:
        s["ulog_check"] = ulog_evasions(args.ulog, df, os.path.join(d, "ulog_escape_response.png"),
                                        os.path.join(d, "ulog_escape_episodes.csv"))
    meta = os.path.join(d, "campaign_meta.json")
    if os.path.exists(meta):
        s["campaign"] = json.load(open(meta))
    with open(os.path.join(d, "summary.json"), "w") as f:
        json.dump(s, f, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print(json.dumps({k: v for k, v in s.items() if k != "campaign"}, indent=1, default=str))


if __name__ == "__main__":
    main()
