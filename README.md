<div align="center">
<img src="docs/assets/logo.png" alt="GiantFiber logo" width="140"/>

# GiantFiber (GF-1)

An event-camera looming-escape reflex modelled on the *Drosophila* Giant Fiber circuit, with
connectome-derived weights (FlyWire v783, MANC v1.2.1). It is a Rust core (std and `no_std`) with a
Python SDK, a PX4 offboard bridge, a CAN-FD frame and a Verilog prototype of the descending stage.

[![CI](https://github.com/reacherwu/giant-fiber/actions/workflows/ci.yml/badge.svg)](https://github.com/reacherwu/giant-fiber/actions/workflows/ci.yml)
</div>

> **Status: research prototype.** Everything below was measured in simulation: synthetic events
> and PX4 SITL. Nothing has been flown, run on real event-camera data, or measured on embedded
> hardware, and no power figure exists. In the PX4 SITL campaign the reflex did **not** reduce
> collisions; see [the results](#px4-sitl-incoming-object-campaign).

---

## What it does

```
events (x, y, t, polarity)
  -> 64x64 time surface
  -> looming estimate: angular size θ, expansion rate dθ/dt, coherence / translation gates
  -> LPLC2 (size-tuned) and LC4 (velocity-tuned) population activity, left/right
  -> Giant Fiber (DNp01) and non-GF descending pathway leaky integrators
       weights = FlyWire v783 synapse-count fractions
  -> escape mode: LONG (non-GF crosses its threshold) or SHORT (GF spike; upgrades LONG within 30 ms)
  -> discrete action (roll away from the stimulus / pitch) + confidence gate
  -> PX4: acceleration setpoints (SET_POSITION_TARGET_LOCAL_NED), or a 24-byte CAN-FD frame
```

Biological basis and how far the model follows it:

| element | source | in the model |
|---|---|---|
| LPLC2 encodes angular size, LC4 angular velocity; both drive the GF | Ache et al. 2019; von Reyn et al. 2017 | activation functions (sigmoid in θ, saturating in dθ/dt); half-activation points are parameters |
| LPLC2/LC4 → DNp01 synapse counts, per hemisphere | FlyWire v783 (Dorkenwald et al. 2024; Schlegel et al. 2024) | real root IDs and counts in `data/connectome/`, normalised per target as weights |
| parallel non-GF descending pathway with a lower threshold → long-mode takeoff; GF spike → short mode | von Reyn et al. 2014 | top-8 non-GF DN types by LPLC2+LC4 input (our selection rule); thresholds 0.30 vs 0.55 |
| GF → TTMn / PSI → DLMn | MANC v1.2.1 (Takemura, Marin, Cheong et al. 2024) | tabulated (`manc121_gf_motor_edges.csv`), not simulated; GF→TTMn is largely electrical and EM counts miss gap junctions |
| heading-hold "central complex" | — | a plain heading integrator, **not** connectome-derived |

Time constants, thresholds and activation shapes are modelling choices, not fits to electrophysiology.
The connectome data were extracted with `tools/connectome/extract_escape_circuit.py`; see
[data/connectome/README.md](data/connectome/README.md) for sources and the CC-BY 4.0 license.

## Model validation (synthetic stimuli)

`python3 tools/validation/validate_escape_model.py --out docs/model` (0.15 m sphere, 90° camera,
geometric event synthesis, 5 seeds per condition):

| r/v (ms) | mean θ at GF spike | GF spike before contact | reached short mode | long mode only |
|---|---|---|---|---|
| 5  | 26.4° | 16.9 ms  | 5/5 | 0/5 |
| 10 | 24.6° | 36.9 ms  | 5/5 | 0/5 |
| 20 | 25.0° | 72.4 ms  | 5/5 | 0/5 |
| 40 | 26.9° | 132.4 ms | 3/5 | 2/5 |
| 80 | 32.8° | 204.2 ms | 0/5 | 5/5 |

- The GF spike time before contact is linear in r/v (2.47·r/v + 16.0 ms, r = 0.980), the signature of a
  size threshold.
- Fast looms end in short mode and slow looms in long mode. This is the qualitative split from von Reyn 2014.
- Receding, lateral, static and noise-only stimuli (0.1–3 Hz/px) gave 0/35 escapes.

![validation](docs/model/looming_validation.png)

## PX4 SITL incoming-object campaign

**Setup.**
- PX4 v1.15.4, SIH quadrotor (`sihsim_quadx`), headless, real-time, offboard mode, hovering at 10 m.
- A 0.30 m sphere is launched at constant speed (6, 10 or 14 m/s) from 15 or 25 m. Azimuth is −15/0/+15°
  and elevation 0/8°. It is aimed at the vehicle's launch position with ≤ 0.1 m aim error.
- Events are synthesised at about 480 Hz from the object geometry and the vehicle's ground-truth pose
  (HIL_STATE_QUATERNION): XOR occupancy changes of the projected disk on a 320×320 camera, plus
  0.3 Hz/px Poisson noise. There is no texture, no DVS pixel model and no motion blur.
- Collision means the minimum centre distance is < 0.40 m (object radius 0.15 m + vehicle radius 0.25 m),
  from ground truth.

**Arms.**
- *Baseline*: the engine runs, but nothing is sent and the vehicle keeps hovering.
- *Reflex (drone preset)*: 60° FOV, LPLC2 half-size 4°, LC4 half-velocity 20°/s. On an escape it streams
  9 m/s² (short mode) or 5 m/s² (long mode) acceleration setpoints sideways or vertically for 0.5 s, then
  holds position.
- *Reflex (fly preset)*: the default fly-scale thresholds with a 90° FOV.
- *Legacy*: the original single SET_ATTITUDE_TARGET packet.
- Controls: 30 × 8 s hover with no object, and 30 fly-bys that miss by 2.5–3.7 m.

The campaign ran 312 trials from 2026-10-19 08:17 to 09:54 PDT on the machine listed below.
Reproduce it with `bash sim/px4_sitl/run_all.sh`.

| arm | trials | collisions | collision rate [95% CI] | escape triggered | median trigger distance | median time-to-contact at trigger |
|---|---|---|---|---|---|---|
| no reflex (baseline) | 72 | 54 | **75.0%** [63.9, 83.6] | 61 (85%) | 1.81 m | 146 ms |
| reflex, drone preset | 108 | 85 | **78.7%** [70.1, 85.4] | 99 (92%) | 2.11 m | 168 ms |
| reflex, fly preset | 36 | 25 | 69.4% [53.1, 82.0] | 20 (56%) | 0.79 m | 31 ms |
| legacy attitude packet | 36 | 31 | 86.1% [71.3, 93.9] | 33 (92%) | 2.02 m | 154 ms |

| control | trials | false triggers |
|---|---|---|
| hover, no object (240 s total) | 30 | 0 (95% CI upper bound 11%) |
| fly-by, 2.5–3.7 m miss | 30 | 0 (95% CI upper bound 11%) |

![collision rate by speed](docs/sim/collision_rate_by_speed.png)

**Finding: the reflex detects the threat, but too late for this vehicle to get out of the way.**
- With the drone preset the escape triggers at a median of 2.1 m (168 ms before contact).
- The vehicle's measured response is too slow for that margin. The time from trigger until its
  ground-truth velocity changes by 0.2 m/s along the escape direction has a median of **186 ms**
  (p5–p95 167–216 ms, n = 99).
- In PX4's own log (121 acceleration-setpoint episodes in the ulog), the vehicle moved a median of
  0.54 m along the commanded direction 0.5 s after the first *logged* setpoint. The setpoint topic is
  logged at 10 Hz.
- So for most trials the vehicle has barely moved at closest approach. The collision rate with the
  reflex is statistically indistinguishable from the baseline: 78.7% vs 75.0%, overlapping CIs.
- The fly preset fires even later (median 31 ms before contact), matching the fly-scale angular thresholds.
- The single-packet legacy command is overridden by the next offboard setpoint and has no effect.
- The baseline does not hit 100% because the hover drifts by a few tenths of a metre during the
  object's 1–4 s flight.

The bottleneck is detection range, not compute:
- Compute per step is 6 µs median.
- Trigger to setpoint sent takes 183 µs median, including the MAVLink encode and UDP send.
- The 64×64 internal time surface needs the object to cover several cells. In an offline check
  (`python3 tools/validation/detection_range.py`, 6 head-on runs per setting, 0.3 m object):
  - the SITL drone preset triggered at 1.6–2.6 m;
  - lowering the thresholds gave 1.9–3.5 m (mostly about 2.6 m);
  - a 30° FOV gave 2.9–4.0 m;
  - none of these settings triggered on the 6 fly-bys.
- Making this useful for a quadrotor needs earlier detection (a higher-resolution surface or a
  narrower FOV), a faster actuation path, or both. These are open items.

Ground-truth trajectories (view from behind the vehicle; circles mark the closest approach):

![trajectories](docs/sim/trajectories.png)

| reflex trial that evaded (6 m/s) | baseline trial that was hit (10 m/s) |
|---|---|
| ![evasion](docs/sim/evasion.gif) | ![baseline](docs/sim/baseline_hit.gif) |

The animations are rendered from the logged ground truth (top view) with the engine's internal state.
They are not camera footage.

Vehicle response from the PX4 ulog (one line per escape episode, aligned to the first logged
acceleration setpoint). A few episodes show motion opposite to the averaged setpoint. These were not investigated
individually; a likely cause is that the analysis merges setpoints less than 2 s apart, so it can
combine two escape commands with different directions into one episode. The analysis found 121
episodes in the ulog against 119 reflex trials that triggered.

![ulog response](docs/sim/ulog_escape_response.png)

Data:
- `docs/sim/trials.csv`: one row per trial.
- `docs/sim/traces/*.csv.gz`: 100 Hz traces per trial.
- `docs/sim/summary.json` and `summary_by_speed.csv`
- `docs/sim/ulog_escape_episodes.csv`
- `docs/sim/campaign_meta.json`

The 1.3 GB ulog is not committed. `sim/px4_sitl/analyze.py --ulog <file>` regenerates the plots
from a new run.

## Benchmarks

`python3 benchmarks/run_benchmarks.py --json benchmarks/results.json` on an Intel Xeon (8 vCPU)
cloud VM running Linux 6.12, with Python 3.13.5 and rustc 1.99.1. All figures were measured in that
run (see `benchmarks/results.json`):

| measurement | result |
|---|---|
| native `step_eval` (Rust, looming stimulus, 56,100 steps) | mean 1.72 µs, p99 5.88 µs, max 34 µs |
| native event ingestion | 141 M events/s |
| Python → C ABI round trip (feed + step + pydantic object) | median 7.7 µs, p99 96 µs |
| event ingestion through the C ABI | 31.5 M events/s |
| snapshot save / restore (280 bytes) | 0.80 µs / 0.76 µs |
| MAVLink SET_POSITION_TARGET_LOCAL_NED encode (Python) | 11.7 µs |
| CAN-FD encode + decode (Python) | 28.3 µs |
| EVT2 decode (Python) | 1.59 M events/s |
| false triggers, 60 s of 1 Hz/px noise | 0 |

These are desktop-CPU numbers. They say nothing about a microcontroller, and no power was measured.

## Components

| path | what |
|---|---|
| `crates/giantfiber-core` | Rust engine. `cargo build --no-default-features --features libm --target thumbv7em-none-eabihf` builds `no_std` (checked in CI); the C ABI (`ffi.rs`) needs `std` |
| `giantfiber/` | Python SDK (ctypes), PX4 bridge (`integrations/px4_mavlink.py`), CAN-FD + SocketCAN (`integrations/can_fd.py`), Prophesee EVT2/EVT3 decoders, event synthesis (`sim/events.py`) |
| `crates/.../canfd.rs` | `no_std` 24-byte CAN-FD frame, byte-identical to the Python codec (tested). The SocketCAN loopback test passes on a `vcan0` interface in CI; it has not been tested on physical CAN hardware |
| `rtl/` | Verilog prototype of the GF/non-GF stage. In Icarus Verilog it matches the Rust engine on 10/11 stimuli exactly and on 1 within one step. Simulation only, not synthesised ([rtl/README.md](rtl/README.md)) |
| `tools/connectome` | connectome extraction (FlyWire v783, MANC v1.2.1) |
| `tools/validation` | model validation on synthetic looms |
| `sim/px4_sitl` | SITL campaign, analysis, one-command reproduction |

The EVT2/EVT3 decoders follow the published Prophesee word formats. They have only been tested on
synthetic words, not on recordings from a real sensor.

## Quick start

```bash
cargo build --release
pip install -r requirements-dev.txt
cargo test && python3 -m pytest tests -q
python3 examples/looming_demo.py          # prints θ, membranes and the escape decision for a loom
```

```python
from giantfiber import GiantFiberCoprocessor, ReflexConfig
with GiantFiberCoprocessor(ReflexConfig()) as gf:
    gf.feed_raw_spikes(buf)        # numpy array of giantfiber.sim.events.EVENT_DTYPE, .tobytes()
    d = gf.step_eval(now_us)       # ReflexDecision: action, escape_mode, vector, confidence, triggered
```

PX4 (SITL or companion computer, offboard mode):
`PX4ReflexBridge(PX4BridgeConfig()).step_eval(t_us)` starts an escape on a decision.
`evasion_packet()` returns the acceleration setpoint to stream while `evasion_active()` is true.
See `sim/px4_sitl/run_campaign.py` for a complete loop.

Make targets: `test`, `clippy`, `nostd`, `bench`, `validate`, `rtl`, `connectome`, `sitl`.

## Roadmap

Done in software:
- connectome-derived model
- `no_std` core
- PX4 offboard bridge tested in SITL
- CAN-FD frame and SocketCAN transport
- RTL prototype of the descending stage

Planned / not started:
- detection range for drone-scale use (higher-resolution time surface)
- validation on real event-camera recordings
- hardware-in-the-loop and flight tests
- embedded timing and power measurements
- RTL for the front end, synthesis and FPGA

## References

- Ache, J. M. et al. (2019) Neural basis for looming size and velocity encoding in the *Drosophila* giant fiber escape pathway. *Curr. Biol.* 29.
- von Reyn, C. R. et al. (2014) A spike-timing mechanism for action selection. *Nat. Neurosci.* 17.
- von Reyn, C. R. et al. (2017) Feature integration drives probabilistic behavior in the *Drosophila* escape response. *Neuron* 94.
- Klapoetke, N. C. et al. (2017) Ultra-selective looming detection from radial motion opponency. *Nature* 551.
- Card, G. & Dickinson, M. H. (2008) Visually mediated motor planning in the escape response of *Drosophila*. *Curr. Biol.* 18.
- Dombrovski, M. et al. (2023) Synaptic gradients transform object location to action. *Nature* 613.
- Dorkenwald, S. et al. (2024) Neuronal wiring diagram of an adult brain. *Nature* 634.
- Schlegel, P. et al. (2024) Whole-brain annotation and multi-connectome cell typing of *Drosophila*. *Nature* 634.
- Takemura, S. et al.; Marin, E. C. et al.; Cheong, H. S. J. et al. (2024) Male adult nerve cord (MANC) papers. *eLife*.

## License

Code: Apache-2.0 ([LICENSE](LICENSE)). Connectome-derived tables in `data/connectome/`: CC-BY 4.0,
from FlyWire and MANC (cite the papers above). PX4, Pixhawk and MAVLink are trademarks of their
respective owners. This project is not affiliated with them.
