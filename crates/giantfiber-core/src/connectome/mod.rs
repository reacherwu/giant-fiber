//! Drosophila Functional Connectome Sub-Circuits (FlyWire / MaleCNS Prior).
//!
//! Models biologically grounded neuronal pathways:
//! - `lobula_plate`: Horizontal (HS) & Vertical (VS) Tangential Cells for Optic Flow & Divergence
//! - `giant_fiber`: Looming-Sensitive Escape Circuit (Col4 -> GF -> TTMn / DLMn / PSI)
//! - `central_complex`: Heading Ring Attractor (E-PG / P-EN compass) for Post-Reflex Stabilization

pub mod giant_fiber;
pub mod lobula_plate;
pub mod central_complex;

pub use giant_fiber::{GiantFiberCircuit, EscapeMotorOutput};
pub use lobula_plate::{LobulaPlateCircuit, OpticFlowField};
pub use central_complex::{CentralComplexCircuit, CompassState};
