//! Drosophila Giant Fiber System (GFS) Looming & Escape Circuit.
//!
//! Presynaptic inputs from Lobula Col4 & LPTCs converge onto the Giant Fiber (GF),
//! which fires into TTMn (jump/thrust) and DLMn/PSI (wing depression and directional roll).

use crate::types::ReflexAction;
use super::lobula_plate::OpticFlowField;

/// Motor Neuron Outputs from the Giant Fiber Pathway.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EscapeMotorOutput {
    /// Whether the Giant Fiber reached all-or-none spike threshold
    pub gf_fired: bool,
    /// Raw uncalibrated activation logit
    pub raw_logit: f32,
    /// Motor drive intensity [0.0, 1.0]
    pub thrust_magnitude: f32,
    /// Lateral evasion bias: negative = evade left, positive = evade right
    pub roll_deflection: f32,
    /// Vertical evasion bias: positive = pitch up, negative = dive down
    pub pitch_deflection: f32,
    /// Recommended discrete action candidate
    pub recommended_action: ReflexAction,
}

impl Default for EscapeMotorOutput {
    fn default() -> Self {
        Self {
            gf_fired: false,
            raw_logit: -5.0,
            thrust_magnitude: 0.0,
            roll_deflection: 0.0,
            pitch_deflection: 0.0,
            recommended_action: ReflexAction::Cruise,
        }
    }
}

/// Giant Fiber Escape Circuit State.
pub struct GiantFiberCircuit {
    /// Membrane voltage proxy for Giant Fiber neuron
    pub v_membrane: f32,
    /// Threshold to trigger all-or-none escape reflex
    pub firing_threshold: f32,
    /// Timestamp (us) of last firing
    pub last_fire_time_us: u64,
    /// Refractory period (us)
    pub refractory_us: u64,
    /// Col4 looming sensitivity weight
    pub col4_weight: f32,
    /// LPTC divergence weight
    pub lptc_weight: f32,
}

impl GiantFiberCircuit {
    pub fn new(firing_threshold: f32, refractory_us: u64) -> Self {
        Self {
            v_membrane: 0.0,
            firing_threshold: if firing_threshold > 0.0 { firing_threshold } else { 0.65 },
            last_fire_time_us: 0,
            refractory_us: if refractory_us > 0 { refractory_us } else { 50000 },
            col4_weight: 1.4,
            lptc_weight: 1.0,
        }
    }

    /// Reset internal state.
    pub fn reset(&mut self) {
        self.v_membrane = 0.0;
        self.last_fire_time_us = 0;
    }

    /// Evaluate escape trigger based on current optic flow and looming expansion.
    pub fn step(
        &mut self,
        flow: &OpticFlowField,
        expansion_rate: f32,
        now_us: u64,
    ) -> EscapeMotorOutput {
        // Check refractory state (only if previously fired)
        if self.last_fire_time_us > 0 && now_us < self.last_fire_time_us + self.refractory_us {
            return EscapeMotorOutput {
                gf_fired: false,
                raw_logit: -3.0,
                thrust_magnitude: 0.0,
                roll_deflection: 0.0,
                pitch_deflection: 0.0,
                recommended_action: ReflexAction::Cruise,
            };
        }

        // Col4 looming signal: non-linear expansion velocity
        let looming_stimulus = expansion_rate.max(0.0) * self.col4_weight;
        let lptc_stimulus = flow.divergence.max(0.0) * self.lptc_weight;

        // Giant Fiber leaky integration
        let leak_factor = 0.85f32;
        self.v_membrane = (self.v_membrane * leak_factor) + (looming_stimulus + lptc_stimulus);

        let fired = self.v_membrane >= self.firing_threshold;
        let raw_logit = (self.v_membrane - self.firing_threshold) * 5.0;

        if fired {
            self.last_fire_time_us = now_us;
            self.v_membrane = 0.0; // Reset after spike

            // Biological evasion steering: Fly always evades AWAY from threat location!
            // If threat is on the right (horizontal_asymmetry > 0), roll LEFT (-1.0).
            // If threat is on the left (horizontal_asymmetry < 0), roll RIGHT (+1.0).
            let roll = if flow.horizontal_asymmetry.abs() > 0.15 {
                -flow.horizontal_asymmetry.signum()
            } else {
                // Symmetrical frontal threat: choose rapid 90-degree knife roll right or climb
                1.0
            };

            let pitch = if flow.vertical_asymmetry > 0.2 {
                // Threat from bottom -> pitch up
                1.0
            } else if flow.vertical_asymmetry < -0.2 {
                // Threat from above -> dive down
                -1.0
            } else {
                0.2 // Slight positive climb bias
            };

            // Map continuous direction to discrete ReflexAction
            let action = if roll < -0.5 {
                ReflexAction::RollLeft90
            } else if roll > 0.5 {
                ReflexAction::RollRight90
            } else if pitch > 0.5 {
                ReflexAction::PitchUp
            } else if pitch < -0.5 {
                ReflexAction::PitchDown
            } else {
                ReflexAction::Brake
            };

            EscapeMotorOutput {
                gf_fired: true,
                raw_logit,
                thrust_magnitude: 1.0,
                roll_deflection: roll,
                pitch_deflection: pitch,
                recommended_action: action,
            }
        } else {
            EscapeMotorOutput {
                gf_fired: false,
                raw_logit,
                thrust_magnitude: 0.0,
                roll_deflection: 0.0,
                pitch_deflection: 0.0,
                recommended_action: ReflexAction::Cruise,
            }
        }
    }
}
