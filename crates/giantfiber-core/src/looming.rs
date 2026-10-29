//! Looming stimulus estimation from the event time surface.
//!
//! The LPLC2 and LC4 visual projection neurons respond to the angular size and the angular
//! velocity of a looming object (Ache et al. 2019, Curr. Biol.; von Reyn et al. 2017, eLife).
//! This module does not model their dendrites; it estimates the two stimulus variables they
//! encode -- angular size theta and its rate dtheta/dt -- from the edge of the object, i.e. from
//! the time-surface cells that received an event within the last `EDGE_WINDOW_US`.

use crate::accumulator::{EventAccumulator, SLOW_WINDOW_US, SURFACE_HEIGHT, SURFACE_WIDTH};
use crate::math::{acosf, sqrtf, tanf};

/// One bit per surface cell in a row.
#[cfg(not(feature = "grid128"))]
type Row = u64;
#[cfg(feature = "grid128")]
type Row = u128;
const _: () = assert!(SURFACE_WIDTH == Row::BITS as usize, "row bitmap width must equal the surface width");

/// Cells whose last event is younger than this belong to the current object edge.
pub const EDGE_WINDOW_US: u64 = 4000;
/// A cell counts as edge only if it received at least this many events in its current burst
/// (a moving edge flips several pixels of a 5x5 cell; isolated noise events rarely do).
pub const MIN_CELL_EVENTS: u8 = 2;
/// Slow-edge channel: a cell is an edge cell if it received at least this many events in its
/// current `SLOW_WINDOW_US` window (used only when the fast channel finds too few cells).
pub const SLOW_MIN_EVENTS: u8 = 3;
/// Minimum time between two angular-size samples used for dtheta/dt.
pub const MIN_DERIV_DT_US: u64 = 1000;

#[derive(Debug, Clone, Copy, PartialEq)]
pub struct LoomingEstimate {
    /// True if enough coherent edge cells were found this step
    pub valid: bool,
    /// Angular diameter of the stimulus in degrees
    pub theta_deg: f32,
    /// Smoothed angular expansion rate in degrees per second
    pub theta_dot_dps: f32,
    /// Stimulus centroid on the time surface (cells)
    pub centroid_x: f32,
    pub centroid_y: f32,
    /// Fraction of edge cells in the left half of the image (left eye)
    pub left_fraction: f32,
    /// Number of edge cells used
    pub edge_cells: u32,
    /// Coefficient of variation of edge-cell distance to the centroid (ring = small, diffuse noise = large)
    pub radial_cv: f32,
    /// Smoothed image-plane centroid speed divided by the radial edge speed (looming on a collision
    /// course ~ 0, translating objects >> 1)
    pub translation_ratio: f32,
}

impl Default for LoomingEstimate {
    fn default() -> Self {
        Self {
            valid: false,
            theta_deg: 0.0,
            theta_dot_dps: 0.0,
            centroid_x: (SURFACE_WIDTH as f32) / 2.0,
            centroid_y: (SURFACE_HEIGHT as f32) / 2.0,
            left_fraction: 0.5,
            edge_cells: 0,
            radial_cv: 1.0,
            translation_ratio: 0.0,
        }
    }
}

pub struct LoomingEstimator {
    pub estimate: LoomingEstimate,
    last_theta_deg: f32,
    last_theta_time_us: u64,
    last_cx: f32,
    last_cy: f32,
    last_r: f32,
    c_vx: f32,
    c_vy: f32,
    r_speed: f32,
}

impl LoomingEstimator {
    pub fn new() -> Self {
        Self {
            estimate: LoomingEstimate::default(),
            last_theta_deg: 0.0,
            last_theta_time_us: 0,
            last_cx: 0.0,
            last_cy: 0.0,
            last_r: 0.0,
            c_vx: 0.0,
            c_vy: 0.0,
            r_speed: 0.0,
        }
    }

    pub fn reset(&mut self) {
        *self = Self::new();
    }

    /// Focal length in surface cells for a pinhole camera with horizontal field of view `fov_deg`.
    #[inline]
    fn focal_cells(fov_deg: f32) -> f32 {
        let half = (fov_deg.clamp(10.0, 170.0) * 0.5).to_radians();
        (SURFACE_WIDTH as f32 * 0.5) / tanf(half)
    }

    /// Angle in degrees between the camera rays through two surface points.
    fn ray_angle_deg(f: f32, ax: f32, ay: f32, bx: f32, by: f32) -> f32 {
        let (cx, cy) = (SURFACE_WIDTH as f32 * 0.5, SURFACE_HEIGHT as f32 * 0.5);
        let (ux, uy, uz) = (ax - cx, ay - cy, f);
        let (vx, vy, vz) = (bx - cx, by - cy, f);
        let dot = ux * vx + uy * vy + uz * vz;
        let n = sqrtf(ux * ux + uy * uy + uz * uz) * sqrtf(vx * vx + vy * vy + vz * vz);
        acosf((dot / n).clamp(-1.0, 1.0)).to_degrees()
    }

