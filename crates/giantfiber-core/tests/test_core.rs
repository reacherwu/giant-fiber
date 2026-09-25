use giantfiber_core::types::*;
use giantfiber_core::accumulator::EventAccumulator;
use giantfiber_core::connectome::*;
use giantfiber_core::GiantFiberEngine;

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
fn test_giant_fiber_looming_evasion_activation() {
    let mut gf = GiantFiberCircuit::new(2.0, 50000);
    let flow = OpticFlowField {
        hs_flow: 0.1,
        vs_flow: 0.0,
        divergence: 0.8,
        horizontal_asymmetry: 0.7, // Looming on the right
        vertical_asymmetry: 0.0,
    };
    // Feed high physical expansion rate (150.0 rad/s)
    let out = gf.step(&flow, 150.0, 10000);
    assert!(out.gf_fired, "Giant fiber should fire on rapid looming");
    // Drosophila avoids away from threat: threat on right -> roll LEFT!
    assert_eq!(out.recommended_action, ReflexAction::RollLeft90);
    assert!(out.thrust_magnitude > 0.9);
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
fn test_full_engine_pipeline_and_latency() {
    let config = EngineConfig {
        confidence_threshold: 0.80,
        temperature: 0.9,
        looming_threshold: 2.0,
        decay_tau_us: 20000.0,
        refractory_period_us: 40000,
    };
    let mut engine = GiantFiberEngine::new(config);

    // Initial step with no stimulus -> Cruise
    let initial_out = engine.step_eval(1000);
    assert_eq!(initial_out.action, ReflexAction::Cruise as u32);
    assert_eq!(initial_out.triggered, 0);
    assert!(initial_out.compute_latency_us < 1500, "Compute latency must be under 1.5ms");

    // Simulate high-speed looming object (rapid expanding disk centered on left)
    for radius in 2..20 {
        let t = 2000 + (radius as u64) * 200;
        for angle_deg in (0..360).step_by(30) {
            let rad = (angle_deg as f32).to_radians();
            let x = (80.0 + (radius as f32) * 3.0 * rad.cos()) as u16;
            let y = (160.0 + (radius as f32) * 3.0 * rad.sin()) as u16;
            let spike = EventSpike {
                timestamp_us: t,
                x,
                y,
                polarity: 1,
                _pad: [0; 7],
            };
            engine.feed_event(&spike, 320, 320);
        }
    }

    // Step evaluation on looming pulse
    let reflex_out = engine.step_eval(6000);
    assert_eq!(reflex_out.triggered, 1, "Escape reflex should trigger");
    assert!(reflex_out.confidence >= 0.80, "Confidence should meet threshold: {}", reflex_out.confidence);
    // Since threat is on the left, roll right!
    assert_eq!(reflex_out.action, ReflexAction::RollRight90 as u32);
    assert!(reflex_out.compute_latency_us < 1000, "Compute latency {}us exceeds 1ms target", reflex_out.compute_latency_us);
}

#[test]
fn test_zero_loss_snapshot_persistence() {
    let mut engine = GiantFiberEngine::new(EngineConfig::default());
    // Give engine some state
    let spike = EventSpike {
        timestamp_us: 55000,
        x: 100,
        y: 100,
        polarity: 1,
        _pad: [0; 7],
    };
    engine.feed_event(&spike, 320, 320);
    engine.step_eval(55000);

    let mut buf = vec![0u8; 1024];
    let written = engine.save_snapshot(&mut buf).expect("Save snapshot failed");
    assert!(written > 0);

    // Verify snapshot magic
    assert_eq!(&buf[0..4], b"GF1\0");

    // Create fresh engine and restore
    let mut new_engine = GiantFiberEngine::new(EngineConfig::default());
    new_engine.restore_snapshot(&buf[..written]).expect("Restore snapshot failed");

    assert_eq!(new_engine.accumulator.current_time_us, engine.accumulator.current_time_us);
    assert_eq!(new_engine.central_complex.state.heading_rad, engine.central_complex.state.heading_rad);
}
