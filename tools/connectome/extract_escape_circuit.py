#!/usr/bin/env python3
"""Extract the Drosophila looming-escape subcircuit from public connectome releases.

Sources (public, no login, no API key):
  * FlyWire FAFB v783 proofread connections (Zenodo record 10676866, CC-BY 4.0)
      https://zenodo.org/records/10676866/files/proofread_connections_783.feather
      md5 f48f972d262323a102aed49af1396b8a
  * FlyWire v783 neuron annotations (Schlegel et al. 2024, flyconnectome/flywire_annotations)
      https://raw.githubusercontent.com/flyconnectome/flywire_annotations/main/supplemental_files/Supplemental_file1_neuron_annotations.tsv
  * MANC v1.2.1 (male adult nerve cord, Takemura et al. 2024 / Marin et al. 2024 / Cheong et al. 2024, CC-BY 4.0),
    as harmonised flat files published by the Lee lab (derived from neuPrint MANC v1.2.1):
      https://storage.googleapis.com/lee-lab_brain-and-nerve-cord-fly-connectome/compiled_data/manc_121/manc_121_meta.feather
      https://storage.googleapis.com/lee-lab_brain-and-nerve-cord-fly-connectome/compiled_data/manc_121/manc_121_simple_edgelist.feather

Usage:
  python3 tools/connectome/extract_escape_circuit.py --download   # fetch the ~970 MB of inputs into .connectome_cache/
  python3 tools/connectome/extract_escape_circuit.py              # regenerate data/connectome/* and the Rust/Python tables

Outputs (all values are copied from the source tables, nothing is hand-edited):
  data/connectome/flywire783_escape_edges.csv     per-neuron LPLC2/LC4 -> GF (DNp01) synapse counts
  data/connectome/flywire783_escape_summary.csv   per-side totals incl. LPLC2/LC4 -> non-GF descending neurons
  data/connectome/manc121_gf_motor_edges.csv      GF -> TTMn / PSI and PSI -> DLMn synapse counts (VNC)
  crates/giantfiber-core/src/connectome/data.rs   generated Rust constants
  giantfiber/data/connectome_graphs.py            generated Python constants
"""
import argparse
import hashlib
import sys
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / ".connectome_cache"
OUT = ROOT / "data" / "connectome"

SOURCES = {
    "proofread_connections_783.feather": (
        "https://zenodo.org/records/10676866/files/proofread_connections_783.feather?download=1",
        "f48f972d262323a102aed49af1396b8a",
    ),
    "Supplemental_file1_neuron_annotations.tsv": (
        "https://raw.githubusercontent.com/flyconnectome/flywire_annotations/main/supplemental_files/Supplemental_file1_neuron_annotations.tsv",
        None,
    ),
    "manc_121_meta.feather": (
        "https://storage.googleapis.com/lee-lab_brain-and-nerve-cord-fly-connectome/compiled_data/manc_121/manc_121_meta.feather",
        None,
    ),
    "manc_121_simple_edgelist.feather": (
        "https://storage.googleapis.com/lee-lab_brain-and-nerve-cord-fly-connectome/compiled_data/manc_121/manc_121_simple_edgelist.feather",
        None,
    ),
}

