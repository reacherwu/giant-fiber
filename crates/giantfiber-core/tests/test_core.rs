use giantfiber_core::accumulator::EventAccumulator;
use giantfiber_core::connectome::data::*;
use giantfiber_core::connectome::giant_fiber::{gf_weights, nongf_weights};
use giantfiber_core::connectome::*;
use giantfiber_core::types::*;
use giantfiber_core::GiantFiberEngine;

// ---------------------------------------------------------------------------
// Synthetic stimulus: a disk (sphere of radius r) approaching head-on a 320x320 camera.
// Events are emitted for pixels whose occupancy changed between steps.
// ---------------------------------------------------------------------------
struct DiskCam {
    occ: Vec<bool>,
    f: f32,
    seed: u64,
}

impl DiskCam {
    fn new(fov_deg: f32) -> Self {
        Self { occ: vec![false; 320 * 320], f: 160.0 / (fov_deg.to_radians() * 0.5).tan(), seed: 12345 }
    }
    fn rand(&mut self) -> u64 {
        self.seed = self.seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        self.seed >> 33
    }
    /// Object at lateral offset x (m) and depth z (m); returns events for this step.
    fn step(&mut self, x: f32, z: f32, r: f32, t_us: u64, noise_events: usize) -> Vec<EventSpike> {
        let mut out = Vec::new();
        let (u, rad) = if z > r {
            let alpha = (r / (x * x + z * z).sqrt()).asin();
            (160.0 + self.f * x / z, self.f * alpha.tan())
        } else {
            (160.0, 0.0)
        };
        for py in 0..320usize {
            for px in 0..320usize {
                let dx = px as f32 + 0.5 - u;
                let dy = py as f32 + 0.5 - 160.0;
                let inside = rad > 0.0 && dx * dx + dy * dy <= rad * rad;
                let i = py * 320 + px;
                if inside != self.occ[i] {
                    self.occ[i] = inside;
                    out.push(EventSpike { timestamp_us: t_us, x: px as u16, y: py as u16, polarity: if inside { -1 } else { 1 }, _pad: [0; 7] });
                }
            }
        }
        for _ in 0..noise_events {
            let px = (self.rand() % 320) as u16;
            let py = (self.rand() % 320) as u16;
            out.push(EventSpike { timestamp_us: t_us, x: px, y: py, polarity: 1, _pad: [0; 7] });
        }
        out
    }
}

/// Head-on approach with constant speed; returns (time-before-contact of GF spike in ms, mode of first episode).
fn loom(rv_ms: f32) -> (Option<f32>, u8) {
    let r = 0.15f32;
    let v = r / (rv_ms * 1e-3);
    let z0 = r / 1.0f32.to_radians().tan();
    let mut eng = GiantFiberEngine::new(EngineConfig::default());
    let mut cam = DiskCam::new(eng.config.fov_deg);
    let dt = 1000u64;
    let mut gf_t = None;
    let mut mode = 0u8;
    let mut k = 0u64;
    loop {
        let t = k as f32 * dt as f32 * 1e-6;
        let z = z0 - v * t;
        if z < 1.05 * r {
            break;
        }
        let ev = cam.step(0.02, z, r, k * dt + 1, 0);
        eng.feed_events_batch(&ev, 320, 320);
        let out = eng.step_eval(k * dt + dt);
        if out.triggered == 1 && mode == 0 {
            mode = out.escape_mode;
        }
        if out.triggered == 1 && out.escape_mode == 2 && mode == 1 {
            mode = 3; // long -> short upgrade
        }
        if gf_t.is_none() && eng.giant_fiber.last_fire_time_us > 0 {
            gf_t = Some((z - r) / v * 1e3);
        }
        k += 1;
    }
    (gf_t, mode)
}

