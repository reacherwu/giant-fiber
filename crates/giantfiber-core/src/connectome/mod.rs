//! Drosophila looming-escape circuit model.
//!
//! - `data`: GENERATED FlyWire v783 / MANC v1.2.1 neuron IDs and synapse counts (see tools/connectome)
//! - `giant_fiber`: LPLC2 / LC4 -> Giant Fiber (DNp01) + non-GF descending pathway, short/long escape modes
//! - `central_complex`: simple heading integrator (not connectome-derived)

pub mod data;
pub mod giant_fiber;
pub mod central_complex;

pub use giant_fiber::{EscapeMode, EscapeMotorOutput, GiantFiberCircuit, VpnActivity};
pub use central_complex::{CentralComplexCircuit, CompassState};
