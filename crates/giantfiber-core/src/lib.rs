//! GiantFiber core (GF-1): an event-camera looming-escape reflex.
//!
//! Pipeline per evaluation step:
//! events -> 64x64 (or 128x128, feature `grid128`) time surface -> looming estimate (angular size, expansion rate)
//! -> LPLC2 / LC4 population activity -> Giant Fiber (DNp01) + non-GF descending pathway
//! (weights from FlyWire v783 synapse counts) -> short/long escape mode -> discrete action.
//!
//! Builds with `std` (default; adds the C ABI) or `no_std` (`--no-default-features --features libm`).

#![cfg_attr(not(feature = "std"), no_std)]

#[cfg(all(not(feature = "std"), not(feature = "libm")))]
compile_error!("giantfiber-core without `std` needs the `libm` feature for float math");

pub mod math;
pub mod types;
pub mod accumulator;
pub mod looming;
pub mod connectome;
pub mod calibrator;
pub mod persistence;
pub mod canfd;
pub mod farfield;

use types::{EventSpike, ImuData, ReflexOutput, EngineConfig};
use accumulator::EventAccumulator;
use looming::LoomingEstimator;
use connectome::{GiantFiberCircuit, CentralComplexCircuit, EscapeMode};
use calibrator::DecisionCalibrator;
use persistence::EngineSnapshot;

pub struct GiantFiberEngine {
    pub accumulator: EventAccumulator,
    pub looming: LoomingEstimator,
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
            looming: LoomingEstimator::new(),
            giant_fiber: GiantFiberCircuit::new(config.looming_threshold, config.refractory_period_us),
            central_complex: CentralComplexCircuit::new(),
            calibrator: DecisionCalibrator::new(config.temperature, config.confidence_threshold),
            config,
            last_imu_time_us: 0,
        }
    }

    pub fn reset(&mut self) {
        self.accumulator.reset();
        self.looming.reset();
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
        // Ignore out-of-order / duplicate samples so a stale timestamp cannot
        // produce a huge integration step on the next valid sample.
        if self.last_imu_time_us > 0 && imu.timestamp_us <= self.last_imu_time_us {
            return;
        }
        if self.last_imu_time_us > 0 {
            let dt_s = ((imu.timestamp_us - self.last_imu_time_us) as f32) / 1_000_000.0;
            self.central_complex.update(imu.gyro_z, dt_s);
        }
        self.last_imu_time_us = imu.timestamp_us;
    }

    /// One evaluation step. `now_us = 0` uses the latest event timestamp.
    pub fn step_eval(&mut self, now_us: u64) -> ReflexOutput {
        #[cfg(feature = "std")]
        let start = std::time::Instant::now();

        let current_us = if now_us > 0 { now_us } else { self.accumulator.current_time_us };
        self.accumulator.update_dynamics(current_us);
        let est = self.looming.update(&self.accumulator, current_us, self.config.fov_deg, self.config.min_edge_cells);
        let escape = self.giant_fiber.step(&est, &self.config, current_us);
        let compass_state = self.central_complex.state;
        #[cfg_attr(not(feature = "std"), allow(unused_mut))]
        let mut decision = self.calibrator.calibrate(&escape, &compass_state, &self.config, current_us, 0);

        #[cfg(feature = "std")]
        {
            decision.compute_latency_us = start.elapsed().as_micros() as u32;
        }
        decision
    }

    pub fn save_snapshot(&self, buffer: &mut [u8]) -> Result<usize, &'static str> {
        let gf = &self.giant_fiber;
        let mut snapshot = EngineSnapshot {
            magic: persistence::SNAPSHOT_MAGIC,
            version: persistence::SNAPSHOT_VERSION,
            timestamp_us: self.accumulator.current_time_us,
            current_time_us: self.accumulator.current_time_us,
            last_fire_time_us: gf.last_fire_time_us,
            last_imu_time_us: self.last_imu_time_us,
            v_gf: gf.v_gf,
            v_nongf: gf.v_nongf,
            escape_mode: gf.mode as u32,
            _pad0: 0,
            episode_start_us: gf.episode_start_us,
            nongf_onset_us: gf.nongf_onset_us,
            last_step_us: gf.last_step_us,
            latched_roll: gf.latched_roll,
            latched_pitch: gf.latched_pitch,
            expansion_rate: self.accumulator.expansion_rate,
            centroid_x: self.accumulator.centroid_x,
            centroid_y: self.accumulator.centroid_y,
            radius: self.accumulator.radius,
            prev_radius: self.accumulator.prev_radius,
            _pad1: 0,
            prev_radius_time_us: self.accumulator.prev_radius_time_us,
            heading_rad: self.central_complex.state.heading_rad,
            target_heading_rad: self.central_complex.target_heading_rad,
            correction_torque: self.central_complex.state.correction_torque,
            wedges: self.central_complex.state.wedges,
            _pad2: 0,
            config: self.config,
            checksum: 0,
            _pad3: 0,
        };
        snapshot.serialize(buffer)
    }

    /// Restores the scalar engine state. The time surface itself (timestamps) is not part of
    /// the snapshot, so recent events are lost on restore.
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
        let gf = &mut self.giant_fiber;
        gf.last_fire_time_us = snapshot.last_fire_time_us;
        gf.v_gf = snapshot.v_gf;
        gf.v_nongf = snapshot.v_nongf;
        gf.mode = EscapeMode::from_u8(snapshot.escape_mode as u8);
        gf.episode_start_us = snapshot.episode_start_us;
        gf.nongf_onset_us = snapshot.nongf_onset_us;
        gf.last_step_us = snapshot.last_step_us;
        gf.latched_roll = snapshot.latched_roll;
        gf.latched_pitch = snapshot.latched_pitch;
        self.central_complex.state.heading_rad = snapshot.heading_rad;
        self.central_complex.target_heading_rad = snapshot.target_heading_rad;
        self.central_complex.state.wedges = snapshot.wedges;
        self.central_complex.state.correction_torque = snapshot.correction_torque;
        self.central_complex.decode_heading();
        self.config = snapshot.config;

        self.accumulator.tau_us = self.config.decay_tau_us;
        gf.firing_threshold = self.config.looming_threshold;
        gf.nongf_threshold = self.config.nongf_threshold;
        gf.refractory_us = self.config.refractory_period_us;
        self.calibrator.temperature = self.config.temperature;
        self.calibrator.threshold = self.config.confidence_threshold;
        Ok(())
    }
}

#[cfg(feature = "std")]
pub mod ffi;

/// Highest closing speed (m/s) validated in simulation (PX4 SITL campaign v4: 0/66 collisions per tier
/// at 6-30 m/s, 95% CI upper bound 5.5%, 0/198 false triggers). Not flight-tested. Above this speed the
/// measured collision rate was 4.5-21.2%, because detection could not arm the required 0.5 s before contact.
pub const VALIDATED_MAX_CLOSING_SPEED_MPS: f32 = 30.0;
/// Minimum lead time (s) between arming and contact that produced zero collisions in v4.
pub const REQUIRED_LEAD_TIME_S: f32 = 0.5;
