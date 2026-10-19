//! C ABI (used by the Python ctypes binding). Requires `std` (heap-allocated engine handle).

use crate::types::{EventSpike, ImuData, ReflexOutput, EngineConfig};
use crate::persistence::EngineSnapshot;
use crate::GiantFiberEngine;

/// Internal state exposed for analysis and plotting.
#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
pub struct GfDebugState {
    pub theta_deg: f32,
    pub theta_dot_dps: f32,
    pub centroid_x: f32,
    pub centroid_y: f32,
    pub left_fraction: f32,
    pub radial_cv: f32,
    pub lplc2: [f32; 2],
    pub lc4: [f32; 2],
    pub v_gf: [f32; 2],
    pub v_nongf: [f32; 2],
    pub edge_cells: u32,
    pub valid: u32,
    pub escape_mode: u32,
    pub _pad: u32,
    pub last_gf_spike_us: u64,
}

/// # Safety
/// `engine` must come from `gf_engine_new`; `out` must be a valid pointer.
#[no_mangle]
/// # Safety
/// Pointers must be valid; `engine` must come from `gf_engine_new`.
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn gf_get_debug_state(engine: *const GiantFiberEngine, out: *mut GfDebugState) -> i32 {
    if let (Some(eng), Some(o)) = (engine.as_ref(), out.as_mut()) {
        let e = eng.looming.estimate;
        let g = &eng.giant_fiber;
        *o = GfDebugState {
            theta_deg: e.theta_deg,
            theta_dot_dps: e.theta_dot_dps,
            centroid_x: e.centroid_x,
            centroid_y: e.centroid_y,
            left_fraction: e.left_fraction,
            radial_cv: e.radial_cv,
            lplc2: g.last_vpn.lplc2,
            lc4: g.last_vpn.lc4,
            v_gf: g.v_gf,
            v_nongf: g.v_nongf,
            edge_cells: e.edge_cells,
            valid: e.valid as u32,
            escape_mode: g.mode as u32,
            _pad: 0,
            last_gf_spike_us: g.last_fire_time_us,
        };
        0
    } else {
        -1
    }
}


#[no_mangle]
/// # Safety
/// Pointers must be valid; `engine` must come from `gf_engine_new`.
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn gf_engine_new(config: *const EngineConfig) -> *mut GiantFiberEngine {
    let cfg = if !config.is_null() {
        *config
    } else {
        EngineConfig::default()
    };
    Box::into_raw(Box::new(GiantFiberEngine::new(cfg)))
}

#[no_mangle]
/// # Safety
/// Pointers must be valid; `engine` must come from `gf_engine_new`.
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn gf_engine_free(engine: *mut GiantFiberEngine) {
    if !engine.is_null() {
        drop(Box::from_raw(engine));
    }
}

#[no_mangle]
/// # Safety
/// Pointers must be valid; `engine` must come from `gf_engine_new`.
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn gf_engine_reset(engine: *mut GiantFiberEngine) {
    if let Some(eng) = engine.as_mut() {
        eng.reset();
    }
}

#[no_mangle]
/// # Safety
/// Pointers must be valid; `engine` must come from `gf_engine_new`.
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn gf_feed_event(
    engine: *mut GiantFiberEngine,
    spike: *const EventSpike,
    raw_w: u16,
    raw_h: u16,
) {
    if let (Some(eng), Some(spk)) = (engine.as_mut(), spike.as_ref()) {
        eng.feed_event(spk, raw_w, raw_h);
    }
}

#[no_mangle]
/// # Safety
/// Pointers must be valid; `engine` must come from `gf_engine_new`.
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn gf_feed_events_batch(
    engine: *mut GiantFiberEngine,
    spikes: *const EventSpike,
    count: usize,
    raw_w: u16,
    raw_h: u16,
) {
    if let (Some(eng), false) = (engine.as_mut(), spikes.is_null()) {
        let slice = std::slice::from_raw_parts(spikes, count);
        eng.feed_events_batch(slice, raw_w, raw_h);
    }
}

#[no_mangle]
/// # Safety
/// Pointers must be valid; `engine` must come from `gf_engine_new`.
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn gf_update_imu(engine: *mut GiantFiberEngine, imu: *const ImuData) {
    if let (Some(eng), Some(i)) = (engine.as_mut(), imu.as_ref()) {
        eng.update_imu(i);
    }
}

#[no_mangle]
/// # Safety
/// Pointers must be valid; `engine` must come from `gf_engine_new`.
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn gf_step_eval(
    engine: *mut GiantFiberEngine,
    now_us: u64,
    out_result: *mut ReflexOutput,
) -> i32 {
    if let (Some(eng), Some(out)) = (engine.as_mut(), out_result.as_mut()) {
        *out = eng.step_eval(now_us);
        0
    } else {
        -1
    }
}

#[no_mangle]
/// # Safety
/// Pointers must be valid; `engine` must come from `gf_engine_new`.
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn gf_get_snapshot_size() -> usize {
    core::mem::size_of::<EngineSnapshot>()
}

#[no_mangle]
/// # Safety
/// Pointers must be valid; `engine` must come from `gf_engine_new`.
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn gf_save_snapshot(
    engine: *mut GiantFiberEngine,
    buffer: *mut u8,
    buf_len: usize,
) -> i32 {
    if let (Some(eng), false) = (engine.as_mut(), buffer.is_null()) {
        let slice = std::slice::from_raw_parts_mut(buffer, buf_len);
        match eng.save_snapshot(slice) {
            Ok(bytes_written) => bytes_written as i32,
            Err(_) => -2,
        }
    } else {
        -1
    }
}

#[no_mangle]
/// # Safety
/// Pointers must be valid; `engine` must come from `gf_engine_new`.
#[allow(clippy::missing_safety_doc)]
pub unsafe extern "C" fn gf_restore_snapshot(
    engine: *mut GiantFiberEngine,
    buffer: *const u8,
    buf_len: usize,
) -> i32 {
    if let (Some(eng), false) = (engine.as_mut(), buffer.is_null()) {
        let slice = std::slice::from_raw_parts(buffer, buf_len);
        match eng.restore_snapshot(slice) {
            Ok(()) => 0,
            Err(_) => -2,
        }
    } else {
        -1
    }
}

/// Encodes the 24-byte CAN-FD reflex frame for `output` into `out` (must hold 24 bytes).
/// Returns the number of bytes written, or -1 on a null pointer.
///
/// # Safety
/// `output` must be valid; `out` must point to at least 24 writable bytes.
#[no_mangle]
pub unsafe extern "C" fn gf_canfd_encode(output: *const ReflexOutput, sequence: u16, timestamp_ms: u32, out: *mut u8) -> i32 {
    if output.is_null() || out.is_null() {
        return -1;
    }
    let frame = crate::canfd::ReflexFrame::from_output(&*output, sequence, timestamp_ms).encode();
    core::ptr::copy_nonoverlapping(frame.as_ptr(), out, frame.len());
    frame.len() as i32
}
