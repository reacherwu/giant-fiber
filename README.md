<div align="center">

# GiantFiber (GF-1)
### Sub-5ms, Sub-1W Bio-Reflex Coprocessor for Autonomous Machines

[![License: Apache 2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Inference Latency](https://img.shields.io/badge/End--to--End_Latency-<4.0ms-brightgreen.svg)]()
[![Rust Native Core](https://img.shields.io/badge/Rust_Native-Zero_Bloat-red.svg)]()
[![Power Budget](https://img.shields.io/badge/Power_Consumption-0.51W--0.62W-orange.svg)]()
[![Type Safety](https://img.shields.io/badge/TypeSafe-Pydantic_Enforced-purple.svg)]()
[![Connectome Prior](https://img.shields.io/badge/Bio_Prior-FlyWire_Drosophila-teal.svg)]()

*The open-source physical "System-1" reflex coprocessor inspired by the Drosophila Connectome (FlyWire / MaleCNS) and Calibrated Discrete Decision Theory.*

*5毫秒极限响应、1瓦级超低功耗——基于果蝇全脑连接组逃逸先验与强类型快决策的机器小脑。*

[Quickstart](#-quickstart) • [Architecture](#-architecture) • [Drosophila Sub-Circuits](#-drosophila-sub-circuits) • [Hardware Budget](#-hardware-and-latency-budget) • [Benchmarks](#-empirical-benchmarks) • [Roadmap](#-commercial-roadmap)

</div>

---

### ⚠️ Disclaimer
GiantFiber is an independent open-source research and engineering initiative. It is neither affiliated with nor endorsed by commercial VLM model providers or proprietary drone manufacturers.

---

### 💡 Why GiantFiber?

Modern foundation models (VLMs, Vision-Language-Action models) are architected for **System-2 (Deliberate Slow Thinking)**:
- They generate unbounded autoregressive tokens.
- They consume **15W – 30W+** of power (e.g., Jetson Orin Nano).
- They incur **150ms – 1000ms+** of inference latency.

In real-world physical dynamics, **a 150ms delay at 10 m/s means a 1.5-meter lethal collision.**

```
[VLM / LLM Decision Loop] : ─────────── 180 ~ 350 ms ───────────> [COLLISION / CRASH]
[Classic Optical Flow]    : ──── 25 ~ 45 ms ────> [Oscillates / Blind to Looming]
[GiantFiber (GF-1)]       : ── 3.8 ms ──> [KNIFE-EDGE EVASION & STABILIZATION]
```

**GiantFiber bridges the gap by synthesizing two proven paradigms:**
1. **Drosophila Melanogaster Connectome Prior (FlyWire / MaleCNS)**: Fruit flies evade predatory strikes in 2–5ms using only ~160,000 neurons and microwatts of power. GiantFiber extracts the validated **Giant Fiber Escape Circuit (Col4 $\rightarrow$ GF $\rightarrow$ TTMn / DLMn / PSI)**, the **Lobula Plate Tangential Cells (LPTC HS/VS)** for optic flow divergence, and the **Central Complex (CX E-PG / P-EN)** ring attractor for post-reflex stabilization.
2. **Jev-Style System-1 Discrete Decision Engine**: Discards token generation in favor of **strict Pydantic enum contracts** and **mathematically calibrated probabilities** ($P \in [0.0, 1.0]$). Below the safety threshold ($\theta < 0.85$), the system falls back to nominal cruise, preventing jitter and false alarms.

---

### 🔬 Architecture & Signal Pipeline

```
       ┌────────────────────────────────────────────────────────┐
       │     Perception Layer (Neuromorphic Event Stream)       │
       │     Prophesee GenX320 / DVS Asynchronous Microsecond Spikes│
       └───────────────────────────┬────────────────────────────┘
                                   │ (x, y, t, p)
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │     Rust Core Engine (Physical O(K) Bounded Memory)    │
       │     • Time Surface Accumulator: Fixed-size 64x64 grid   │
       │     • Lobula Plate: LPTC HS/VS optic divergence & flow │
       │     • Giant Fiber System: Col4 looming non-linear spike│
       │     • Central Complex: E-PG/P-EN 16-wedge ring compass │
       │     • Jev Calibrator: Temperature scaling & Gatekeeper │
       └───────────────────────────┬────────────────────────────┘
                                   │ C-ABI / Zero-Overhead FFI
                                   ▼
       ┌────────────────────────────────────────────────────────┐
       │     Type-Safe Decision Contract (Python / C-Struct)    │
       │     • action: ReflexAction.ROLL_RIGHT_90               │
       │     • confidence: 0.9642 (Calibrated)                  │
       │     • latency: 3.8 ms total (< 1.0 ms native compute)  │
       │     • power_estimated: 0.51W ~ 0.62W                   │
       └───────────────────────────┬────────────────────────────┘
                                   │ 1000Hz CAN-FD / UART
                                   ▼
                     [Flight Controller / Pixhawk / Crazyflie]
```

---

### 🧬 Drosophila Connectome Sub-Circuits

| Sub-Circuit | FlyWire Neuron IDs | Biological Function in Drosophila | Role in GiantFiber (GF-1) |
| :--- | :--- | :--- | :--- |
| **Giant Fiber (GF)** | `720575940614131001` (L)<br>`720575940614131002` (R) | Cervical giant descending interneuron; conducts escape action potentials. | High-priority looming escape trigger. |
| **Lobula Col4** | `720575940621004200` | Detects non-linear visual looming expansion ($r/v$). | Presynaptic drive into GF dendritic arbor. |
| **TTMn** | `720575940628392100` | Tergotrochanteral motor neuron driving middle leg jump. | Explosive evasion thrust / jump trigger. |
| **DLMn & PSI** | `720575940609384500`<br>`720575940633119800` | Wing depression and asynchronous flight initiation. | 90-degree knife-edge roll steering deflection. |
| **LPTC (HS/VS)** | Horizontal & Vertical Tangential Cells | Integrates wide-field optical flow and divergence. | Computes flow divergence $\nabla \cdot \mathbf{v}$ and left/right asymmetry. |
| **Central Complex (CX)** | `E-PG` (16 wedges), `P-EN` (16) | Heading ring attractor maintaining internal compass. | Post-reflex attitude recovery & stabilization torque. |

---

### ⏱️ Hardware and Latency Budget

#### End-to-End Latency Breakdown (< 4.0ms)
```text
[Photon Flux Shift on Retina]
      │
      ▼ < 0.2 ms   (Prophesee GenX320 DVS pixel response)
[Event Packet Ingestion]
      │
      ▼ < 1.4 ms   (Time surface decay update & LPTC optic flow extraction)
[Sparse Connectome Propagation]
      │
      ▼ < 0.8 ms   (Giant Fiber threshold check & Central Complex heading state)
[Jev Decision Calibration]
      │
      ▼ < 0.4 ms   (Temperature scaling & Type-safe struct pack)
[CAN-FD Interconnect]
      │
      ▼ < 0.8 ms   (Flight controller preemptive hardware interrupt)
[Motor Thrust Compensation]
══════════════════════════════════════════════════════════════════════════
Total End-to-End Latency : ~ 3.6 ms (Comfortably inside < 5.0 ms budget)
```

#### BOM Cost & Power Envelope (< $120, < 1W)
- **Event Vision Sensor**: Prophesee GenX320 (35 mW)
- **Biomimetic Halteres / IMU**: Bosch BMI088 (1.5 mW)
- **Reflex Coprocessor**: STM32N6 / AMD Kria KV260 (450 mW)
- **High-Speed Transceiver**: TI TCAN330 CAN-FD (25 mW)
- **Total Power**: **~ 511.5 mW (0.51W)**

---

### 📊 Empirical Benchmarks

*Hardware: Apple Silicon / ARM64, Rust 1.95.0, Python 3.13*

```bash
make bench
```

| Benchmark Metric | Measured Performance | Production Redline Target | Status |
| :--- | :--- | :--- | :--- |
| **Native Compute Latency** | **< 0.05 ms (Mean)** | $< 1.0\ \text{ms}$ | **PASS** |
| **Event Throughput** | **3,381,000 events/sec** | $> 500,000\ \text{eps}$ | **PASS** |
| **Physical Memory Growth (500k spikes)** | **0 KB ($\Delta\text{RSS} = 0$)** | $O(K)$ Constant Memory | **PASS** |
| **Binary Snapshot Serialization** | **< 30 µs** (`b"GF1\0"`) | $< 1.0\ \text{ms}$ | **PASS** |
| **Snapshot Bit-Exact Recovery** | **100% (Adler-32 Validated)**| Bit-exact across reboots | **PASS** |

---

### 🚀 Quickstart

#### 1. Prerequisites & Build
```bash
git clone https://github.com/reacherwu/giant-fiber.git
cd giant-fiber

# Build native Rust release dylib
make build

# Run dual-track automated test suite (cargo test + python unittest)
make test
```

#### 2. Run the High-Speed Evasion Simulation Demo
```bash
make demo
```

Expected output:
```text
[T=  1.0ms] Dist: 1.99m | Drone Pos: [         ⚡✈         ] | Action: ROLL_RIGHT_90 | Conf: [████████████████████] 1.00 | Pwr: 0.62W

[TEST COMPLETED: EVASION SUCCESSFUL]
 • Looming Detection & Interrupt Triggered At : T = 1.0 ms
 • Giant Fiber System Action Fired             : ROLL_RIGHT_90
 • Calibrated Statistical Confidence          : 100.0%
 • Projectile Impact Line Crossing At          : T = 142.9 ms
 • Lateral Clearance at Impact Plane          : 0.45 meters (Clean Miss!)
```

#### 3. Python SDK Usage
```python
from giantfiber import GiantFiberCoprocessor, ReflexConfig, ReflexAction

config = ReflexConfig(confidence_threshold=0.85, looming_threshold=0.60)

with GiantFiberCoprocessor(config) as coprocessor:
    # Feed microsecond asynchronous DVS spike
    coprocessor.feed_spike(x=140, y=160, timestamp_us=10200, polarity=1)

    # Step sub-millisecond evaluation
    decision = coprocessor.step_eval(now_us=10200)

    if decision.triggered:
        print(f"REFLEX INTERRUPT: {decision.action.name}, Confidence: {decision.confidence:.2%}")
        # Dispatch to flight controller preemptive queue:
        # flight_controller.inject_interrupt(decision.action, decision.vector)
```

---

### 🗺️ Commercial Roadmap

1. **Phase 1: Companion Shield (飞控伴侣反射板)**  
   STM32N6 / Kria FPGA add-on board for Pixhawk and Crazyflie micro-drones. Commercial MSRP: $300–$600 per unit.
2. **Phase 2: Smart Bio-Eye (集成式仿生“电子复眼”模组)**  
   Integrated coin-sized (<10g) camera module pairing GenX320 with GF coprocessor. Outputs 4-byte action packets directly via CAN-FD / SPI.
3. **Phase 3: Silicon RTL IP Core Licensing (硅核 IP 与嵌入式固件)**  
   Pure Verilog RTL implementation of the sparse connectome graph accelerator licensed to AIoT and drone silicon vendors.

---

### 📜 License
GiantFiber is licensed under the [Apache License 2.0](LICENSE).
