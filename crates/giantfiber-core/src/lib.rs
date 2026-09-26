//! GiantFiber Core Engine (GF-1).
//!
//! Sub-5ms, Sub-1W Bio-Reflex Coprocessor Core integrating Drosophila Connectome
//! functional subcircuits with Jev's System-1 calibrated discrete decision theory.

pub mod types;
pub mod accumulator;
pub mod connectome;
pub mod calibrator;
pub mod persistence;

use std::time::Instant;
use types::{EventSpike, ImuData, ReflexOutput, EngineConfig};
use accumulator::EventAccumulator;
use connectome::{LobulaPlateCircuit, GiantFiberCircuit, CentralComplexCircuit};
use calibrator::DecisionCalibrator;
use persistence::EngineSnapshot;

/// GiantFiber Core Engine Instance.
pub struct GiantFiberEngine {
    pub accumulator: EventAccumulator,
    pub lobula_plate: LobulaPlateCircuit,
    pub giant_fiber: GiantFiberCircuit,
    pub central_complex: CentralComplexCircuit,
    pub calibrator: DecisionCalibrator,
    pub config: EngineConfig,
    pub last_imu_time_us: u64,
}

impl GiantFiberEngine {
    pub fn new(config: EngineConfig) -> Self {
        Self {
            accumulator: EventAccumulator::new(config.decay_tau_us),
            lobula_plate: LobulaPlateCircuit::new(),
            giant_fiber: GiantFiberCircuit::new(config.looming_threshold, config.refractory_period_us),
            central_complex: CentralComplexCircuit::new(),
            calibrator: DecisionCalibrator::new(config.temperature, config.confidence_threshold),
            config,
            last_imu_time_us: 0,
        }
    }

    pub fn reset(&mut self) {
        self.accumulator.reset();
        self.giant_fiber.reset();
        self.central_complex.reset();
        self.last_imu_time_us = 0;
    }

    #[inline(always)]
    pub fn feed_event(&mut self, spike: &EventSpike, raw_w: u16, raw_h: u16) {
        self.accumulator.feed_spike(spike, raw_w, raw_h);
    }

    pub fn feed_events_batch(&mut self, spikes: &[EventSpike], raw_w: u16, raw_h: u16) {
        for spike in spikes {
            self.accumulator.feed_spike(spike, raw_w, raw_h);
        }
    }

    pub fn update_imu(&mut self, imu: &ImuData) {
        if !imu.gyro_z.is_finite() {
            return;
        }
        if self.last_imu_time_us > 0 && imu.timestamp_us > self.last_imu_time_us {
            let dt_s = ((imu.timestamp_us - self.last_imu_time_us) as f32) / 1_000_000.0;
            self.central_complex.update(imu.gyro_z, dt_s);
        }
        self.last_imu_time_us = imu.timestamp_us;
    }

    /// Primary System-1 Evaluation Pipeline.
    /// Executes end-to-end connectome inference in sub-millisecond flat memory scan.
    pub fn step_eval(&mut self, now_us: u64) -> ReflexOutput {
        let start = Instant::now();

        let current_us = if now_us > 0 { now_us } else { self.accumulator.current_time_us };

        // 1. Update temporal event dynamics (expansion rate dR/dt)
        self.accumulator.update_dynamics(current_us);

        // 2. Lobula Plate Tangential Cells (LPTC HS/VS) Optic Flow Extraction
        let flow = self.lobula_plate.compute_flow(&self.accumulator);

        // 3. Giant Fiber Escape Circuit Firing Evaluation
        let escape_output = self.giant_fiber.step(&flow, self.accumulator.expansion_rate, current_us);

        // 4. Central Complex Heading & Stabilization State
        let compass_state = self.central_complex.state;

        // 5. Jev System-1 Calibrated Decision Gatekeeper
        let mut decision = self.calibrator.calibrate(
            &escape_output,
            &compass_state,
            &self.config,
            current_us,
            0,
        );

        // Capture total pipeline latency including gatekeeper calibration
        decision.compute_latency_us = start.elapsed().as_micros() as u32;
        decision
    }

