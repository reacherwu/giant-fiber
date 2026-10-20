import math
import numpy as np

from giantfiber.farfield import FarFieldTTC, FarFieldConfig, Derotator
from giantfiber.filters import BackgroundActivityFilter
from giantfiber.sim.events import imx636_camera, world_to_cam


def _approach(v=10.0, d0=20.0, noise=0.1, seed=1):
    cam = imx636_camera(noise_rate_hz=noise, seed=seed)
    ff = FarFieldTTC(FarFieldConfig())
    start = np.array([d0, 0.05, 0.0])
    vel = -start / np.linalg.norm(start) * v
    cam.prime(world_to_cam(start, np.zeros(3), (1, 0, 0, 0)), 0.15)
    for k in range(int(d0 / v * 500)):
        p = start + vel * k * 0.002
        ff.feed(cam.step(world_to_cam(p, np.zeros(3), (1, 0, 0, 0)), 0.15, k * 2000 + 1, 2000))
        o = ff.eval(k * 2000 + 2000)
        if o["armed"]:
            return (np.linalg.norm(p) - 0.4) / v, o
    return None, None


def test_farfield_arms_with_lead_time():
    ttc, o = _approach()
    assert ttc is not None and 0.6 < ttc < 1.1
    assert abs(o["tau_s"] - ttc) < 0.2


def test_baf_removes_isolated_noise():
    cam = imx636_camera(noise_rate_hz=1.0, seed=3)
    f = BackgroundActivityFilter(1280, 720, 2000)
    n_in = n_out = 0
    for k in range(200):
        ev = cam.step(None, 0.15, k * 2000 + 1, 2000)
        n_in += len(ev)
        n_out += len(f.apply(ev))
    assert n_out < 0.05 * n_in


def test_derotator_identity():
    cam = imx636_camera(seed=0)
    d = Derotator(cam.width, cam.height, cam.fov_deg)
    d.set_reference(np.eye(3))
    ev = cam.step((0.2, -0.1, 10.0), 0.15, 1, 2000)
    out = d.apply(ev, np.eye(3))
    assert len(out) == len(ev)
    assert np.all(out["x"] == ev["x"]) and np.all(out["y"] == ev["y"])
