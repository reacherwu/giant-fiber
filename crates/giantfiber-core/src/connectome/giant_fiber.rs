//! Looming-escape circuit: LPLC2 / LC4 -> Giant Fiber (DNp01) and the parallel non-GF pathway.
//!
//! Structure (what is taken from data vs. what is a modelling choice):
//!
//! * Connectivity -- FROM DATA. The relative weights of the LPLC2 and LC4 inputs onto each Giant Fiber
//!   and onto the non-GF descending neurons are the FlyWire v783 synapse counts in [`super::data`],
//!   normalised per target: `w_LPLC2 = syn(LPLC2->target) / (syn(LPLC2->target) + syn(LC4->target))`.
//!   All LPLC2/LC4 -> GF connections in FlyWire v783 are ipsilateral, so each eye drives its own GF.
//! * Stimulus encoding -- FROM LITERATURE, parameters are modelling choices. LPLC2 is driven by angular
//!   size of an expanding stimulus, LC4 by angular velocity (Ache et al. 2019; von Reyn et al. 2017).
//!   The half-activation constants (`lplc2_half_size_deg`, `lc4_half_velocity_dps`) are not measured values.
//! * Action selection -- FROM LITERATURE. von Reyn et al. 2014 showed that the GF has a higher activation
//!   threshold than parallel escape circuits; parallel (non-GF) activation starts a slow "long-mode"
//!   takeoff, and a GF spike that arrives early enough overrides it with a fast "short-mode" takeoff.
//!   Here: non-GF pathway crossing threshold -> long-mode escape; GF spike before onset+window -> short mode.
//! * Membrane dynamics -- MODELLING CHOICE. Both pathways are leaky integrators; time constants and
//!   thresholds are configurable and not fitted to electrophysiology.
//! * Escape direction -- ENGINEERING CHOICE. The vehicle moves away from the stimulus centroid
//!   (flies do steer away from the looming side, Card & Dickinson 2008, but that steering is not
//!   implemented from circuit data here).

use super::data::*;
use crate::looming::LoomingEstimate;
use crate::math::sigmoid;
use crate::types::{EngineConfig, ReflexAction};

pub const LEFT: usize = 0;
pub const RIGHT: usize = 1;

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EscapeMode {
    None = 0,
    /// Initiated by the non-GF descending pathway (slower, more stable takeoff in the fly)
    Long = 1,
    /// Initiated or upgraded by a Giant Fiber spike (fast takeoff in the fly)
    Short = 2,
}

impl EscapeMode {
    pub fn from_u8(v: u8) -> Self {
        match v { 1 => EscapeMode::Long, 2 => EscapeMode::Short, _ => EscapeMode::None }
    }
}

/// Normalised input weights of one target pathway, `[left, right]`.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct PathwayWeights {
    pub lplc2: [f32; 2],
    pub lc4: [f32; 2],
}

fn normalise(lplc2: u32, lc4: u32) -> (f32, f32) {
    let t = (lplc2 + lc4).max(1) as f32;
    (lplc2 as f32 / t, lc4 as f32 / t)
}

/// LPLC2/LC4 -> DNp01 weights from FlyWire v783 synapse counts.
pub fn gf_weights() -> PathwayWeights {
    let (l0, c0) = normalise(LPLC2_LEFT_TO_GF_SYN, LC4_LEFT_TO_GF_SYN);
    let (l1, c1) = normalise(LPLC2_RIGHT_TO_GF_SYN, LC4_RIGHT_TO_GF_SYN);
    PathwayWeights { lplc2: [l0, l1], lc4: [c0, c1] }
}

/// LPLC2/LC4 -> non-GF descending neuron weights from FlyWire v783 synapse counts.
pub fn nongf_weights() -> PathwayWeights {
    let (l0, c0) = normalise(LPLC2_LEFT_TO_NONGF_DN_SYN, LC4_LEFT_TO_NONGF_DN_SYN);
    let (l1, c1) = normalise(LPLC2_RIGHT_TO_NONGF_DN_SYN, LC4_RIGHT_TO_NONGF_DN_SYN);
    PathwayWeights { lplc2: [l0, l1], lc4: [c0, c1] }
}

/// Population activity of the visual projection neurons, `[left, right]`, each in [0, 1].
#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct VpnActivity {
    pub lplc2: [f32; 2],
    pub lc4: [f32; 2],
}

