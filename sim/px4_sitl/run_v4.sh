#!/usr/bin/env bash
# Reproduce the final held-out PX4 SITL campaign v4 (docs/sim_v4): 6 PX4 SIH instances, rig tiled9,
# seeds 2001-2006, 8 tiers x (11 reflex + 1 baseline) + 3 x 11 controls per shard.
#
#   bash sim/px4_sitl/run_v4.sh            # full campaign (~1.5 h on 8 cores)
#   N=2 LIMIT=10 bash sim/px4_sitl/run_v4.sh  # smoke test: 2 shards, 10 trials each
#
# Environment: PX4_DIR (default $HOME/px4/PX4-Autopilot, built with `make px4_sitl_default` at v1.15.4),
# PYTHON (python with requirements-dev.txt), OUT (default docs/sim_v4), N (instances, default 6).
# Timing is wall-clock (real-time SITL), so results depend on host load; v4 ran on an 8-core box.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PX4_DIR="${PX4_DIR:-$HOME/px4/PX4-Autopilot}"
PYTHON="${PYTHON:-python3}"
OUT="${OUT:-$REPO/docs/sim_v4}"
N="${N:-6}"
LIMIT="${LIMIT:-0}"
BUILD="$PX4_DIR/build/px4_sitl_default"
[ -x "$BUILD/bin/px4" ] || (cd "$PX4_DIR" && make px4_sitl_default)
(cd "$REPO" && cargo build --release)
pids=()
cleanup() { kill "${pids[@]}" 2>/dev/null || true; pkill -f "px4_sitl_default/bin/px4" 2>/dev/null || true; }
trap cleanup EXIT
for i in $(seq 0 $((N - 1))); do
  mkdir -p "$BUILD/instance_$i"
  (cd "$BUILD/instance_$i" && HEADLESS=1 PX4_SIM_MODEL=sihsim_quadx PX4_SYS_AUTOSTART=10040 \
    "$BUILD/bin/px4" -i "$i" -d "$BUILD/etc" > "$OUT.px4_$i.log" 2>&1) &
  pids+=($!)
done
sleep 20
mkdir -p "$OUT"
cpids=()
for i in $(seq 0 $((N - 1))); do
  extra=(); [ "$LIMIT" != 0 ] && extra=(--limit "$LIMIT")
  (cd "$REPO" && "$PYTHON" sim/px4_sitl/run_campaign_v3.py --instance "$i" --rig tiled9 --seed $((2001 + i)) \
    --shard "x$i" --tiers 6,10,14,20,30,60,90,120 --n-reflex 11 --n-baseline 1 --controls 11 \
    --settle-max 60 --out "$OUT" "${extra[@]}" > "$OUT.x$i.log" 2>&1) &
  cpids+=($!)
done
wait "${cpids[@]}"
(cd "$REPO" && "$PYTHON" sim/px4_sitl/analyze_v3.py --dir "$OUT")
