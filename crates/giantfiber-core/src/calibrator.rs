//! Jev-Style System-1 Discrete Logit Calibration & Gatekeeper.
//!
//! Replaces hallucinated text generation with temperature-scaled, statistically calibrated
//! probability evaluation over finite discrete action space.

use crate::types::{ReflexAction, Vector3D, ReflexOutput, EngineConfig};
use crate::connectome::giant_fiber::EscapeMotorOutput;
use crate::connectome::central_complex::CompassState;

pub const NUM_ACTIONS: usize = 8;

pub struct DecisionCalibrator {
    pub temperature: f32,
    pub threshold: f32,
}

impl DecisionCalibrator {
    pub fn new(temperature: f32, threshold: f32) -> Self {
        Self {
            temperature: if temperature > 0.01 { temperature } else { 1.0 },
            threshold: if threshold > 0.0 { threshold } else { 0.85 },
        }
    }

    /// Calibrate raw escape circuit outputs and heading stabilization into a
    /// type-safe ReflexOutput contract.
    pub fn calibrate(
        &self,
        escape: &EscapeMotorOutput,
        compass: &CompassState,
        config: &EngineConfig,
        timestamp_us: u64,
        latency_us: u32,
    ) -> ReflexOutput {
        let temp = if config.temperature > 0.01 { config.temperature } else { self.temperature };
        let thresh = if config.confidence_threshold > 0.0 { config.confidence_threshold } else { self.threshold };

        // Construct 8 action logits
        let mut logits = [-3.0f32; NUM_ACTIONS];
        // Cruise is default baseline
        logits[ReflexAction::Cruise as usize] = 0.5;

        if escape.gf_fired {
            // Assign high logit to the anatomically recommended evasion action
            let act_idx = escape.recommended_action as usize;
            if act_idx < NUM_ACTIONS {
                logits[act_idx] = escape.raw_logit.max(2.0);
                // Lower baseline cruise logit when escape fires
                logits[ReflexAction::Cruise as usize] = -2.0;
            }
        }

        // Apply Temperature Scaling
        let mut scaled = [0.0f32; NUM_ACTIONS];
        let mut max_scaled = -1e9f32;
        for i in 0..NUM_ACTIONS {
            scaled[i] = logits[i] / temp;
            if scaled[i] > max_scaled {
                max_scaled = scaled[i];
            }
        }

        // Softmax normalization
        let mut sum_exp = 0.0f32;
        let mut probs = [0.0f32; NUM_ACTIONS];
        for i in 0..NUM_ACTIONS {
            let exp_val = (scaled[i] - max_scaled).exp();
            probs[i] = exp_val;
            sum_exp += exp_val;
        }

        let mut best_action_idx = 0;
        let mut best_prob = 0.0f32;
        for i in 0..NUM_ACTIONS {
            probs[i] /= sum_exp;
            if probs[i] > best_prob {
                best_prob = probs[i];
                best_action_idx = i;
            }
        }

        let best_action = ReflexAction::from_u32(best_action_idx as u32);

        // System-1 Gatekeeper Decision Contract:
        // Only trigger non-cruise escape maneuver if confidence >= threshold
        if escape.gf_fired && best_prob >= thresh && best_action != ReflexAction::Cruise {
            ReflexOutput {
                action: best_action as u32,
                confidence: best_prob,
                vector: Vector3D {
                    x: escape.roll_deflection,
                    y: escape.pitch_deflection,
                    z: escape.thrust_magnitude,
                },
                compute_latency_us: latency_us,
                circuit_id: 1, // GiantFiber_L1
                timestamp_us,
                triggered: 1,
                _pad: [0; 7],
            }
        } else {
            // Safe fallback: Cruise with heading stabilization torque from Central Complex
            ReflexOutput {
                action: ReflexAction::Cruise as u32,
                confidence: probs[ReflexAction::Cruise as usize],
                vector: compass.correction_torque,
                compute_latency_us: latency_us,
                circuit_id: 3, // CentralComplex_Compass
                timestamp_us,
                triggered: 0,
                _pad: [0; 7],
            }
        }
    }
}