/// Map a looming estimate to LPLC2 / LC4 population activity.
pub fn vpn_activity(est: &LoomingEstimate, cfg: &EngineConfig) -> VpnActivity {
    if !est.valid {
        return VpnActivity::default();
    }
    let td = est.theta_dot_dps.max(0.0);
    let v50 = cfg.lc4_half_velocity_dps.max(1.0);
    // LC4: saturating function of angular velocity
    let lc4 = td / (td + v50);
    // LPLC2: sigmoidal in angular size, only for expanding stimuli (LPLC2 needs outward motion)
    let s50 = cfg.lplc2_half_size_deg.max(1.0);
    let size_term = sigmoid((est.theta_deg - s50) / (0.25 * s50));
    let expanding = (td / (0.25 * v50)).clamp(0.0, 1.0);
    let lplc2 = size_term * expanding;
    // Engineering noise gate (not biology): diffuse sensor noise has a large radial spread
    let coherence = ((0.32 - est.radial_cv) / 0.1).clamp(0.0, 1.0);
    // Approximation of LPLC2's requirement for outward motion in all directions (Klapoetke et al. 2017):
    // suppress stimuli whose image centroid moves faster than their edges expand.
    let radial = (2.0 - est.translation_ratio).clamp(0.0, 1.0);
    let coherence = coherence * radial;
    // Each eye sees its own hemifield plus the frontal overlap: a centred stimulus drives both sides
    let left = (2.0 * est.left_fraction).min(1.0);
    let right = (2.0 * (1.0 - est.left_fraction)).min(1.0);
    VpnActivity {
        lplc2: [lplc2 * left * coherence, lplc2 * right * coherence],
        lc4: [lc4 * left * coherence, lc4 * right * coherence],
    }
}

/// Output of one circuit step.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EscapeMotorOutput {
    /// True on the step where the GF spiked
    pub gf_fired: bool,
    /// True on the step where the non-GF pathway crossed threshold
    pub nongf_fired: bool,
    /// True if an escape was initiated or upgraded on this step
    pub initiated: bool,
    pub mode: EscapeMode,
    /// Membrane margin of the winning pathway relative to its threshold
    pub raw_logit: f32,
    /// 1.0 for short mode, 0.5 for long mode
    pub thrust_magnitude: f32,
    /// Lateral escape direction: -1 = move left, +1 = move right
    pub roll_deflection: f32,
    /// Vertical escape direction: +1 = climb, -1 = descend
    pub pitch_deflection: f32,
    pub recommended_action: ReflexAction,
}

impl Default for EscapeMotorOutput {
    fn default() -> Self {
        Self {
            gf_fired: false,
            nongf_fired: false,
            initiated: false,
            mode: EscapeMode::None,
            raw_logit: -5.0,
            thrust_magnitude: 0.0,
            roll_deflection: 0.0,
            pitch_deflection: 0.0,
            recommended_action: ReflexAction::Cruise,
        }
    }
}

pub struct GiantFiberCircuit {
    pub gf_w: PathwayWeights,
    pub nongf_w: PathwayWeights,
    /// GF (DNp01) membrane state, `[left, right]`, normalised units
    pub v_gf: [f32; 2],
    /// Non-GF descending pathway state, `[left, right]`
    pub v_nongf: [f32; 2],
    pub firing_threshold: f32,
    pub nongf_threshold: f32,
    pub refractory_us: u64,
    pub mode: EscapeMode,
    pub episode_start_us: u64,
    pub nongf_onset_us: u64,
    pub last_fire_time_us: u64,
    pub last_step_us: u64,
    pub last_vpn: VpnActivity,
    /// Escape direction latched at episode start
    pub latched_roll: f32,
    pub latched_pitch: f32,
}

impl GiantFiberCircuit {
    pub fn new(firing_threshold: f32, refractory_us: u64) -> Self {
        Self {
            gf_w: gf_weights(),
            nongf_w: nongf_weights(),
            v_gf: [0.0; 2],
            v_nongf: [0.0; 2],
            firing_threshold: if firing_threshold > 0.0 { firing_threshold } else { 0.55 },
            nongf_threshold: 0.30,
            refractory_us,
            mode: EscapeMode::None,
            episode_start_us: 0,
            nongf_onset_us: 0,
            last_fire_time_us: 0,
            last_step_us: 0,
            last_vpn: VpnActivity::default(),
            latched_roll: 0.0,
            latched_pitch: 0.0,
        }
    }

    pub fn reset(&mut self) {
        let thr = self.firing_threshold;
        let ng = self.nongf_threshold;
        let refr = self.refractory_us;
        *self = Self::new(thr, refr);
        self.nongf_threshold = ng;
    }

    /// Largest GF membrane value (used for reporting / snapshots).
    pub fn v_membrane(&self) -> f32 {
        self.v_gf[LEFT].max(self.v_gf[RIGHT])
    }