#[test]
fn test_connectome_tables_are_consistent() {
    // Per-neuron edges must add up to the per-side totals generated from the same source tables
    let sum = |edges: &[Edge], side: Side| edges.iter().filter(|e| e.side == side).map(|e| e.syn_count).sum::<u32>();
    assert_eq!(sum(&LPLC2_TO_GF, Side::Left), LPLC2_LEFT_TO_GF_SYN);
    assert_eq!(sum(&LPLC2_TO_GF, Side::Right), LPLC2_RIGHT_TO_GF_SYN);
    assert_eq!(sum(&LC4_TO_GF, Side::Left), LC4_LEFT_TO_GF_SYN);
    assert_eq!(sum(&LC4_TO_GF, Side::Right), LC4_RIGHT_TO_GF_SYN);
    for e in LPLC2_TO_GF.iter().chain(LC4_TO_GF.iter()) {
        let gf = if e.side == Side::Left { GF_LEFT_ROOT_ID } else { GF_RIGHT_ROOT_ID };
        assert_eq!(e.post_root_id, gf);
        assert!(e.syn_count > 0);
    }
    // FlyWire root IDs are unique per presynaptic neuron within a cell type
    let mut ids: Vec<u64> = LPLC2_TO_GF.iter().map(|e| e.pre_root_id).collect();
    ids.sort();
    ids.dedup();
    assert_eq!(ids.len(), LPLC2_TO_GF.len());
    // Inputs are a fraction of the total GF input
    const _: () = assert!(LPLC2_LEFT_TO_GF_SYN + LC4_LEFT_TO_GF_SYN < GF_LEFT_TOTAL_INPUT_SYN);
    const _: () = assert!(LPLC2_RIGHT_TO_GF_SYN + LC4_RIGHT_TO_GF_SYN < GF_RIGHT_TOTAL_INPUT_SYN);
}

#[test]
fn test_weights_derive_from_synapse_counts() {
    let w = gf_weights();
    for s in 0..2 {
        assert!((w.lplc2[s] + w.lc4[s] - 1.0).abs() < 1e-6);
    }
    let expected = LPLC2_LEFT_TO_GF_SYN as f32 / (LPLC2_LEFT_TO_GF_SYN + LC4_LEFT_TO_GF_SYN) as f32;
    assert!((w.lplc2[0] - expected).abs() < 1e-6);
    let n = nongf_weights();
    assert!((n.lplc2[1] + n.lc4[1] - 1.0).abs() < 1e-6);
}

#[test]
fn test_looming_estimate_tracks_angular_size() {
    let mut eng = GiantFiberEngine::new(EngineConfig::default());
    let mut cam = DiskCam::new(90.0);
    let r = 0.15;
    // object at 1.0 m approaching at 2 m/s, sample after 100 ms (z = 0.8 m)
    for k in 0..100u64 {
        let z = 1.0 - 2.0 * k as f32 * 1e-3;
        let ev = cam.step(0.0, z, r, k * 1000 + 1, 0);
        eng.feed_events_batch(&ev, 320, 320);
        eng.step_eval(k * 1000 + 1000);
    }
    let est = eng.looming.estimate;
    let true_theta = 2.0 * (r / 0.802f32).asin().to_degrees();
    assert!(est.valid);
    assert!((est.theta_deg - true_theta).abs() < 0.25 * true_theta, "theta {} vs true {}", est.theta_deg, true_theta);
    assert!(est.theta_dot_dps > 0.0);
}

#[test]
fn test_gf_spike_angular_size_and_mode_selection() {
    // Fast looms: GF spikes and the escape becomes short-mode; slow looms: non-GF long mode.
    let (fast_t, fast_mode) = loom(10.0);
    let (slow_t, slow_mode) = loom(80.0);
    assert!(fast_t.is_some() && slow_t.is_some());
    assert!(fast_mode == 2 || fast_mode == 3, "fast loom mode {}", fast_mode);
    assert_eq!(slow_mode, 1, "slow loom should stay long-mode");
    // A fixed angular-size threshold predicts spike time-before-contact roughly proportional to r/v
    assert!(slow_t.unwrap() > 3.0 * fast_t.unwrap());
}