# The non-GF descending neurons reported here are simply the descending-neuron types that receive
# the most LPLC2 + LC4 synapses in FlyWire v783, other than DNp01. Selection rule: top-N by synapse count.
N_NON_GF_DN_TYPES = 8


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download():
    CACHE.mkdir(exist_ok=True)
    for name, (url, digest) in SOURCES.items():
        dst = CACHE / name
        if not dst.exists():
            print(f"downloading {name} ...", flush=True)
            urllib.request.urlretrieve(url, dst)
        if digest:
            got = md5(dst)
            if got != digest:
                sys.exit(f"md5 mismatch for {name}: {got} != {digest}")
        print(f"ok {name} ({dst.stat().st_size} bytes)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--download", action="store_true")
    ap.add_argument("--cache", default=str(CACHE))
    args = ap.parse_args()
    if args.download:
        download()
    cache = Path(args.cache)

    ann = pd.read_csv(cache / "Supplemental_file1_neuron_annotations.tsv", sep="\t", dtype=str)
    ann["rid"] = ann.root_id.astype("int64")
    ctype = dict(zip(ann.rid, ann.cell_type))
    cside = dict(zip(ann.rid, ann.side))
    csuper = dict(zip(ann.rid, ann.super_class))

    conn = pd.read_feather(cache / "proofread_connections_783.feather")
    vis_ids = set(ann[ann.cell_type.isin(["LPLC2", "LC4"])].rid)
    gf_ids = set(ann[ann.cell_type == "DNp01"].rid)

    # Sum over neuropils -> one row per neuron pair
    out = conn[conn.pre_pt_root_id.isin(vis_ids)]
    out = out.groupby(["pre_pt_root_id", "post_pt_root_id"], as_index=False).syn_count.sum()
    out["pre_type"] = out.pre_pt_root_id.map(ctype)
    out["pre_side"] = out.pre_pt_root_id.map(cside)
    out["post_type"] = out.post_pt_root_id.map(ctype)
    out["post_side"] = out.post_pt_root_id.map(cside)
    out["post_super_class"] = out.post_pt_root_id.map(csuper)

    gf_edges = out[out.post_pt_root_id.isin(gf_ids)].sort_values(
        ["post_side", "pre_type", "syn_count", "pre_pt_root_id"], ascending=[True, True, False, True]
    )
    gf_in_total = conn[conn.post_pt_root_id.isin(gf_ids)].groupby("post_pt_root_id").syn_count.sum()

    dn = out[(out.post_super_class == "descending") & (~out.post_pt_root_id.isin(gf_ids))]
    top_dn = dn.groupby("post_type").syn_count.sum().sort_values(ascending=False).head(N_NON_GF_DN_TYPES)
    dn = dn[dn.post_type.isin(top_dn.index)]

    OUT.mkdir(parents=True, exist_ok=True)
    gf_edges[["pre_pt_root_id", "pre_type", "pre_side", "post_pt_root_id", "post_type", "post_side", "syn_count"]].to_csv(
        OUT / "flywire783_escape_edges.csv", index=False
    )

    rows = []
    for side in ["left", "right"]:
        gf_id = next(r for r in gf_ids if cside[r] == side)
        for src in ["LPLC2", "LC4"]:
            e = gf_edges[(gf_edges.post_side == side) & (gf_edges.pre_type == src)]
            rows.append({"side": side, "pre_type": src, "target": "DNp01", "target_root_id": gf_id,
                             "n_pre_neurons": len(e), "syn_count": int(e.syn_count.sum()),
                             "target_total_input_syn": int(gf_in_total[gf_id])})
            d = dn[(dn.post_side == side) & (dn.pre_type == src)]
            rows.append({"side": side, "pre_type": src, "target": "nonGF_DN", "target_root_id": 0,
                             "n_pre_neurons": d.pre_pt_root_id.nunique(), "syn_count": int(d.syn_count.sum()),
                             "target_total_input_syn": 0})
    summary = pd.DataFrame(rows)
    summary.to_csv(OUT / "flywire783_escape_summary.csv", index=False)
    top_dn.rename("lplc2_plus_lc4_syn").to_csv(OUT / "flywire783_nongf_dn_types.csv")

    # ---------------- MANC (VNC) ----------------
    meta = pd.read_feather(cache / "manc_121_meta.feather")
    meta["id"] = meta.manc_121_id.astype(str)
    mt = dict(zip(meta.id, meta.cell_type))
    ms = dict(zip(meta.id, meta.side))
    el = pd.read_feather(cache / "manc_121_simple_edgelist.feather")
    el["pre_type"] = el.pre.map(mt)
    el["post_type"] = el.post.map(mt)
    el["pre_side"] = el.pre.map(ms)
    el["post_side"] = el.post.map(ms)
    keep = ((el.pre_type == "DNp01") & el.post_type.isin(["TTMn", "PSI"])) | (
        (el.pre_type == "PSI") & el.post_type.str.startswith("DLMn", na=False))
    motor = el[keep][["pre", "pre_type", "pre_side", "post", "post_type", "post_side", "count"]]
    motor = motor.rename(columns={"pre": "pre_body_id", "post": "post_body_id", "count": "syn_count"})
    motor = motor.sort_values(["pre_type", "post_type", "pre_side", "post_side", "pre_body_id", "post_body_id"])
    motor.to_csv(OUT / "manc121_gf_motor_edges.csv", index=False)

    write_rust(gf_edges, summary, top_dn, motor)
    write_python(gf_edges, summary, top_dn, motor)
    print(summary.to_string(index=False))
    print(motor.to_string(index=False))


def _side(s):
    return {"left": "Side::Left", "right": "Side::Right"}[s]