    pub fn step(&mut self, est: &LoomingEstimate, cfg: &EngineConfig, now_us: u64) -> EscapeMotorOutput {
        self.firing_threshold = cfg.looming_threshold;
        self.nongf_threshold = cfg.nongf_threshold;
        self.refractory_us = cfg.refractory_period_us;

        let dt_us = if self.last_step_us == 0 || now_us <= self.last_step_us {
            0.0
        } else {
            ((now_us - self.last_step_us) as f32).min(50_000.0)
        };
        self.last_step_us = now_us;

        let vpn = vpn_activity(est, cfg);
        self.last_vpn = vpn;
        let a_gf = (dt_us / cfg.gf_tau_us.max(100.0)).min(1.0);
        let a_ng = (dt_us / cfg.nongf_tau_us.max(100.0)).min(1.0);
        let mut gf_spike = false;
        let mut ng_cross = false;
        for s in [LEFT, RIGHT] {
            let i_gf = self.gf_w.lplc2[s] * vpn.lplc2[s] + self.gf_w.lc4[s] * vpn.lc4[s];
            let i_ng = self.nongf_w.lplc2[s] * vpn.lplc2[s] + self.nongf_w.lc4[s] * vpn.lc4[s];
            let prev_ng = self.v_nongf[s];
            self.v_gf[s] += a_gf * (i_gf - self.v_gf[s]);
            self.v_nongf[s] += a_ng * (i_ng - self.v_nongf[s]);
            if self.v_gf[s] >= self.firing_threshold {
                gf_spike = true;
            }
            if prev_ng < self.nongf_threshold && self.v_nongf[s] >= self.nongf_threshold {
                ng_cross = true;
            }
        }
        let margin_gf = self.v_membrane() - self.firing_threshold;
        let margin_ng = self.v_nongf[LEFT].max(self.v_nongf[RIGHT]) - self.nongf_threshold;
        if gf_spike {
            // all-or-none spike, then reset
            self.v_gf = [0.0; 2];
            self.last_fire_time_us = now_us;
        }

        // End an episode after the refractory period
        if self.mode != EscapeMode::None && now_us >= self.episode_start_us + self.refractory_us {
            self.mode = EscapeMode::None;
        }

        let mut initiated = false;
        match self.mode {
            EscapeMode::None => {
                if gf_spike {
                    self.mode = EscapeMode::Short;
                    initiated = true;
                } else if ng_cross {
                    self.mode = EscapeMode::Long;
                    self.nongf_onset_us = now_us;
                    initiated = true;
                }
                if initiated {
                    self.episode_start_us = now_us;
                    self.latch_direction(est);
                }
            }
            EscapeMode::Long => {
                if gf_spike && (now_us as f32) <= self.nongf_onset_us as f32 + cfg.long_mode_window_us {
                    self.mode = EscapeMode::Short;
                    initiated = true;
                }
            }
            EscapeMode::Short => {}
        }

        if !initiated {
            return EscapeMotorOutput {
                gf_fired: gf_spike,
                nongf_fired: ng_cross,
                initiated: false,
                mode: self.mode,
                raw_logit: margin_gf.max(margin_ng) * 10.0,
                ..EscapeMotorOutput::default()
            };
        }

        let roll = self.latched_roll;
        let pitch = self.latched_pitch;
        let action = if pitch.abs() > roll.abs() {
            if pitch > 0.0 { ReflexAction::PitchUp } else { ReflexAction::PitchDown }
        } else if roll < 0.0 {
            ReflexAction::RollLeft90
        } else {
            ReflexAction::RollRight90
        };
        EscapeMotorOutput {
            gf_fired: gf_spike,
            nongf_fired: ng_cross,
            initiated: true,
            mode: self.mode,
            raw_logit: if gf_spike { margin_gf.max(0.0) } else { margin_ng.max(0.0) } * 10.0,
            thrust_magnitude: if self.mode == EscapeMode::Short { 1.0 } else { 0.5 },
            roll_deflection: roll,
            pitch_deflection: pitch,
            recommended_action: action,
        }
    }

    fn latch_direction(&mut self, est: &LoomingEstimate) {
        let half = crate::accumulator::SURFACE_WIDTH as f32 * 0.5;
        let ox = (est.centroid_x - half) / half; // +: stimulus on the right
        let oy = (est.centroid_y - half) / half; // +: stimulus below the optical axis
        // Move away from the stimulus. Centred stimulus: default to the right.
        self.latched_roll = if ox > 0.0 { -1.0 } else { 1.0 };
        // Only use the vertical axis when the stimulus is clearly above/below
        self.latched_pitch = if oy.abs() > 0.5 && oy.abs() > 1.5 * ox.abs() {
            if oy > 0.0 { 1.0 } else { -1.0 }
        } else {
            0.0
        };
    }
}
