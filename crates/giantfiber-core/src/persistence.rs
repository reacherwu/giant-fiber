//! Zero-Loss Microsecond Binary Persistence for GiantFiber.
//!
//! Compact, fixed-size binary snapshot format with magic header `b"GF1\0"`.
//! Guarantees sub-millisecond save/restore with 100% bit-exact state recovery.

use core::mem::size_of;
use crate::types::EngineConfig;
use crate::connectome::central_complex::NUM_WEDGES;

pub const SNAPSHOT_MAGIC: [u8; 4] = *b"GF1\0";
pub const SNAPSHOT_VERSION: u32 = 1;

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct EngineSnapshot {
    pub magic: [u8; 4],
    pub version: u32,
    pub timestamp_us: u64,
    pub current_time_us: u64,
    pub last_fire_time_us: u64,
    pub v_membrane: f32,
    pub expansion_rate: f32,
    pub heading_rad: f32,
    pub target_heading_rad: f32,
    pub wedges: [f32; NUM_WEDGES],
    pub config: EngineConfig,
    pub checksum: u32,
}

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
