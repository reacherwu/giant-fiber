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

Optional sensor effects (used for the IMX636 model, see `imx636_camera`):
5. Pixel dead time (`refractory_us`): an event within that time of the previous event at the same
   pixel is dropped.
6. Readout latency is modelled by the caller (see `sim/px4_sitl/run_campaign.py`, `DelayLine`).

Not modelled: background texture (the scene is textureless, so ego-motion produces no events),
partial-pixel coverage and contrast-threshold non-uniformity (the object is assumed to have far more
than the 25% log-contrast threshold, so a pixel fires once when its centre is covered or uncovered),
motion blur, lens distortion.
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
    refractory_us: int = 0

    def __post_init__(self):
        self.f = (self.width / 2) / math.tan(math.radians(self.fov_deg) / 2)
        self.cx = self.width / 2
        self.cy = self.height / 2
        yy, xx = np.mgrid[0:self.height, 0:self.width]
        self.xx = xx.astype(np.float32) + 0.5
        self.yy = yy.astype(np.float32) + 0.5
        self.prev = np.zeros((self.height, self.width), dtype=bool)
        self.prev_box = None
        self.rng = np.random.default_rng(self.seed)
        self.last_t = np.zeros((self.height, self.width), dtype=np.int64) - (1 << 40) if self.refractory_us else None

    def reset(self):
        self.prev[:] = False
        self.prev_box = None

    def prime(self, p_cam, radius):
        """Set the occupancy state without emitting events (object already in view before t=0)."""
        self.prev[:] = False
        self.prev_box = self._draw(self.prev, p_cam, radius)

    def _draw(self, mask, p_cam, radius):
        """Draw the disk into `mask`; return its pixel box (y0, y1, x0, x1) or None."""
        pr = self.project_sphere(p_cam, radius) if p_cam is not None else None
        if pr is None:
            return None
        u, v, r = pr
        if u + r < 0 or u - r > self.width or v + r < 0 or v - r > self.height:
            return None
        x0, x1 = max(0, int(u - r - 1)), min(self.width, int(u + r + 2))
        y0, y1 = max(0, int(v - r - 1)), min(self.height, int(v + r + 2))
        if x1 <= x0 or y1 <= y0:
            return None
        mask[y0:y1, x0:x1] = (self.xx[y0:y1, x0:x1] - u) ** 2 + (self.yy[y0:y1, x0:x1] - v) ** 2 <= r * r
        return (y0, y1, x0, x1)

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

    def _disk_box(self, p_cam, radius):
        pr = self.project_sphere(p_cam, radius) if p_cam is not None else None
        if pr is None:
            return None, None
        u, v, r = pr
        if u + r < 0 or u - r > self.width or v + r < 0 or v - r > self.height:
            return None, None
        x0, x1 = max(0, int(u - r - 1)), min(self.width, int(u + r + 2))
        y0, y1 = max(0, int(v - r - 1)), min(self.height, int(v + r + 2))
        if x1 <= x0 or y1 <= y0:
            return None, None
        return (y0, y1, x0, x1), (u, v, r)

    def step(self, p_cam, radius, t0_us: int, dt_us: int):
        """Advance one step; returns a numpy array with EVENT_DTYPE. Only the bounding box of the
        previous and the new disk is touched (no full-frame copies)."""
        box, pr = self._disk_box(p_cam, radius)
        boxes = [b for b in (box, self.prev_box) if b is not None]
        if boxes:
            y0 = min(b[0] for b in boxes); y1 = max(b[1] for b in boxes)
            x0 = min(b[2] for b in boxes); x1 = max(b[3] for b in boxes)
            old = self.prev[y0:y1, x0:x1]
            if pr is not None:
                u, v, r = pr
                newr = (self.xx[y0:y1, x0:x1] - u) ** 2 + (self.yy[y0:y1, x0:x1] - v) ** 2 <= r * r
            else:
                newr = np.zeros_like(old)
            ys, xs = np.nonzero(newr ^ old)
            pol = np.where(newr[ys, xs], -1, 1).astype(np.int8)
            ys = ys + y0; xs = xs + x0
            self.prev[y0:y1, x0:x1] = newr
        else:
            ys = xs = np.zeros(0, dtype=np.int64)
            pol = np.zeros(0, dtype=np.int8)
        self.prev_box = box
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
            ev["timestamp_us"] = t0_us + self.rng.integers(0, max(1, dt_us), len(ev))
            ev = ev[np.argsort(ev["timestamp_us"], kind="stable")]
            if self.last_t is not None:
                x = ev["x"].astype(np.int64); y = ev["y"].astype(np.int64); t = ev["timestamp_us"].astype(np.int64)
                # keep the first event per pixel in this step, then apply the dead time
                _, first = np.unique(y * self.width + x, return_index=True)
                keep = np.zeros(len(ev), dtype=bool)
                keep[first] = t[first] - self.last_t[y[first], x[first]] >= self.refractory_us
                ev = ev[keep]
                self.last_t[ev["y"].astype(np.int64), ev["x"].astype(np.int64)] = ev["timestamp_us"].astype(np.int64)
        return ev


# Prophesee / Sony IMX636 (EVK4 HD): 1280 x 720, 4.86 um pixels. Typical contrast threshold 25%,
# latency < 100 us at 1000 lx and < 1000 us at 5 lx, background rate <= 0.1 Hz/px at 1000 lx and
# <= 10 Hz/px at 5 lx (Sony IMX636-AAMR-C flyer). With an 8 mm lens the horizontal FOV is
# 2*atan(1280*4.86e-3/2/8) = 42.5 deg (24.6 deg vertical).
IMX636 = dict(width=1280, height=720, pixel_um=4.86, lens_mm=8.0)


def imx636_hfov_deg(lens_mm=8.0):
    return math.degrees(2 * math.atan(1280 * 4.86e-3 / 2 / lens_mm))


def imx636_camera(noise_rate_hz=0.1, refractory_us=1000, lens_mm=8.0, seed=0):
    return EventCamera(width=1280, height=720, fov_deg=imx636_hfov_deg(lens_mm), noise_rate_hz=noise_rate_hz,
                       seed=seed, refractory_us=refractory_us)


R_CB_FORWARD = np.array([[0.0, 0.0, 1.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]])  # columns: cam x, y, z in body FRD


def mount_rotation(yaw_deg=0.0, pitch_deg=0.0):
    """Camera->body rotation for a camera yawed right by `yaw_deg` and pitched up by `pitch_deg`."""
    y, p = math.radians(yaw_deg), math.radians(pitch_deg)
    Rz = np.array([[math.cos(y), -math.sin(y), 0.0], [math.sin(y), math.cos(y), 0.0], [0.0, 0.0, 1.0]])
    Ry = np.array([[math.cos(p), 0.0, math.sin(p)], [0.0, 1.0, 0.0], [-math.sin(p), 0.0, math.cos(p)]])
    return Rz @ Ry @ R_CB_FORWARD


def world_to_cam(p_obj_ned, p_drone_ned, q_body_to_ned, R_cb=None):
    """Object position in camera coordinates (X right, Y down, Z forward); forward camera unless
    `R_cb` (camera->body rotation, see `mount_rotation`) is given."""
    R = quat_to_rot(q_body_to_ned)
    pb = R.T @ (np.asarray(p_obj_ned) - np.asarray(p_drone_ned))  # body FRD
    if R_cb is None:
        return (pb[1], pb[2], pb[0])
    pc = R_cb.T @ pb
    return (pc[0], pc[1], pc[2])
