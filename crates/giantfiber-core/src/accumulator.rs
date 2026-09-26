//! Physical O(K) Bounded Neuromorphic Event Accumulator (Time Surface).
//!
//! Maintains a constant-memory 2D decay surface and extracts spatial-temporal moments
//! (centroid, variance, and expansion rate) without dynamic heap allocations.

use crate::types::EventSpike;

pub const SURFACE_WIDTH: usize = 64;
pub const SURFACE_HEIGHT: usize = 64;

/// Bounded Constant-Memory Receptive Field Time Surface.
pub struct EventAccumulator {
    /// Last spike microsecond timestamp per cell
    last_timestamp: [[u64; SURFACE_WIDTH]; SURFACE_HEIGHT],
    /// Last polarity per cell (+1 or -1)
    polarity_surface: [[i8; SURFACE_WIDTH]; SURFACE_HEIGHT],
    /// Global latest timestamp
    pub current_time_us: u64,
    /// Time constant tau in microseconds for exponential decay
    pub tau_us: f32,
    /// Moving centroid tracking
    pub centroid_x: f32,
    pub centroid_y: f32,
    /// Moving spatial radius (spread) tracking
    pub radius: f32,
    /// Previous radius for expansion rate (dR/dt)
    pub prev_radius: f32,
    pub prev_radius_time_us: u64,
    /// Recent event count in current epoch
    pub recent_event_count: u32,
    /// Accumulated divergence / expansion rate (dR / dt)
    pub expansion_rate: f32,
}

impl EventAccumulator {
    pub fn new(tau_us: f32) -> Self {
        Self {
            last_timestamp: [[0; SURFACE_WIDTH]; SURFACE_HEIGHT],
            polarity_surface: [[0; SURFACE_WIDTH]; SURFACE_HEIGHT],
            current_time_us: 0,
            tau_us: if tau_us > 0.0 { tau_us } else { 25000.0 },
            centroid_x: (SURFACE_WIDTH as f32) / 2.0,
            centroid_y: (SURFACE_HEIGHT as f32) / 2.0,
            radius: 1.0,
            prev_radius: 1.0,
            prev_radius_time_us: 0,
            recent_event_count: 0,
            expansion_rate: 0.0,
        }
    }

    /// Reset all state to clean baseline.
    pub fn reset(&mut self) {
        self.last_timestamp = [[0; SURFACE_WIDTH]; SURFACE_HEIGHT];
        self.polarity_surface = [[0; SURFACE_WIDTH]; SURFACE_HEIGHT];
        self.current_time_us = 0;
        self.centroid_x = (SURFACE_WIDTH as f32) / 2.0;
        self.centroid_y = (SURFACE_HEIGHT as f32) / 2.0;
        self.radius = 1.0;
        self.prev_radius = 1.0;
        self.prev_radius_time_us = 0;
        self.recent_event_count = 0;
        self.expansion_rate = 0.0;
    }

    /// Feed a single event spike. Quantizes raw sensor coordinates (e.g. 320x320)
    /// into the bounded 64x64 surface.
    #[inline(always)]
    pub fn feed_spike(&mut self, spike: &EventSpike, raw_width: u16, raw_height: u16) {
        if spike.timestamp_us > self.current_time_us {
            self.current_time_us = spike.timestamp_us;
        }

        let sx = ((spike.x as usize * SURFACE_WIDTH) / (raw_width as usize).max(1))
            .min(SURFACE_WIDTH - 1);
        let sy = ((spike.y as usize * SURFACE_HEIGHT) / (raw_height as usize).max(1))
            .min(SURFACE_HEIGHT - 1);

        self.last_timestamp[sy][sx] = spike.timestamp_us;
        self.polarity_surface[sy][sx] = if spike.polarity == 0 { -1 } else { spike.polarity };
        self.recent_event_count = self.recent_event_count.saturating_add(1);

        // Exponential moving average for Centroid
        let alpha = 0.05f32;
        self.centroid_x = (1.0 - alpha) * self.centroid_x + alpha * (sx as f32);
        self.centroid_y = (1.0 - alpha) * self.centroid_y + alpha * (sy as f32);

        let dx = (sx as f32) - self.centroid_x;
        let dy = (sy as f32) - self.centroid_y;
        let dist = (dx * dx + dy * dy).sqrt();

        self.radius = (1.0 - alpha) * self.radius + alpha * dist.max(1.0);
    }

    /// Update expansion rate and temporal dynamics at evaluation step.
    pub fn update_dynamics(&mut self, now_us: u64) {
        if now_us > self.current_time_us {
            self.current_time_us = now_us;
        }

        if self.prev_radius_time_us == 0 {
            // First evaluation step: establish baseline
            self.prev_radius = self.radius;
            self.prev_radius_time_us = self.current_time_us;
            self.expansion_rate = 0.0;
            return;
        }

        let dt_us = self.current_time_us.saturating_sub(self.prev_radius_time_us);
        if dt_us >= 500 {
            // Evaluated over at least 0.5ms window
            let dr = self.radius - self.prev_radius;
            let dt_sec = (dt_us as f32) / 1_000_000.0;
            let vel = dr / dt_sec;
            let instant_expansion = vel / self.prev_radius.max(0.5);

            // Biological low-pass filter (tau ~ 4ms) to suppress single-frame random white noise
            let smooth_alpha = 0.35f32;
            self.expansion_rate = (1.0 - smooth_alpha) * self.expansion_rate + smooth_alpha * instant_expansion;

            self.prev_radius = self.radius;
            self.prev_radius_time_us = self.current_time_us;
        }
    }

    /// Read decaying surface value at (x, y) relative to current time.
    #[inline(always)]
    pub fn get_surface_value(&self, x: usize, y: usize) -> f32 {
        if x >= SURFACE_WIDTH || y >= SURFACE_HEIGHT {
            return 0.0;
        }
        let last_t = self.last_timestamp[y][x];
        if last_t == 0 {
            return 0.0;
        }
        let dt = self.current_time_us.saturating_sub(last_t) as f32;
        let decay = (-dt / self.tau_us).exp();
        decay * (self.polarity_surface[y][x] as f32)
    }

    /// Extract 4-quadrant activity to determine threat azimuth.
    /// Quadrants:
    /// Q0: Top-Left, Q1: Top-Right, Q2: Bottom-Left, Q3: Bottom-Right
    pub fn quadrant_activity(&self) -> [f32; 4] {
        let mut quad = [0.0f32; 4];
        let mid_x = SURFACE_WIDTH / 2;
        let mid_y = SURFACE_HEIGHT / 2;

        for y in 0..SURFACE_HEIGHT {
            for x in 0..SURFACE_WIDTH {
                let val = self.get_surface_value(x, y).abs();
                if val > 0.01 {
                    let q_idx = match (x >= mid_x, y >= mid_y) {
                        (false, false) => 0,
                        (true, false) => 1,
                        (false, true) => 2,
                        (true, true) => 3,
                    };
                    quad[q_idx] += val;
                }
            }
        }
        quad
    }
}
