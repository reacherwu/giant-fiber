"""Shared synthetic stimuli for the Python tests (uses giantfiber.sim.events)."""

from giantfiber.sim.events import EventCamera


def run_loom(engine_step, feed, *, rv_ms=10.0, R=0.15, z0=6.0, offset_x=0.0, fov_deg=90.0,
             dt_us=1000, noise_hz=0.0, seed=0, max_s=3.0):
    """Drive a sphere of radius R approaching at constant speed v = R / (rv_ms * 1e-3).

    `feed(bytes)` receives raw event buffers, `engine_step(now_us)` returns a ReflexDecision.
    Returns (decisions, first_triggered_decision_or_None).
    """
    cam = EventCamera(noise_rate_hz=noise_hz, seed=seed, fov_deg=fov_deg)
    v = R / (rv_ms * 1e-3)
    out = []
    first = None
    for k in range(int(max_s * 1e6 / dt_us)):
        t = k * dt_us * 1e-6
        Z = z0 - v * t
        if Z < R * 1.05:
            break
        ev = cam.step((offset_x, 0.0, Z), R, k * dt_us + 1, dt_us)
        if len(ev):
            feed(ev.tobytes())
        d = engine_step(k * dt_us + dt_us)
        out.append(d)
        if d.triggered and first is None:
            first = d
    return out, first
