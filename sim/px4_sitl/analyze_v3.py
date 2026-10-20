#!/usr/bin/env python3
"""Analyse campaign v3 (docs/sim_v3/s*/trials.csv). Every number is computed from the CSV files."""
import argparse, glob, gzip, json, math, os
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib import animation  # noqa: E402

NARROW_HALF = (math.degrees(math.atan(1280 * 4.86e-3 / 2 / 25)), math.degrees(math.atan(720 * 4.86e-3 / 2 / 25)))


def wilson(k, n, z=1.96):
    if n == 0:
        return (float("nan"), float("nan"))
    p = k / n; d = 1 + z * z / n; c = p + z * z / (2 * n)
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return (max(0.0, (c - h) / d), min(1.0, (c + h) / d))


def landed_cutoff(shard_dir, ids):
    """Index of the first trial at which PX4's battery failsafe had started a descent: the start altitude
    rises monotonically over the next 5 trials, by more than 1 m in total (smaller monotone runs
    occur by chance in normal hover jitter). That trial and all later ones in the shard are excluded."""
    z = []
    for tid in ids:
        tr = pd.read_csv(os.path.join(shard_dir, "traces", f"{tid}.csv.gz"), nrows=1)
        z.append(float(tr.drone_d.iloc[0]) if len(tr) else float("nan"))
    cut = len(z)
    for i in range(len(z) - 5):
        if all(z[j + 1] > z[j] + 0.05 for j in range(i, i + 5)) and z[i + 5] - z[i] > 1.0:
            cut = i
            break
    # ... or the vehicle never again starts a trial at hover altitude (below -10.5 m NED in the gt frame)
    for i in range(len(z)):
        if all(zz > -10.5 for zz in z[i:]):
            cut = min(cut, i)
            break
    # conservative: also drop the trial before the detected onset
    return max(0, cut - 1) if cut < len(z) else cut, z