    /// Create snapshot of internal engine state.
    pub fn save_snapshot(&self, buffer: &mut [u8]) -> Result<usize, &'static str> {
        let mut snapshot = EngineSnapshot {
            magic: persistence::SNAPSHOT_MAGIC,
            version: persistence::SNAPSHOT_VERSION,
            timestamp_us: self.accumulator.current_time_us,
            current_time_us: self.accumulator.current_time_us,
            last_fire_time_us: self.giant_fiber.last_fire_time_us,
            last_imu_time_us: self.last_imu_time_us,
            v_membrane: self.giant_fiber.v_membrane,
            expansion_rate: self.accumulator.expansion_rate,
            centroid_x: self.accumulator.centroid_x,
            centroid_y: self.accumulator.centroid_y,
            radius: self.accumulator.radius,
            prev_radius: self.accumulator.prev_radius,
            prev_radius_time_us: self.accumulator.prev_radius_time_us,
            heading_rad: self.central_complex.state.heading_rad,
            target_heading_rad: self.central_complex.target_heading_rad,
            correction_torque: self.central_complex.state.correction_torque,
            wedges: self.central_complex.state.wedges,
            config: self.config,
            checksum: 0,
        };
        snapshot.serialize(buffer)
    }

    /// Restore internal engine state from snapshot.
    pub fn restore_snapshot(&mut self, buffer: &[u8]) -> Result<(), &'static str> {
        let snapshot = EngineSnapshot::deserialize(buffer)?;
        self.accumulator.current_time_us = snapshot.current_time_us;
        self.accumulator.expansion_rate = snapshot.expansion_rate;
        self.accumulator.centroid_x = snapshot.centroid_x;
        self.accumulator.centroid_y = snapshot.centroid_y;
        self.accumulator.radius = snapshot.radius;
        self.accumulator.prev_radius = snapshot.prev_radius;
        self.accumulator.prev_radius_time_us = snapshot.prev_radius_time_us;
        self.last_imu_time_us = snapshot.last_imu_time_us;
        self.giant_fiber.last_fire_time_us = snapshot.last_fire_time_us;
        self.giant_fiber.v_membrane = snapshot.v_membrane;
        self.central_complex.state.heading_rad = snapshot.heading_rad;
        self.central_complex.target_heading_rad = snapshot.target_heading_rad;
        self.central_complex.state.wedges = snapshot.wedges;
        self.central_complex.state.correction_torque = snapshot.correction_torque;
        self.config = snapshot.config;

        // Synchronize restored configuration across circuits
        self.accumulator.tau_us = self.config.decay_tau_us;
        self.giant_fiber.firing_threshold = self.config.looming_threshold;
        self.giant_fiber.refractory_us = self.config.refractory_period_us;
        self.calibrator.temperature = self.config.temperature;
        self.calibrator.threshold = self.config.confidence_threshold;
        Ok(())
    }
}

// ----------------------------------------------------------------------------
// C-ABI Export Layer (Zero-cost FFI for Python ctypes, C, C++, Verilog/FPGA)
// ----------------------------------------------------------------------------

#[no_mangle]
pub unsafe extern "C" fn gf_engine_new(config: *const EngineConfig) -> *mut GiantFiberEngine {
    let cfg = if !config.is_null() {
        *config
    } else {
        EngineConfig::default()
    };
    Box::into_raw(Box::new(GiantFiberEngine::new(cfg)))
}

#[no_mangle]
pub unsafe extern "C" fn gf_engine_free(engine: *mut GiantFiberEngine) {
    if !engine.is_null() {
        drop(Box::from_raw(engine));
    }
}

#[no_mangle]
pub unsafe extern "C" fn gf_engine_reset(engine: *mut GiantFiberEngine) {
    if let Some(eng) = engine.as_mut() {
        eng.reset();
    }
}

#[no_mangle]
pub unsafe extern "C" fn gf_feed_event(
    engine: *mut GiantFiberEngine,
    spike: *const EventSpike,
    raw_w: u16,
    raw_h: u16,
) {
    if let (Some(eng), Some(spk)) = (engine.as_mut(), spike.as_ref()) {
        eng.feed_event(spk, raw_w, raw_h);
    }
}

#[no_mangle]
pub unsafe extern "C" fn gf_feed_events_batch(
    engine: *mut GiantFiberEngine,
    spikes: *const EventSpike,
    count: usize,
    raw_w: u16,
    raw_h: u16,
) {
    if let (Some(eng), false) = (engine.as_mut(), spikes.is_null()) {
        let slice = std::slice::from_raw_parts(spikes, count);
        eng.feed_events_batch(slice, raw_w, raw_h);
    }
}

#[no_mangle]
pub unsafe extern "C" fn gf_update_imu(engine: *mut GiantFiberEngine, imu: *const ImuData) {
    if let (Some(eng), Some(i)) = (engine.as_mut(), imu.as_ref()) {
        eng.update_imu(i);
    }
}

#[no_mangle]
pub unsafe extern "C" fn gf_step_eval(
    engine: *mut GiantFiberEngine,
    now_us: u64,
    out_result: *mut ReflexOutput,
) -> i32 {
    if let (Some(eng), Some(out)) = (engine.as_mut(), out_result.as_mut()) {
        *out = eng.step_eval(now_us);
        0
    } else {
        -1
    }
}

#[no_mangle]
pub unsafe extern "C" fn gf_get_snapshot_size() -> usize {
    core::mem::size_of::<EngineSnapshot>()
}

#[no_mangle]
pub unsafe extern "C" fn gf_save_snapshot(
    engine: *mut GiantFiberEngine,
    buffer: *mut u8,
    buf_len: usize,
) -> i32 {
    if let (Some(eng), false) = (engine.as_mut(), buffer.is_null()) {
        let slice = std::slice::from_raw_parts_mut(buffer, buf_len);
        match eng.save_snapshot(slice) {
            Ok(bytes_written) => bytes_written as i32,
            Err(_) => -2,
        }
    } else {
        -1
    }
}

#[no_mangle]
pub unsafe extern "C" fn gf_restore_snapshot(
    engine: *mut GiantFiberEngine,
    buffer: *const u8,
    buf_len: usize,
) -> i32 {
    if let (Some(eng), false) = (engine.as_mut(), buffer.is_null()) {
        let slice = std::slice::from_raw_parts(buffer, buf_len);
        match eng.restore_snapshot(slice) {
            Ok(()) => 0,
            Err(_) => -2,
        }
    } else {
        -1
    }
}
