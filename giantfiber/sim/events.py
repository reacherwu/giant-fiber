"""Synthetic event-camera model used by the validation script and the PX4 SITL scenario.

There is no event-camera simulator in this setup. Events are synthesized from geometry:

1. The incoming object is a sphere of radius R. Each step, its position is transformed into the
   camera frame (camera rigidly fixed to the body, looking along body +X, image x = body +Y,
   image y = body +Z) using the vehicle's ground-truth pose.
2. The sphere is drawn as a filled disk (pinhole projection of the centre, angular radius asin(R/d))
   into a W x H binary occupancy image.
3. Every pixel whose occupancy changed since the previous step emits one event
   (newly covered -> polarity -1 / OFF for a dark object, uncovered -> +1 / ON).
   Timestamps are spread uniformly inside the step.
4. Optional background noise: independent Poisson events at `noise_rate_hz` per pixel.

Not modelled: background texture (the scene is textureless, so ego-motion produces no events),
contrast thresholds, pixel refractory period, readout latency, motion blur, lens distortion.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

# Must match `EventSpike` in crates/giantfiber-core/src/types.rs (24 bytes incl. padding, repr(C))
EVENT_DTYPE = np.dtype({
    "names": ["timestamp_us", "x", "y", "polarity"],
    "formats": ["<u8", "<u2", "<u2", "i1"],
    "offsets": [0, 8, 10, 12],
    "itemsize": 24,
})


def quat_to_rot(q):
    """Body->world rotation matrix from quaternion (w, x, y, z)."""
    w, x, y, z = q
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


@dataclass
class EventCamera:
    width: int = 320
    height: int = 320
    fov_deg: float = 90.0
    noise_rate_hz: float = 0.0  # per pixel
    seed: int = 0

    def __post_init__(self):
        self.f = (self.width / 2) / math.tan(math.radians(self.fov_deg) / 2)
        self.cx = self.width / 2
        self.cy = self.height / 2
        yy, xx = np.mgrid[0:self.height, 0:self.width]
        self.xx = xx.astype(np.float32) + 0.5
        self.yy = yy.astype(np.float32) + 0.5
        self.prev = np.zeros((self.height, self.width), dtype=bool)
        self.rng = np.random.default_rng(self.seed)

    def reset(self):
        self.prev[:] = False

    def project_sphere(self, p_cam, radius):
        """p_cam = (X right, Y down, Z forward). Returns (u, v, r_px) or None if behind / invalid."""
        X, Y, Z = p_cam
        d = math.sqrt(X * X + Y * Y + Z * Z)
        if Z <= 0.05 or d <= radius:
            return None
        u = self.cx + self.f * X / Z
        v = self.cy + self.f * Y / Z
        alpha = math.asin(min(1.0, radius / d))
        r_px = self.f * math.tan(alpha) * d / Z  # approximate off-axis scaling
        return u, v, r_px

    def render(self, p_cam, radius):
        mask = np.zeros((self.height, self.width), dtype=bool)
        pr = self.project_sphere(p_cam, radius)
        if pr is None:
            return mask, None
        u, v, r = pr
        if u + r < 0 or u - r > self.width or v + r < 0 or v - r > self.height:
            return mask, pr
        x0, x1 = max(0, int(u - r - 1)), min(self.width, int(u + r + 2))
        y0, y1 = max(0, int(v - r - 1)), min(self.height, int(v + r + 2))
        sub = (self.xx[y0:y1, x0:x1] - u) ** 2 + (self.yy[y0:y1, x0:x1] - v) ** 2 <= r * r
        mask[y0:y1, x0:x1] = sub
        return mask, pr

    def step(self, p_cam, radius, t0_us: int, dt_us: int):
        """Advance one step; returns a numpy array with EVENT_DTYPE."""
        mask, _ = (self.render(p_cam, radius) if p_cam is not None
                   else (np.zeros_like(self.prev), None))
        changed = mask ^ self.prev
        ys, xs = np.nonzero(changed)
        pol = np.where(mask[ys, xs], -1, 1).astype(np.int8)
        self.prev = mask
        n_obj = len(xs)
        n_noise = 0
        if self.noise_rate_hz > 0:
            lam = self.noise_rate_hz * self.width * self.height * dt_us * 1e-6
            n_noise = int(self.rng.poisson(lam))
        ev = np.zeros(n_obj + n_noise, dtype=EVENT_DTYPE)
        if n_obj:
            ev["x"][:n_obj] = xs
            ev["y"][:n_obj] = ys
            ev["polarity"][:n_obj] = pol
        if n_noise:
            ev["x"][n_obj:] = self.rng.integers(0, self.width, n_noise)
            ev["y"][n_obj:] = self.rng.integers(0, self.height, n_noise)
            ev["polarity"][n_obj:] = self.rng.choice(np.array([-1, 1], dtype=np.int8), n_noise)
        if len(ev):
            ev["timestamp_us"] = t0_us + np.sort(self.rng.integers(0, max(1, dt_us), len(ev)))
        return ev


def world_to_cam(p_obj_ned, p_drone_ned, q_body_to_ned):
    """Object position in camera coordinates (X right, Y down, Z forward) for a forward camera."""
    R = quat_to_rot(q_body_to_ned)
    pb = R.T @ (np.asarray(p_obj_ned) - np.asarray(p_drone_ned))  # body FRD
    return (pb[1], pb[2], pb[0])
