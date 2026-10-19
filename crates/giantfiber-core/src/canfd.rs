//! 24-byte CAN-FD reflex frame (no_std, no allocation).
//!
//! Same wire layout as `giantfiber/integrations/can_fd.py` (cross-checked by the Python tests):
//!
//! | bytes  | field                                  |
//! |--------|----------------------------------------|
//! | 0..2   | magic `b"GF"`                          |
//! | 2..4   | sequence, u16 LE                       |
//! | 4      | action (ReflexAction)                  |
//! | 5      | confidence, u8 (0..255 -> 0..1)        |
//! | 6..8   | roll rate, i16 LE, 0.1 deg/s           |
//! | 8..10  | pitch rate, i16 LE, 0.1 deg/s          |
//! | 10..12 | yaw rate, i16 LE, 0.1 deg/s            |
//! | 12..14 | thrust, u16 LE, permille               |
//! | 14..16 | compute latency, u16 LE, us            |
//! | 16..20 | timestamp, u32 LE, ms                  |
//! | 20     | escape mode (0 none, 1 long, 2 short)  |
//! | 21     | reserved (0)                           |
//! | 22..24 | CRC-16-CCITT (0x1021, init 0xFFFF) over bytes 0..22, LE |
//!
//! 24 bytes is a valid CAN-FD DLC (DLC 12). This module only builds the payload; putting it on a
//! bus is the job of the MCU's CAN peripheral driver (or SocketCAN on Linux, see the Python side).

use crate::math::floorf;
use crate::types::{ReflexAction, ReflexOutput};

pub const FRAME_LEN: usize = 24;
pub const MAGIC: [u8; 2] = *b"GF";
/// Default 29-bit identifier used by the Python codec.
pub const DEFAULT_CAN_ID: u32 = 0x18F0_2001;

#[derive(Debug, Clone, Copy, PartialEq, Default)]
pub struct ReflexFrame {
    pub sequence: u16,
    pub action: u8,
    pub confidence: f32,
    pub roll_rate_dps: f32,
    pub pitch_rate_dps: f32,
    pub yaw_rate_dps: f32,
    pub thrust: f32,
    pub compute_latency_us: u32,
    pub timestamp_ms: u32,
    pub escape_mode: u8,
}

pub fn crc16_ccitt(data: &[u8]) -> u16 {
    let mut crc: u16 = 0xFFFF;
    for &b in data {
        crc ^= (b as u16) << 8;
        for _ in 0..8 {
            crc = if crc & 0x8000 != 0 { (crc << 1) ^ 0x1021 } else { crc << 1 };
        }
    }
    crc
}

/// Round half up, then saturate (same rule as the Python codec: floor(x + 0.5)).
fn round_sat(v: f32, lo: i32, hi: i32) -> i32 {
    if !v.is_finite() {
        return 0;
    }
    let r = floorf(v + 0.5);
    if r < lo as f32 {
        lo
    } else if r > hi as f32 {
        hi
    } else {
        r as i32
    }
}

fn sat_i16(v: f32) -> i16 {
    round_sat(v, i16::MIN as i32, i16::MAX as i32) as i16
}

impl ReflexFrame {
    /// Rate/thrust setpoints for a discrete action (same table as `CANFDCodec.from_decision`).
    pub fn from_output(out: &ReflexOutput, sequence: u16, timestamp_ms: u32) -> Self {
        let (roll, pitch, thrust) = match ReflexAction::from_u32(out.action) {
            ReflexAction::RollRight90 => (300.0, 0.0, 0.95),
            ReflexAction::RollLeft90 => (-300.0, 0.0, 0.95),
            ReflexAction::PitchUp => (0.0, 200.0, 0.90),
            ReflexAction::Brake => (0.0, -200.0, 1.0),
            _ => (0.0, 0.0, 0.7),
        };
        Self {
            sequence,
            action: out.action as u8,
            confidence: out.confidence,
            roll_rate_dps: roll,
            pitch_rate_dps: pitch,
            yaw_rate_dps: 0.0,
            thrust,
            compute_latency_us: out.compute_latency_us,
            timestamp_ms,
            escape_mode: out.escape_mode,
        }
    }

    pub fn encode(&self) -> [u8; FRAME_LEN] {
        let mut b = [0u8; FRAME_LEN];
        b[0..2].copy_from_slice(&MAGIC);
        b[2..4].copy_from_slice(&self.sequence.to_le_bytes());
        b[4] = self.action;
        b[5] = round_sat(self.confidence * 255.0, 0, 255) as u8;
        b[6..8].copy_from_slice(&sat_i16(self.roll_rate_dps * 10.0).to_le_bytes());
        b[8..10].copy_from_slice(&sat_i16(self.pitch_rate_dps * 10.0).to_le_bytes());
        b[10..12].copy_from_slice(&sat_i16(self.yaw_rate_dps * 10.0).to_le_bytes());
        b[12..14].copy_from_slice(&(round_sat(self.thrust * 1000.0, 0, 1000) as u16).to_le_bytes());
        b[14..16].copy_from_slice(&(self.compute_latency_us.min(65535) as u16).to_le_bytes());
        b[16..20].copy_from_slice(&self.timestamp_ms.to_le_bytes());
        b[20] = self.escape_mode;
        b[21] = 0;
        let crc = crc16_ccitt(&b[..22]);
        b[22..24].copy_from_slice(&crc.to_le_bytes());
        b
    }

    pub fn decode(b: &[u8]) -> Result<Self, &'static str> {
        if b.len() < FRAME_LEN {
            return Err("frame too short");
        }
        if b[0..2] != MAGIC {
            return Err("bad magic");
        }
        if crc16_ccitt(&b[..22]) != u16::from_le_bytes([b[22], b[23]]) {
            return Err("crc mismatch");
        }
        let i16_at = |i: usize| i16::from_le_bytes([b[i], b[i + 1]]) as f32 / 10.0;
        Ok(Self {
            sequence: u16::from_le_bytes([b[2], b[3]]),
            action: b[4],
            confidence: b[5] as f32 / 255.0,
            roll_rate_dps: i16_at(6),
            pitch_rate_dps: i16_at(8),
            yaw_rate_dps: i16_at(10),
            thrust: u16::from_le_bytes([b[12], b[13]]) as f32 / 1000.0,
            compute_latency_us: u16::from_le_bytes([b[14], b[15]]) as u32,
            timestamp_ms: u32::from_le_bytes([b[16], b[17], b[18], b[19]]),
            escape_mode: b[20],
        })
    }
}
