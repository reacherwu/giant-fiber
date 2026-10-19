#!/usr/bin/env bash
# One-command reproduction of the PX4 SITL campaign in docs/sim/.
#
#   bash sim/px4_sitl/run_all.sh [extra run_campaign.py args, e.g. --limit 20]
#
# Environment:
#   PX4_DIR   PX4-Autopilot checkout (default: $HOME/px4/PX4-Autopilot; cloned at v1.15.4 if missing)
#   OUT       output directory (default: docs/sim)
#   PYTHON    python with requirements-dev.txt installed (default: python3)
# PX4 build prerequisites (compiler, cmake, ninja, python deps such as empy==3.3.4, kconfiglib,
# jinja2, pyros-genmsg) must already be installed; see PX4's Tools/setup/ubuntu.sh.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PX4_DIR="${PX4_DIR:-$HOME/px4/PX4-Autopilot}"
OUT="${OUT:-$REPO/docs/sim}"
PYTHON="${PYTHON:-python3}"
PX4_TAG="v1.15.4"

if [ ! -d "$PX4_DIR" ]; then
  mkdir -p "$(dirname "$PX4_DIR")"
  git clone --branch "$PX4_TAG" --depth 1 --recursive --shallow-submodules \
    https://github.com/PX4/PX4-Autopilot.git "$PX4_DIR"
  # the NuttX submodule's version header needs its tag, which a shallow clone lacks
  (cd "$PX4_DIR/platforms/nuttx/NuttX/nuttx" && git fetch --depth 1 origin tag nuttx-11.0.0 || true)
fi

if [ ! -x "$PX4_DIR/build/px4_sitl_default/bin/px4" ]; then
  (cd "$PX4_DIR" && make px4_sitl_default)
fi

(cd "$REPO" && cargo build --release)

LOG_DIR="$PX4_DIR/build/px4_sitl_default/rootfs/log"
MARK="$(mktemp)"
echo "starting PX4 SITL (SIH quadrotor, headless)"
(cd "$PX4_DIR" && HEADLESS=1 PX4_SIM_MODEL=sihsim_quadx PX4_SYS_AUTOSTART=10040 \
  ./build/px4_sitl_default/bin/px4 -d > "$OUT.px4.log" 2>&1) &
PX4_PID=$!
trap 'kill $PX4_PID 2>/dev/null || true; pkill -f "px4_sitl_default/bin/px4" 2>/dev/null || true' EXIT
sleep 15

mkdir -p "$OUT"
"$PYTHON" "$REPO/sim/px4_sitl/run_campaign.py" --out "$OUT" "$@"

kill $PX4_PID 2>/dev/null || true
sleep 3
ULOG="$(find "$LOG_DIR" -name '*.ulg' -newer "$MARK" | sort | tail -1)"
echo "PX4 log: ${ULOG:-none}"
if [ -n "$ULOG" ]; then
  "$PYTHON" "$REPO/sim/px4_sitl/analyze.py" --dir "$OUT" --ulog "$ULOG"
else
  "$PYTHON" "$REPO/sim/px4_sitl/analyze.py" --dir "$OUT"
fi
