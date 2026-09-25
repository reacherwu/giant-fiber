//! Central Complex (CX) Heading Ring Attractor Circuit.
//!
//! Emulates the Drosophila compass system (E-PG & P-EN neurons in Ellipsoid Body / Protocerebral Bridge).
//! Maintains continuous heading orientation and provides post-reflex attitude stabilization.

use core::f32::consts::PI;
use crate::types::Vector3D;

pub const NUM_WEDGES: usize = 16;

/// State of the Heading Compass Ring Attractor.
#[derive(Debug, Clone, Copy)]
pub struct CompassState {
    /// Neural activity bump across the 16 compass wedges
    pub wedges: [f32; NUM_WEDGES],
    /// Decoded heading angle in radians [-PI, PI]
    pub heading_rad: f32,
    /// Heading certainty / bump strength [0.0, 1.0]
    pub bump_amplitude: f32,
    /// Post-reflex corrective torque to restore stable flight path
    pub correction_torque: Vector3D,
}

impl Default for CompassState {
    fn default() -> Self {
        let mut wedges = [0.0f32; NUM_WEDGES];
        wedges[0] = 1.0; // Initialized forward
        Self {
            wedges,
            heading_rad: 0.0,
            bump_amplitude: 1.0,
            correction_torque: Vector3D::default(),
        }
    }
}

/// Central Complex Ring Attractor.
pub struct CentralComplexCircuit {
    pub state: CompassState,
    pub target_heading_rad: f32,
}

impl CentralComplexCircuit {
    pub fn new() -> Self {
        let mut s = Self {
            state: CompassState::default(),
            target_heading_rad: 0.0,
        };
        s.init_bump(0.0);
        s
    }

    /// Reset compass to forward orientation (heading = 0.0).
    pub fn reset(&mut self) {
        self.target_heading_rad = 0.0;
        self.init_bump(0.0);
    }

    /// Initialize a single Gaussian activity bump at angle `theta_rad`.
    pub fn init_bump(&mut self, theta_rad: f32) {
        let step = (2.0 * PI) / (NUM_WEDGES as f32);
        for i in 0..NUM_WEDGES {
            let wedge_angle = -PI + (i as f32 + 0.5) * step;
            let diff = normalize_angle(wedge_angle - theta_rad);
            // Gaussian profile: exp(-diff^2 / (2 * sigma^2))
            let sigma = 0.5f32;
            self.state.wedges[i] = (-diff * diff / (2.0 * sigma * sigma)).exp();
        }
        self.decode_heading();
    }

    /// Integrate angular velocity (e.g. from IMU gyro_z or LPTC horizontal optic flow).
    pub fn update(&mut self, yaw_rate_rad_s: f32, dt_s: f32) -> CompassState {
        let d_theta = yaw_rate_rad_s * dt_s;
        let new_heading = normalize_angle(self.state.heading_rad + d_theta);
        self.init_bump(new_heading);

        // Compute corrective stabilization vector towards nominal target
        let heading_error = normalize_angle(self.target_heading_rad - self.state.heading_rad);
        self.state.correction_torque = Vector3D {
            x: 0.0,
            y: 0.0,
            z: (heading_error * 0.5).clamp(-1.0, 1.0),
        };

        self.state
    }

    /// Decode the current heading angle using population vector averaging.
    fn decode_heading(&mut self) {
        let step = (2.0 * PI) / (NUM_WEDGES as f32);
        let mut sin_sum = 0.0f32;
        let mut cos_sum = 0.0f32;
        let mut max_val = 0.0f32;

        for i in 0..NUM_WEDGES {
            let angle = -PI + (i as f32 + 0.5) * step;
            let w = self.state.wedges[i];
            sin_sum += w * angle.sin();
            cos_sum += w * angle.cos();
            if w > max_val {
                max_val = w;
            }
        }

        self.state.heading_rad = sin_sum.atan2(cos_sum);
        self.state.bump_amplitude = max_val.min(1.0);
    }
}

#[inline(always)]
fn normalize_angle(mut a: f32) -> f32 {
    while a > PI {
        a -= 2.0 * PI;
    }
    while a < -PI {
        a += 2.0 * PI;
    }
    a
}
