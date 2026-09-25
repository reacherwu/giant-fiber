//! Lobula Plate Tangential Cells (LPTC) Circuit.
//!
//! Emulates Drosophila HS (Horizontal System) and VS (Vertical System) neurons
//! to extract wide-field optic flow, rotational velocity, and looming divergence.

use crate::accumulator::{EventAccumulator, SURFACE_WIDTH, SURFACE_HEIGHT};

/// Extracted Optic Flow Field and Divergence.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct OpticFlowField {
    /// Horizontal wide-field motion (HS activation): positive = rightward, negative = leftward
    pub hs_flow: f32,
    /// Vertical wide-field motion (VS activation): positive = downward, negative = upward
    pub vs_flow: f32,
    /// Flow divergence (expansion): positive = looming expansion, negative = contraction
    pub divergence: f32,
    /// Left-vs-Right asymmetric expansion bias (-1.0 to +1.0)
    pub horizontal_asymmetry: f32,
    /// Top-vs-Bottom asymmetric expansion bias (-1.0 to +1.0)
    pub vertical_asymmetry: f32,
}

impl Default for OpticFlowField {
    fn default() -> Self {
        Self {
            hs_flow: 0.0,
            vs_flow: 0.0,
            divergence: 0.0,
            horizontal_asymmetry: 0.0,
            vertical_asymmetry: 0.0,
        }
    }
}

/// Lobula Plate Processing Circuit.
pub struct LobulaPlateCircuit {
    pub prev_flow: OpticFlowField,
}

impl LobulaPlateCircuit {
    pub fn new() -> Self {
        Self {
            prev_flow: OpticFlowField::default(),
        }
    }

    /// Process the 2D time surface through LPTC receptive fields.
    /// Operates in sub-millisecond flat memory scan (64x64 grid).
    pub fn compute_flow(&mut self, accumulator: &EventAccumulator) -> OpticFlowField {
        let mid_x = SURFACE_WIDTH / 2;
        let mid_y = SURFACE_HEIGHT / 2;

        let mut hs_sum = 0.0f32;
        let mut vs_sum = 0.0f32;
        let mut div_sum = 0.0f32;
        let mut left_energy = 0.0f32;
        let mut right_energy = 0.0f32;
        let mut top_energy = 0.0f32;
        let mut bottom_energy = 0.0f32;

        // Sample spatial gradients with step size 2 for microsecond execution
        let step = 2;
        for y in (1..(SURFACE_HEIGHT - 1)).step_by(step) {
            for x in (1..(SURFACE_WIDTH - 1)).step_by(step) {
                let s_curr = accumulator.get_surface_value(x, y);
                if s_curr.abs() < 0.005 {
                    continue;
                }

                let s_right = accumulator.get_surface_value(x + 1, y);
                let s_left = accumulator.get_surface_value(x - 1, y);
                let s_down = accumulator.get_surface_value(x, y + 1);
                let s_up = accumulator.get_surface_value(x, y - 1);

                // Spatial gradients (approximate optical flow components)
                let grad_x = (s_right - s_left) * 0.5;
                let grad_y = (s_down - s_up) * 0.5;

                hs_sum += grad_x;
                vs_sum += grad_y;

                // Divergence component relative to center: (x - cx)*vx + (y - cy)*vy
                let dx = (x as f32) - (mid_x as f32);
                let dy = (y as f32) - (mid_y as f32);
                let radial_div = dx * grad_x + dy * grad_y;
                div_sum += radial_div;

                let energy = s_curr.abs();
                if x < mid_x {
                    left_energy += energy;
                } else {
                    right_energy += energy;
                }

                if y < mid_y {
                    top_energy += energy;
                } else {
                    bottom_energy += energy;
                }
            }
        }

        // Normalize
        let total_energy = (left_energy + right_energy).max(0.1);
        let horiz_asym = (right_energy - left_energy) / total_energy;
        let total_vert = (top_energy + bottom_energy).max(0.1);
        let vert_asym = (bottom_energy - top_energy) / total_vert;

        // Combine with accumulator's temporal expansion rate
        let combined_divergence = (div_sum * 0.001) + (accumulator.expansion_rate * 0.8);

        let flow = OpticFlowField {
            hs_flow: hs_sum * 0.002,
            vs_flow: vs_sum * 0.002,
            divergence: combined_divergence,
            horizontal_asymmetry: horiz_asym.clamp(-1.0, 1.0),
            vertical_asymmetry: vert_asym.clamp(-1.0, 1.0),
        };

        self.prev_flow = flow;
        flow
    }
}
