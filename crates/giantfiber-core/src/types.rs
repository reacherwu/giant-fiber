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
    /// Drosophila sub-circuit that fired (1: GiantFiber, 2: LobulaPlate, 3: CentralComplex)
    pub circuit_id: u32,
    /// Microsecond timestamp of decision
    pub timestamp_us: u64,
    /// 1 if reflex triggered above threshold, 0 if cruise fallback
    pub triggered: u8,
    pub _pad: [u8; 7],
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
            _pad: [0; 7],
        }
    }
}

/// Coprocessor Configuration Parameters.
#[repr(C)]
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct EngineConfig {
    /// Minimum calibrated confidence to trigger reflex action (default 0.85)
    pub confidence_threshold: f32,
    /// Temperature for softmax scaling calibration (default 1.0)
    pub temperature: f32,
    /// Looming expansion rate threshold (r/v ratio proxy)
    pub looming_threshold: f32,
    /// Exponential decay constant for time surface in microseconds (e.g. 25000 us)
    pub decay_tau_us: f32,
    /// Refractory period in microseconds after an escape reflex (e.g. 50000 us = 50ms)
    pub refractory_period_us: u64,
}

impl Default for EngineConfig {
    fn default() -> Self {
        Self {
            confidence_threshold: 0.85,
            temperature: 1.0,
            looming_threshold: 3.0,
            decay_tau_us: 25000.0,
            refractory_period_us: 50000,
        }
    }
}
