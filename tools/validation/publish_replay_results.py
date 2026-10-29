#!/usr/bin/env python3
"""Publish replay metrics into docs/ (and an envelope snippet).

Reads a replay_results.json produced by replay_dataset.py (and/or replay_summary.json
from replay_events.py) and writes:
  - docs/<out>/summary.md   : human-readable metrics table
  - docs/<out>/envelope_snippet.py : measured values to merge into giantfiber/envelope.py by hand

Usage:
    python3 tools/validation/publish_replay_results.py --results docs/replay/replay_results.json --out docs/replay
"""
import argparse
import json
import os
import sys


def load(path):
    with open(path) as f:
        return json.load(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", required=True)
    ap.add_argument("--out", default="docs/replay")
    ap.add_argument("--extra", default=None, help="optional replay_summary.json from replay_events.py")
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    data = load(args.results)
    summary = data.get("summary", data)
    trials = data.get("trials", [])

    # Optional second source
    extra_summary = None
    if args.extra and os.path.exists(args.extra):
        extra_summary = load(args.extra).get("summary")

    lines = ["# Replay metrics (auto-generated)", "", "## Summary", ""]
    lines.append("| metric | value |")
    lines.append("|---|---|")
    for k, v in summary.items():
        lines.append(f"| {k} | {v} |")
    if extra_summary:
        lines.append("")
        lines.append("## Far-field + GF stack (replay_events.py)")
        lines.append("")
        lines.append("| metric | value |")
        lines.append("|---|---|")
        for k, v in extra_summary.items():
            lines.append(f"| {k} | {v} |")

    # Per-trial table (truncated)
    if trials:
        lines.append("")
        lines.append("## Per-trial (first 20)")
        lines.append("")
        lines.append("| file | kind | triggered | ttc_ms_at_trigger |")
        lines.append("|---|---|---|---|")
        for t in trials[:20]:
            lines.append(f"| {t.get('file')} | {t.get('kind')} | {t.get('triggered')} | {t.get('ttc_ms_at_trigger')} |")

    with open(os.path.join(args.out, "summary.md"), "w") as f:
        f.write("\n".join(lines) + "\n")

    # Envelope snippet (manual merge)
    ft = summary.get("false_trigger_rate")
    med_ttc = summary.get("median_ttc_ms_at_trigger")
    with open(os.path.join(args.out, "envelope_snippet.py"), "w") as f:
        f.write("# Measured (replay) values - merge into giantfiber/envelope.py by hand after review\n")
        f.write(f"REPLAY_FALSE_TRIGGER_RATE = {ft!r}\n")
        f.write(f"REPLAY_MEDIAN_TTC_MS_AT_TRIGGER = {med_ttc!r}\n")
        f.write(f"REPLAY_N_TRIALS = {summary.get('n_trials')!r}\n")

    print(f"wrote {args.out}/summary.md and envelope_snippet.py")


if __name__ == "__main__":
    main()