def write_rust(gf_edges, summary, top_dn, motor):
    p = ROOT / "crates/giantfiber-core/src/connectome/data.rs"
    L = ["//! GENERATED by tools/connectome/extract_escape_circuit.py -- do not edit by hand.",
         "//!",
         "//! FlyWire FAFB v783 (Dorkenwald et al. 2024; Schlegel et al. 2024; Zenodo 10676866, CC-BY 4.0)",
         "//! MANC v1.2.1 (Takemura et al. 2024; Marin et al. 2024; Cheong et al. 2024, CC-BY 4.0)",
         "//! Synapse counts are automatically detected chemical synapses summed over neuropils.",
         "//! Electrical synapses (gap junctions, e.g. GF -> TTMn) are NOT visible in EM synapse tables.",
         "",
         "#[derive(Debug, Clone, Copy, PartialEq, Eq)]",
         "pub enum Side { Left, Right }",
         "",
         "#[derive(Debug, Clone, Copy)]",
         "pub struct Edge { pub pre_root_id: u64, pub post_root_id: u64, pub side: Side, pub syn_count: u32 }",
         ""]
    for side in ["left", "right"]:
        s = summary[(summary.side == side) & (summary.target == "DNp01")]
        L.append(f"pub const GF_{side.upper()}_ROOT_ID: u64 = {int(s.target_root_id.iloc[0])};")
        L.append(f"pub const GF_{side.upper()}_TOTAL_INPUT_SYN: u32 = {int(s.target_total_input_syn.iloc[0])};")
        for src in ["LPLC2", "LC4"]:
            for tgt, name in [("DNp01", "GF"), ("nonGF_DN", "NONGF_DN")]:
                r = summary[(summary.side == side) & (summary.pre_type == src) & (summary.target == tgt)].iloc[0]
                L.append(f"pub const {src}_{side.upper()}_TO_{name}_SYN: u32 = {int(r.syn_count)};")
        L.append("")
    L.append("/// Descending-neuron types (other than DNp01) with the most LPLC2+LC4 input; used as the non-GF escape pathway.")
    L.append("pub const NONGF_DN_TYPES: [(&str, u32); %d] = [" % len(top_dn))
    for t, n in top_dn.items():
        L.append(f'    ("{t}", {int(n)}),')
    L.append("];")
    L.append("")
    for src in ["LPLC2", "LC4"]:
        e = gf_edges[gf_edges.pre_type == src]
        L.append(f"/// Individual {src} -> DNp01 connections (FlyWire v783 root IDs).")
        L.append(f"pub const {src}_TO_GF: [Edge; {len(e)}] = [")
        for _, r in e.iterrows():
            L.append(f"    Edge {{ pre_root_id: {int(r.pre_pt_root_id)}, post_root_id: {int(r.post_pt_root_id)}, side: {_side(r.post_side)}, syn_count: {int(r.syn_count)} }},")
        L.append("];")
        L.append("")
    L.append("/// MANC v1.2.1 body IDs: (pre_body_id, pre_type, post_body_id, post_type, syn_count).")
    L.append(f"pub const MANC_GF_MOTOR: [(u64, &str, u64, &str, u32); {len(motor)}] = [")
    for _, r in motor.iterrows():
        L.append(f'    ({int(r.pre_body_id)}, "{r.pre_type}", {int(r.post_body_id)}, "{r.post_type}", {int(r.syn_count)}),')
    L.append("];")
    p.write_text("\n".join(L) + "\n")


def write_python(gf_edges, summary, top_dn, motor):
    p = ROOT / "giantfiber/data/connectome_graphs.py"
    L = ['"""GENERATED by tools/connectome/extract_escape_circuit.py -- do not edit by hand.',
         "",
         "Real neuron IDs and synapse counts for the Drosophila looming-escape circuit:",
         "  FlyWire FAFB v783 (Zenodo 10676866, CC-BY 4.0) for LPLC2/LC4 -> DNp01 (Giant Fiber) and non-GF DNs,",
         "  MANC v1.2.1 (CC-BY 4.0) for GF -> TTMn / PSI and PSI -> DLMn in the ventral nerve cord.",
         "Chemical synapses only; GF electrical synapses (gap junctions) are not in these tables.",
         '"""', "",
         "ESCAPE_SUMMARY = ["]
    for _, r in summary.iterrows():
        L.append(f"    { {'side': r.side, 'pre_type': r.pre_type, 'target': r.target, 'target_root_id': int(r.target_root_id), 'n_pre_neurons': int(r.n_pre_neurons), 'syn_count': int(r.syn_count), 'target_total_input_syn': int(r.target_total_input_syn)}!r},")
    L.append("]")
    L.append("")
    L.append("NONGF_DN_TYPES = " + repr({k: int(v) for k, v in top_dn.items()}))
    L.append("")
    L.append("GF_INPUT_EDGES = [  # (pre_root_id, pre_type, post_root_id, post_side, syn_count)")
    for _, r in gf_edges.iterrows():
        L.append(f"    ({int(r.pre_pt_root_id)}, {r.pre_type!r}, {int(r.post_pt_root_id)}, {r.post_side!r}, {int(r.syn_count)}),")
    L.append("]")
    L.append("")
    L.append("MANC_GF_MOTOR_EDGES = [  # (pre_body_id, pre_type, post_body_id, post_type, syn_count)")
    for _, r in motor.iterrows():
        L.append(f"    ({int(r.pre_body_id)}, {r.pre_type!r}, {int(r.post_body_id)}, {r.post_type!r}, {int(r.syn_count)}),")
    L.append("]")
    p.write_text("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