    /// Active-cell bitmap from a per-cell predicate, keeping only cells with an active 8-neighbour
    /// (object edges are contiguous, isolated sensor-noise events mostly are not; engineering
    /// filter, not biology).
    fn edge_cells(acc: &EventAccumulator, active: impl Fn(u64, usize, usize) -> bool) -> [Row; SURFACE_HEIGHT] {
        let mut rows: [Row; SURFACE_HEIGHT] = [0; SURFACE_HEIGHT];
        for (y, row) in rows.iter_mut().enumerate() {
            for x in 0..SURFACE_WIDTH {
                let t = acc.last_timestamp[y][x];
                if t != 0 && active(t, y, x) {
                    *row |= (1 as Row) << x;
                }
            }
        }
        let mut kept: [Row; SURFACE_HEIGHT] = [0; SURFACE_HEIGHT];
        for y in 0..SURFACE_HEIGHT {
            let r = rows[y];
            let up = if y > 0 { rows[y - 1] } else { 0 };
            let dn = if y + 1 < SURFACE_HEIGHT { rows[y + 1] } else { 0 };
            let vert = up | dn;
            let neigh = (r << 1) | (r >> 1) | vert | (vert << 1) | (vert >> 1);
            kept[y] = r & neigh;
        }
        kept
    }

    pub fn update(&mut self, acc: &EventAccumulator, now_us: u64, fov_deg: f32, min_edge_cells: u32) -> LoomingEstimate {
        // Fast channel: recent bursts (near / fast-expanding objects)
        let horizon = now_us.saturating_sub(EDGE_WINDOW_US);
        let mut kept = Self::edge_cells(acc, |t, y, x| {
            t >= horizon && acc.burst_count[y][x] >= MIN_CELL_EVENTS
        });
        let fast_n: u32 = kept.iter().map(|r| r.count_ones()).sum();
        if fast_n < min_edge_cells.max(1) {
            // Slow channel: events integrated over SLOW_WINDOW_US (far / slowly expanding objects)
            let slow_horizon = now_us.saturating_sub(SLOW_WINDOW_US);
            kept = Self::edge_cells(acc, |t, y, x| {
                t >= slow_horizon && acc.win_start[y][x] >= slow_horizon && acc.win_count[y][x] >= SLOW_MIN_EVENTS
            });
        }
        // Pass 1: centroid of kept edge cells
        let (mut n, mut sx, mut sy, mut nl) = (0u32, 0.0f32, 0.0f32, 0u32);
        for (y, &row) in kept.iter().enumerate() {
            let mut bits = row;
            while bits != 0 {
                let x = bits.trailing_zeros() as usize;
                bits &= bits - 1;
                n += 1;
                sx += x as f32 + 0.5;
                sy += y as f32 + 0.5;
                if x < SURFACE_WIDTH / 2 { nl += 1; }
            }
        }
        let mut est = self.estimate;
        est.edge_cells = n;
        if n < min_edge_cells.max(1) {
            // No usable stimulus: let the expansion estimate decay towards zero
            est.valid = false;
            est.theta_dot_dps *= 0.5;
            self.estimate = est;
            return est;
        }
        let (cx, cy) = (sx / n as f32, sy / n as f32);
        // Pass 2: mean and spread of radial distance
        let (mut sd, mut sd2) = (0.0f32, 0.0f32);
        for (y, &row) in kept.iter().enumerate() {
            let mut bits = row;
            while bits != 0 {
                let x = bits.trailing_zeros() as usize;
                bits &= bits - 1;
                let dx = x as f32 + 0.5 - cx;
                let dy = y as f32 + 0.5 - cy;
                let d = sqrtf(dx * dx + dy * dy);
                sd += d;
                sd2 += d * d;
            }
        }
        let mean_d = sd / n as f32;
        let var = (sd2 / n as f32 - mean_d * mean_d).max(0.0);
        // A filled blob of one cell still has a physical extent of ~0.5 cell
        let r = mean_d.max(0.5);
        let f = Self::focal_cells(fov_deg);
        let theta = 2.0 * Self::ray_angle_deg(f, cx, cy, cx + r, cy);

        est.valid = true;
        est.theta_deg = theta;
        est.centroid_x = cx;
        est.centroid_y = cy;
        est.left_fraction = nl as f32 / n as f32;
        est.radial_cv = if mean_d > 1e-3 { sqrtf(var) / mean_d } else { 0.0 };

        if self.last_theta_time_us == 0 {
            self.last_theta_deg = theta;
            self.last_theta_time_us = now_us;
            self.last_cx = cx;
            self.last_cy = cy;
            self.last_r = r;
        } else if now_us >= self.last_theta_time_us + MIN_DERIV_DT_US {
            let dt = (now_us - self.last_theta_time_us) as f32 * 1e-6;
            let inst = (theta - self.last_theta_deg) / dt;
            // first-order low-pass on the derivatives
            est.theta_dot_dps = 0.6 * est.theta_dot_dps + 0.4 * inst;
            // Signed centroid velocity (quantisation jitter averages out), radial edge speed
            self.c_vx = 0.9 * self.c_vx + 0.1 * (cx - self.last_cx) / dt;
            self.c_vy = 0.9 * self.c_vy + 0.1 * (cy - self.last_cy) / dt;
            self.r_speed = 0.9 * self.r_speed + 0.1 * (r - self.last_r) / dt;
            self.last_theta_deg = theta;
            self.last_theta_time_us = now_us;
            self.last_cx = cx;
            self.last_cy = cy;
            self.last_r = r;
        }
        est.translation_ratio = sqrtf(self.c_vx * self.c_vx + self.c_vy * self.c_vy) / self.r_speed.max(1e-3);
        self.estimate = est;
        est
    }
}

impl Default for LoomingEstimator {
    fn default() -> Self { Self::new() }
}
