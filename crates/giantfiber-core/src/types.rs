//! C-ABI compatible data structures and types for GiantFiber (GF-1).

/// Discrete Reflex Actions matching Jev's System-1 Type Contract.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReflexAction {
    /// Nominal Cruise: No looming threat or threat below confidence threshold.
    Cruise = 0,
    /// Rapid emergency braking / reverse thrust.
    Brake = 1,
    /// High-G 90-degree knife-edge roll to the left.
    RollLeft90 = 2,
    /// High-G 90-degree knife-edge roll to the right.
    RollRight90 = 3,
    /// Rapid vertical climb evasion.
    PitchUp = 4,
    /// Rapid vertical dive evasion.
    PitchDown = 5,
    /// Quadruped / biped leg stiffening reflex (fall / impact arrest).
    StiffenLegs = 6,
    /// Hard circuit-breaker cutoff.
    EmergencyKill = 7,
}

impl ReflexAction {
    pub fn from_u32(val: u32) -> Self {
        match val {
            1 => ReflexAction::Brake,
            2 => ReflexAction::RollLeft90,
            3 => ReflexAction::RollRight90,
            4 => ReflexAction::PitchUp,
            5 => ReflexAction::PitchDown,
            6 => ReflexAction::StiffenLegs,
            7 => ReflexAction::EmergencyKill,
            _ => ReflexAction::Cruise,
        }
    }
}

/// 3-Axis Normalized Directional Deflection / Acceleration Vector.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Vector3D {
    pub x: f32,
    pub y: f32,
    pub z: f32,
}

impl Default for Vector3D {
    fn default() -> Self {
        Self { x: 0.0, y: 0.0, z: 0.0 }
    }
}

/// Neuromorphic Asynchronous Event Spike from DVS camera.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EventSpike {
    pub timestamp_us: u64,
    pub x: u16,
    pub y: u16,
    pub polarity: i8, // +1 (ON), -1 (OFF)
    pub _pad: [u8; 7],
}

impl Default for EventSpike {
    fn default() -> Self {
        Self {
            timestamp_us: 0,
            x: 0,
            y: 0,
            polarity: 1,
            _pad: [0; 7],
        }
    }
}

/// 6-Axis High-Rate IMU (Halteres biomimetic input).
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ImuData {
    pub timestamp_us: u64,
    pub gyro_x: f32,
    pub gyro_y: f32,
    pub gyro_z: f32,
    pub accel_x: f32,
    pub accel_y: f32,
    pub accel_z: f32,
}

impl Default for ImuData {
    fn default() -> Self {
        Self {
            timestamp_us: 0,
            gyro_x: 0.0,
            gyro_y: 0.0,
            gyro_z: 0.0,
            accel_x: 0.0,
            accel_y: 0.0,
            accel_z: 0.0,
        }
    }
}

/// System-1 Type-Safe Calibrated Decision Output Contract.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct ReflexOutput {
    /// Active Reflex Action (0: Cruise, 1: Brake, etc.)
    pub action: u32,
    /// Calibrated Confidence probability in [0.0, 1.0]
    pub confidence: f32,
    /// Normalized control deflection vector (-1.0 to 1.0)
    pub vector: Vector3D,
    /// Pure computation latency in microseconds (us)
    pub compute_latency_us: u32,
    /// Pathway that produced the output (1: Giant Fiber / DNp01, 2: non-GF descending pathway, 3: heading integrator)
    pub circuit_id: u32,
    /// Microsecond timestamp of decision
    pub timestamp_us: u64,
    /// 1 on the evaluation step where an escape is initiated (long mode) or upgraded (short mode)
    pub triggered: u8,
    /// Escape mode of the current episode: 0 none, 1 long mode (non-GF pathway), 2 short mode (Giant Fiber)
    pub escape_mode: u8,
    pub _pad: [u8; 6],
}

impl Default for ReflexOutput {
    fn default() -> Self {
        Self {
            action: ReflexAction::Cruise as u32,
            confidence: 0.0,
            vector: Vector3D::default(),
            compute_latency_us: 0,
            circuit_id: 0,
            timestamp_us: 0,
            triggered: 0,
            escape_mode: 0,
            _pad: [0; 6],
        }
    }
}

/// Engine configuration. Unless stated otherwise these are modelling parameters, not measured biology.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EngineConfig {
    /// Minimum softmax confidence for the decision gate to report an escape (default 0.85)
    pub confidence_threshold: f32,
    /// Softmax temperature of the decision gate (default 1.0)
    pub temperature: f32,
    /// Giant Fiber (DNp01) firing threshold, in units of normalised synaptic drive [0, 1] (default 0.55)
    pub looming_threshold: f32,
    /// Time-surface decay constant in microseconds (default 25000)
    pub decay_tau_us: f32,
    /// Refractory period after an escape episode, microseconds (default 300000)
    pub refractory_period_us: u64,
    /// Horizontal field of view of the event camera in degrees (default 90)
    pub fov_deg: f32,
    /// Giant Fiber membrane time constant, microseconds (default 10000)
    pub gf_tau_us: f32,
    /// Non-GF descending pathway threshold; lower than the GF threshold (von Reyn et al. 2014) (default 0.30)
    pub nongf_threshold: f32,
    /// Non-GF pathway time constant, microseconds (default 30000)
    pub nongf_tau_us: f32,
    /// LPLC2 half-activation angular size in degrees (default 20)
    pub lplc2_half_size_deg: f32,
    /// LC4 half-activation angular velocity in degrees per second (default 200)
    pub lc4_half_velocity_dps: f32,
    /// Window after non-GF onset in which a GF spike still converts the escape to short mode, microseconds (default 30000)
    pub long_mode_window_us: f32,
    /// Minimum number of recently active time-surface cells needed to estimate the stimulus (default 6)
    pub min_edge_cells: u32,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self {
            confidence_threshold: 0.85,
            temperature: 1.0,
            looming_threshold: 0.55,
            decay_tau_us: 25000.0,
            refractory_period_us: 300_000,
            fov_deg: 90.0,
            gf_tau_us: 10_000.0,
            nongf_threshold: 0.30,
            nongf_tau_us: 30_000.0,
            lplc2_half_size_deg: 20.0,
            lc4_half_velocity_dps: 200.0,
            long_mode_window_us: 30_000.0,
            min_edge_cells: 6,
        }
    }
}