#[test]
fn test_no_escape_for_static_or_receding_object() {
    let mut eng = GiantFiberEngine::new(EngineConfig::default());
    let mut cam = DiskCam::new(90.0);
    let mut triggers = 0;
    for k in 0..800u64 {
        let t = k as f32 * 1e-3;
        // static for 100 ms, then recede at 2 m/s
        let z = 0.6 + 2.0 * (t - 0.1).max(0.0);
        let ev = cam.step(0.02, z, 0.15, k * 1000 + 1, 0);
        eng.feed_events_batch(&ev, 320, 320);
        triggers += eng.step_eval(k * 1000 + 1000).triggered as u32;
    }
    assert_eq!(triggers, 0);
}

#[test]
fn test_no_escape_for_sensor_noise() {
    let mut eng = GiantFiberEngine::new(EngineConfig::default());
    let mut cam = DiskCam::new(90.0);
    let mut triggers = 0;
    // ~0.3 events per pixel per second on 320x320 => ~31 events per 1 ms step
    for k in 0..2000u64 {
        let ev = cam.step(0.0, -1.0, 0.15, k * 1000 + 1, 31);
        eng.feed_events_batch(&ev, 320, 320);
        triggers += eng.step_eval(k * 1000 + 1000).triggered as u32;
    }
    assert_eq!(triggers, 0);
}

#[test]
fn test_escape_direction_away_from_stimulus() {
    let mut eng = GiantFiberEngine::new(EngineConfig::default());
    let mut cam = DiskCam::new(90.0);
    let r = 0.15;
    let v = r / 0.01;
    let mut action = None;
    for k in 0..2000u64 {
        let z = 8.0 - v * k as f32 * 1e-3;
        if z < 0.2 { break; }
        // object to the right of the optical axis (x = +0.4 * z keeps a constant bearing)
        let ev = cam.step(0.4 * z, z, r, k * 1000 + 1, 0);
        eng.feed_events_batch(&ev, 320, 320);
        let out = eng.step_eval(k * 1000 + 1000);
        if out.triggered == 1 { action = Some(out.action); break; }
    }
    assert_eq!(action, Some(ReflexAction::RollLeft90 as u32));
}

#[test]
fn test_snapshot_roundtrip() {
    let mut engine = GiantFiberEngine::new(EngineConfig::default());
    let mut cam = DiskCam::new(90.0);
    for k in 0..300u64 {
        let z = 3.0 - 8.0 * k as f32 * 1e-3;
        let ev = cam.step(0.02, z, 0.15, k * 1000 + 1, 0);
        engine.feed_events_batch(&ev, 320, 320);
        engine.step_eval(k * 1000 + 1000);
    }
    let mut buf = vec![0u8; 1024];
    let written = engine.save_snapshot(&mut buf).expect("save");
    assert_eq!(&buf[0..4], b"GF1\0");
    let mut restored = GiantFiberEngine::new(EngineConfig::default());
    restored.restore_snapshot(&buf[..written]).expect("restore");
    assert_eq!(restored.giant_fiber.v_gf, engine.giant_fiber.v_gf);
    assert_eq!(restored.giant_fiber.v_nongf, engine.giant_fiber.v_nongf);
    assert_eq!(restored.giant_fiber.mode, engine.giant_fiber.mode);
    assert_eq!(restored.giant_fiber.episode_start_us, engine.giant_fiber.episode_start_us);
    assert_eq!(restored.accumulator.current_time_us, engine.accumulator.current_time_us);
    assert_eq!(restored.config, engine.config);
    // Corrupting one byte must fail the checksum
    buf[20] ^= 0xFF;
    assert!(restored.restore_snapshot(&buf[..written]).is_err());
}

#[test]
fn test_rem_euclid_shim() {
    use giantfiber_core::math::rem_euclidf;
    assert!((rem_euclidf(-1.0, 3.0) - 2.0).abs() < 1e-6);
    assert!((rem_euclidf(7.5, 2.0) - 1.5).abs() < 1e-6);
}

#[test]
fn test_event_accumulator_bounded_updates() {
    let mut acc = EventAccumulator::new(25000.0);
    // Inject spikes in top-right corner
    for i in 0..100 {
        let spike = EventSpike {
            timestamp_us: 1000 + i * 10,
            x: 280,
            y: 40,
            polarity: 1,
            _pad: [0; 7],
        };
        acc.feed_spike(&spike, 320, 320);
    }
    acc.update_dynamics(2000);
    let quads = acc.quadrant_activity();
    // Q1 is top-right, should have the highest activity
    assert!(quads[1] > quads[0], "Top-right quadrant should dominate");
    assert!(quads[1] > quads[2]);
    assert!(quads[1] > quads[3]);
}

