//! Native micro-benchmark of the engine (no FFI, no Python).
//!
//! `cargo run --release --example bench -p giantfiber-core [-- --quick]`
//! Prints one JSON object. Stimulus: a 0.15 m sphere approaching head-on at r/v = 10 ms on a
//! 320x320, 90 deg camera (occupancy-change events), 1 ms steps, repeated.

use giantfiber_core::types::{EngineConfig, EventSpike};
use giantfiber_core::GiantFiberEngine;
use std::time::Instant;

fn loom_events(rv_ms: f32) -> Vec<Vec<EventSpike>> {
    let r = 0.15f32;
    let v = r / (rv_ms * 1e-3);
    let f = 160.0 / (45f32.to_radians()).tan();
    let mut occ = vec![false; 320 * 320];
    let mut steps = Vec::new();
    let mut z = r / 1f32.to_radians().tan();
    let mut t: u64 = 0;
    while z > r * 1.2 {
        let rad = f * (r / z).asin().tan();
        let mut ev = Vec::new();
        for py in 0..320usize {
            for px in 0..320usize {
                let dx = px as f32 + 0.5 - 160.0;
                let dy = py as f32 + 0.5 - 160.0;
                let inside = dx * dx + dy * dy <= rad * rad;
                let i = py * 320 + px;
                if inside != occ[i] {
                    occ[i] = inside;
                    ev.push(EventSpike { timestamp_us: t + 1, x: px as u16, y: py as u16, polarity: -1, _pad: [0; 7] });
                }
            }
        }
        steps.push(ev);
        z -= v * 1e-3;
        t += 1000;
    }
    steps
}

fn pct(v: &mut [f64], p: f64) -> f64 {
    v.sort_by(|a, b| a.partial_cmp(b).unwrap());
    v[((v.len() as f64 - 1.0) * p).round() as usize]
}

fn main() {
    let quick = std::env::args().any(|a| a == "--quick");
    let reps = if quick { 5 } else { 100 };
    let steps = loom_events(10.0);
    let n_events: usize = steps.iter().map(|s| s.len()).sum();

    let mut step_ns = Vec::new();
    let mut feed_ns_total = 0f64;
    let mut events_total = 0usize;
    let mut triggers = 0usize;
    for _ in 0..reps {
        let mut e = GiantFiberEngine::new(EngineConfig::default());
        for (k, ev) in steps.iter().enumerate() {
            let t0 = Instant::now();
            e.feed_events_batch(ev, 320, 320);
            feed_ns_total += t0.elapsed().as_nanos() as f64;
            events_total += ev.len();
            let t1 = Instant::now();
            let out = e.step_eval((k as u64 + 1) * 1000);
            step_ns.push(t1.elapsed().as_nanos() as f64);
            triggers += out.triggered as usize;
        }
    }
    let mut e = GiantFiberEngine::new(EngineConfig::default());
    let mut buf = [0u8; 512];
    let n_snap = if quick { 1000 } else { 100_000 };
    let t0 = Instant::now();
    let mut len = 0;
    for _ in 0..n_snap {
        len = e.save_snapshot(&mut buf).unwrap();
    }
    let save_ns = t0.elapsed().as_nanos() as f64 / n_snap as f64;
    let t0 = Instant::now();
    for _ in 0..n_snap {
        e.restore_snapshot(&buf[..len]).unwrap();
    }
    let restore_ns = t0.elapsed().as_nanos() as f64 / n_snap as f64;

    let mean = step_ns.iter().sum::<f64>() / step_ns.len() as f64;
    let max = step_ns.iter().cloned().fold(0.0, f64::max);
    println!(
        "{{\"steps\":{},\"events_per_loom\":{},\"step_eval_ns\":{{\"mean\":{:.0},\"p50\":{:.0},\"p99\":{:.0},\"max\":{:.0}}},\"ingest_events_per_s\":{:.0},\"snapshot_bytes\":{},\"snapshot_save_ns\":{:.0},\"snapshot_restore_ns\":{:.0},\"engine_struct_bytes\":{},\"triggers_per_loom\":{:.2}}}",
        step_ns.len(), n_events, mean, pct(&mut step_ns, 0.5), pct(&mut step_ns, 0.99), max,
        events_total as f64 / (feed_ns_total * 1e-9), len, save_ns, restore_ns,
        std::mem::size_of::<GiantFiberEngine>(), triggers as f64 / reps as f64
    );
}
