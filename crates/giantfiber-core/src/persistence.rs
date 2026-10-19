//! Zero-Loss Microsecond Binary Persistence for GiantFiber.
//!
//! Compact, fixed-size binary snapshot format with magic header `b"GF1\0"`.
//! Restores the engine state bit-exactly (checked by tests); see README for measured timings.

use core::mem::size_of;
use crate::types::{EngineConfig, Vector3D};
use crate::connectome::central_complex::NUM_WEDGES;

pub const SNAPSHOT_MAGIC: [u8; 4] = *b"GF1\0";
pub const SNAPSHOT_VERSION: u32 = 3;

/// Fixed 280-byte layout; padding fields are explicit so no uninitialised bytes are written.
#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct EngineSnapshot {
    pub magic: [u8; 4],
    pub version: u32,
    pub timestamp_us: u64,
    pub current_time_us: u64,
    pub last_fire_time_us: u64,
    pub last_imu_time_us: u64,
    pub v_gf: [f32; 2],
    pub v_nongf: [f32; 2],
    pub escape_mode: u32,
    pub _pad0: u32,
    pub episode_start_us: u64,
    pub nongf_onset_us: u64,
    pub last_step_us: u64,
    pub latched_roll: f32,
    pub latched_pitch: f32,
    pub expansion_rate: f32,
    pub centroid_x: f32,
    pub centroid_y: f32,
    pub radius: f32,
    pub prev_radius: f32,
    pub _pad1: u32,
    pub prev_radius_time_us: u64,
    pub heading_rad: f32,
    pub target_heading_rad: f32,
    pub correction_torque: Vector3D,
    pub wedges: [f32; NUM_WEDGES],
    pub _pad2: u32,
    pub config: EngineConfig,
    pub checksum: u32,
    pub _pad3: u32,
}

// All padding is explicit (and zeroed), so the serialized bytes and the checksum are deterministic.
const _: () = assert!(size_of::<EngineSnapshot>() == 280);
const _: () = assert!(size_of::<EngineConfig>() == 56);

impl EngineSnapshot {
    pub fn compute_checksum(&self) -> u32 {
        // Fast deterministic Adler-32 variant over all fields prior to checksum
        let checksum_offset = core::mem::offset_of!(Self, checksum);
        let bytes: &[u8] = unsafe {
            let ptr = self as *const Self as *const u8;
            core::slice::from_raw_parts(ptr, checksum_offset)
        };
        let mut a: u32 = 1;
        let mut b: u32 = 0;
        for &byte in bytes {
            a = (a + byte as u32) % 65521;
            b = (b + a) % 65521;
        }
        (b << 16) | a
    }

    pub fn serialize(&mut self, out_buffer: &mut [u8]) -> Result<usize, &'static str> {
        let size = size_of::<Self>();
        if out_buffer.len() < size {
            return Err("Buffer too small for snapshot");
        }
        self.magic = SNAPSHOT_MAGIC;
        self.version = SNAPSHOT_VERSION;
        self.checksum = self.compute_checksum();

        unsafe {
            let src = self as *const Self as *const u8;
            core::ptr::copy_nonoverlapping(src, out_buffer.as_mut_ptr(), size);
        }
        Ok(size)
    }

    pub fn deserialize(in_buffer: &[u8]) -> Result<Self, &'static str> {
        let size = size_of::<Self>();
        if in_buffer.len() < size {
            return Err("Input buffer smaller than snapshot struct");
        }

        let snapshot: Self = unsafe {
            let mut s = core::mem::MaybeUninit::<Self>::uninit();
            core::ptr::copy_nonoverlapping(in_buffer.as_ptr(), s.as_mut_ptr() as *mut u8, size);
            s.assume_init()
        };

        if snapshot.magic != SNAPSHOT_MAGIC {
            return Err("Invalid magic bytes in snapshot");
        }
        if snapshot.version != SNAPSHOT_VERSION {
            return Err("Incompatible snapshot version");
        }
        if snapshot.checksum != snapshot.compute_checksum() {
            return Err("Snapshot checksum mismatch");
        }

        Ok(snapshot)
    }
}