#[test]
fn test_central_complex_heading_compass() {
    let mut cx = CentralComplexCircuit::new();
    assert!(cx.state.heading_rad.abs() < 1e-4);

    // Turn 90 degrees right (PI/2 rad/s for 1s)
    let state = cx.update(core::f32::consts::FRAC_PI_2, 1.0);
    assert!((state.heading_rad - core::f32::consts::FRAC_PI_2).abs() < 0.2);
    // Correction torque should attempt to restore heading to 0.0
    assert!(state.correction_torque.z < 0.0, "Correction torque should oppose heading deviation");
}

#[test]
fn test_normalize_angle_inf_nan_guards() {
    use giantfiber_core::connectome::central_complex::normalize_angle;
    // Must return 0.0 for non-finite values without hanging or panicking
    assert_eq!(normalize_angle(f32::INFINITY), 0.0);
    assert_eq!(normalize_angle(f32::NEG_INFINITY), 0.0);
    assert_eq!(normalize_angle(f32::NAN), 0.0);

    // Large multiples of 2*PI must wrap in O(1)
    let wrapped = normalize_angle(1000000.0 * core::f32::consts::PI);
    assert!(wrapped.abs() <= core::f32::consts::PI + 1e-4);
}

#[test]
fn test_compass_reset_clears_torque() {
    let mut cx = CentralComplexCircuit::new();
    cx.update(1.5, 0.5);
    assert!(cx.state.correction_torque.z != 0.0);

    cx.reset();
    assert_eq!(cx.state.correction_torque.x, 0.0);
    assert_eq!(cx.state.correction_torque.y, 0.0);
    assert_eq!(cx.state.correction_torque.z, 0.0);
    assert!(cx.state.heading_rad.abs() < 1e-6);
}

#[test]
fn test_accumulator_off_polarity_decay() {
    let mut acc = EventAccumulator::new(25000.0);
    // Feed OFF spike (polarity = 0)
    let spike_off = EventSpike {
        timestamp_us: 1000,
        x: 160,
        y: 160,
        polarity: 0,
        _pad: [0; 7],
    };
    acc.feed_spike(&spike_off, 320, 320);

    // Surface value at center should be negative (-1.0 decayed), NOT 0.0
    let sx = (160 * 64) / 320;
    let sy = (160 * 64) / 320;
    let val = acc.get_surface_value(sx, sy);
    assert!(val < -0.9, "OFF event should produce negative decaying surface, got {}", val);
}

#[test]
fn test_canfd_frame_roundtrip_and_crc() {
    use giantfiber_core::canfd::{crc16_ccitt, ReflexFrame, FRAME_LEN};
    // CRC-16/CCITT-FALSE check value for "123456789"
    assert_eq!(crc16_ccitt(b"123456789"), 0x29B1);
    let f = ReflexFrame {
        sequence: 42,
        action: 3,
        confidence: 0.965,
        roll_rate_dps: 300.0,
        pitch_rate_dps: -15.0,
        yaw_rate_dps: 0.0,
        thrust: 0.95,
        compute_latency_us: 12,
        timestamp_ms: 1_234_567,
        escape_mode: 2,
    };
    let b = f.encode();
    assert_eq!(b.len(), FRAME_LEN);
    assert_eq!(&b[0..2], b"GF");
    let d = ReflexFrame::decode(&b).unwrap();
    assert_eq!(d.sequence, 42);
    assert_eq!(d.action, 3);
    assert_eq!(d.escape_mode, 2);
    assert!((d.roll_rate_dps - 300.0).abs() < 1e-3);
    assert!((d.pitch_rate_dps + 15.0).abs() < 1e-3);
    assert!((d.thrust - 0.95).abs() < 1e-3);
    let mut bad = b;
    bad[6] ^= 0xFF;
    assert!(ReflexFrame::decode(&bad).is_err());
}