def load(d):
    parts = []
    excl = {}
    for f in sorted(glob.glob(os.path.join(d, "*", "trials.csv"))):
        t = pd.read_csv(f)
        cut, _ = landed_cutoff(os.path.dirname(f), list(t["id"]))
        excl[os.path.basename(os.path.dirname(f))] = int(len(t) - cut)
        parts.append(t.iloc[:cut])
    df = pd.concat(parts, ignore_index=True)
    df.attrs["excluded_after_failsafe"] = excl
    df["shard_dir"] = df["id"].str.split("-").str[0]
    for c in ["trigger_t_s", "t_min_s", "trigger_ttc_ms", "min_dist_m", "hit", "speed_mps", "response_ms", "compute_us", "trigger_dist_m"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["pre_ca_trigger"] = (df["triggered"] == 1) & (df["kind"].isin(["threat", "flyby"])) & (df["trigger_t_s"] <= df["t_min_s"])
    df.loc[~df["kind"].isin(["threat", "flyby"]), "pre_ca_trigger"] = df["triggered"] == 1
    df["in_narrow"] = (df["az_deg"].abs() < NARROW_HALF[0]) & (df["el_deg"].abs() < NARROW_HALF[1])
    return df


def tier_table(df):
    rows = []
    th = df[df.kind == "threat"]
    for v in sorted(th.speed_mps.unique()):
        for arm in ("reflex", "baseline"):
            g = th[(th.speed_mps == v) & (th.arm == arm)]
            k, n = int(g.hit.sum()), len(g)
            lo, hi = wilson(k, n)
            det = g[g.pre_ca_trigger]
            row = dict(speed_mps=v, arm=arm, n=n, hits=k, rate=k / n if n else float("nan"), ci_lo=lo, ci_hi=hi,
                       detected=int(len(det)), median_trigger_ttc_ms=float(det.trigger_ttc_ms.median()) if len(det) else None,
                       p10_trigger_ttc_ms=float(det.trigger_ttc_ms.quantile(0.1)) if len(det) else None,
                       median_trigger_dist_m=float(det.trigger_dist_m.median()) if len(det) else None)
            if arm == "reflex":
                for name, sub in (("in_narrow", g[g.in_narrow]), ("out_narrow", g[~g.in_narrow])):
                    kk, nn = int(sub.hit.sum()), len(sub)
                    row[f"{name}_hits"], row[f"{name}_n"] = kk, nn
                row["median_response_ms"] = float(g.response_ms.median()) if g.response_ms.notna().any() else None
                row["src_farfield"] = int((det.trigger_src == "farfield").sum()); row["src_gf"] = int((det.trigger_src == "gf").sum())
            rows.append(row)
    return pd.DataFrame(rows)


def controls(df):
    out = {}
    for kind in ("no_object", "noise", "flyby"):
        g = df[df.kind == kind]
        k_any = int((g.triggered == 1).sum())
        k_pre = int(g.pre_ca_trigger.sum())
        out[kind] = dict(n=len(g), any_trigger=k_any, ci95_upper_any=wilson(k_any, len(g))[1] if len(g) else None,
                         pre_closest_approach_trigger=k_pre,
                         by_src={s: int(((g.triggered == 1) & (g.trigger_src == s)).sum()) for s in ("farfield", "gf")})
        if kind == "flyby":
            out[kind]["flyby_hits"] = int(pd.to_numeric(g.hit, errors="coerce").sum())
    return out


def plot_rates(tt, path):
    fig, ax = plt.subplots(figsize=(9, 4.2))
    sp = sorted(tt.speed_mps.unique()); x = np.arange(len(sp))
    for j, (arm, col) in enumerate((("baseline", "#999"), ("reflex", "#1f77b4"))):
        g = tt[tt.arm == arm].set_index("speed_mps").loc[sp]
        ax.bar(x + (j - 0.5) * 0.38, g.rate * 100, 0.38, color=col, label=f"{arm} (n per tier: {int(g.n.min())}-{int(g.n.max())})",
               yerr=[((g.rate - g.ci_lo) * 100).clip(lower=0), ((g.ci_hi - g.rate) * 100).clip(lower=0)], capsize=3)
    ax.axhline(5, color="g", ls="--", lw=1, label="target 5%")
    ax.set_xticks(x, [f"{int(s)}" for s in sp]); ax.set_xlabel("object speed (m/s)"); ax.set_ylabel("collision rate (%)")
    ax.set_title("PX4 SITL v3 (held-out): collision rate per speed tier, 95% Wilson CI"); ax.legend(fontsize=8)
    fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def plot_ttc(df, path):
    th = df[(df.kind == "threat") & (df.arm == "reflex")]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for hit, col, lab in ((0, "#2ca02c", "evaded"), (1, "#d62728", "hit")):
        g = th[th.hit == hit]
        ttc = np.where(g.pre_ca_trigger, g.trigger_ttc_ms, 0.0)
        ax.scatter(g.speed_mps * np.exp(np.random.default_rng(0).normal(0, 0.03, len(g))), ttc, s=10, c=col, label=lab, alpha=0.7)
    ax.axhline(400, color="k", ls=":", lw=1, label="~0.4 s needed to climb 0.4-0.55 m (step test)")
    ax.set_xscale("log"); ax.set_xticks([6, 10, 14, 20, 30, 60, 90, 120], ["6", "10", "14", "20", "30", "60", "90", "120"])
    ax.set_xlabel("object speed (m/s)"); ax.set_ylabel("time-to-contact at trigger (ms; 0 = no trigger before closest approach)")
    ax.legend(fontsize=8); fig.tight_layout(); fig.savefig(path, dpi=130); plt.close(fig)


def load_trace(d, tid):
    sh = tid.split("-")[0]
    return pd.read_csv(os.path.join(d, sh, "traces", f"{tid}.csv.gz"))


def gif(d, row, path, fps=20):
    tr = load_trace(d, row["id"])
    tr = tr.dropna(subset=["obj_n"])
    ca = row["t_min_s"]
    tr = tr[(tr.t > ca - 1.2) & (tr.t < ca + 0.4)]
    if len(tr) < 5:
        return False
    tr = tr.iloc[::2]
    dn, de, dd = tr.drone_n.values, tr.drone_e.values, tr.drone_d.values
    on, oe, od = tr.obj_n.values, tr.obj_e.values, tr.obj_d.values
    fig, ax = plt.subplots(figsize=(5, 4))
    span = 6.0
    def upd(i):
        ax.clear()
        cn, cd = dn[i], dd[i]
        ax.plot(on[: i + 1] - cn, -(od[: i + 1] - cd), "r-", lw=1)
        ax.add_patch(plt.Circle((on[i] - cn, -(od[i] - cd)), 0.15, color="r"))
        ax.plot(dn[: i + 1] - cn, -(dd[: i + 1] - cd), "b-", lw=1)
        ax.add_patch(plt.Circle((0, 0), 0.25, color="b", alpha=0.6))
        ax.set_xlim(-span, span); ax.set_ylim(-2.5, 3.5); ax.set_aspect("equal")
        trig = row["trigger_t_s"] if row["triggered"] == 1 else None
        st = "ESCAPE" if trig is not None and tr.t.values[i] >= trig else ""
        ax.set_title(f"{row['arm']} v={row['speed_mps']:.0f} m/s  t-t_ca={tr.t.values[i]-ca:+.2f}s {st}", fontsize=9)
        ax.set_xlabel("north rel. to vehicle (m)"); ax.set_ylabel("up (m)")
    an = animation.FuncAnimation(fig, upd, frames=len(tr), interval=1000 / fps)
    an.save(path, writer=animation.PillowWriter(fps=fps)); plt.close(fig)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(os.path.dirname(__file__), "..", "..", "docs", "sim_v3"))
    a = ap.parse_args()
    df = load(a.dir)
    tt = tier_table(df)
    tt.to_csv(os.path.join(a.dir, "summary_by_tier.csv"), index=False)
    ctl = controls(df)
    th = df[(df.kind == "threat") & (df.arm == "reflex") & df.pre_ca_trigger]
    summary = dict(n_trials=len(df), excluded_after_battery_failsafe=df.attrs.get("excluded_after_failsafe"), tiers=tt.to_dict("records"), controls=ctl,
                   compute_us_median=float(th.compute_us.median()), compute_us_p95=float(th.compute_us.quantile(0.95)),
                   response_ms_median=float(th.response_ms.median()), loop_hz_median=float(df.loop_hz.median()),
                   loop_hz_min=float(df.loop_hz.min()), settled_fraction=float(df.settled.mean()))
    json.dump(summary, open(os.path.join(a.dir, "summary.json"), "w"), indent=1, default=float)
    plot_rates(tt, os.path.join(a.dir, "collision_rate_by_tier.png"))
    plot_ttc(df, os.path.join(a.dir, "trigger_ttc_by_speed.png"))
    r = df[(df.kind == "threat") & (df.arm == "reflex")]
    for name, sel in (("evasion_10mps.gif", r[(r.speed_mps == 10) & (r.hit == 0)]), ("evasion_60mps.gif", r[(r.speed_mps == 60) & (r.hit == 0)]),
                      ("hit_120mps.gif", r[(r.speed_mps == 120) & (r.hit == 1)]),
                      ("baseline_hit_10mps.gif", df[(df.kind == "threat") & (df.arm == "baseline") & (df.speed_mps == 10) & (df.hit == 1)])):
        if len(sel):
            gif(a.dir, sel.iloc[0], os.path.join(a.dir, name))
    print(tt.to_string()); print(json.dumps(ctl, indent=1))
    print({k: v for k, v in summary.items() if k not in ("tiers", "controls")})


if __name__ == "__main__":
    main()
