"""Validated safety envelope and default configuration (PX4 SITL campaign v4, simulation only).

The reflex was validated only in simulation. In the held-out v4 campaign it had 0/66 collisions per
tier at 6, 10, 14, 20 and 30 m/s, with a Wilson 95% CI upper bound of 5.5%, and 0/198 false triggers.
Closing speeds above 30 m/s are outside the envelope: the measured collision rates were 7.6% at
60 m/s, 4.5% at 90 m/s (CI upper bound 12.5%) and 21.2% at 120 m/s. Escape needs the far-field stage
to arm at least about 0.5 s before contact, and that is detection-limited at those speeds.
Nothing here has been flight-tested.
"""
from __future__ import annotations

import warnings

#: Highest closing speed (m/s) validated against the acceptance bar (simulation only).
VALIDATED_MAX_CLOSING_SPEED_MPS = 30.0
#: Measured lead-time rule: no v4 collisions when armed >= 0.5 s before contact (0/141 trials).
REQUIRED_LEAD_TIME_S = 0.5
#: Object radius used in validation (0.30 m diameter sphere).
VALIDATED_OBJECT_RADIUS_M = 0.15
#: Threat cone used in validation (azimuth, elevation half-angles, deg, relative to the camera axis).
VALIDATED_THREAT_CONE_DEG = (15.0, 8.0)

#: Camera rig validated in v4: (lens_mm, yaw_deg, pitch_deg) of simulated Sony/Prophesee IMX636 sensors.
DEFAULT_RIG = [(8.0, 0.0, 0.0)] + [(25.0, y, p) for p in (7.0, 0.0, -7.0) for y in (-12.0, 0.0, 12.0)]
#: Far-field TTC stage settings frozen for v3/v4 (see giantfiber.farfield.FarFieldConfig).
DEFAULT_FARFIELD = {"tau_arm_s": 1.0, "tau_min_s": 0.15, "min_fit_span_us": 150000, "min_t_stat": 8.0,
                        "confirm_evals": 15, "max_miss_radii": 6.0}
#: Escape command: thrust-axis (climb, or descend if the object passes above), 20 m/s^2 for 1.2 s.
DEFAULT_ESCAPE = {"policy": "vertical", "accel_mps2": 20.0, "duration_s": 1.2}


class OutsideEnvelopeWarning(UserWarning):
    """The configured scenario is outside the validated safety envelope."""


def within_envelope(closing_speed_mps: float) -> bool:
    return closing_speed_mps <= VALIDATED_MAX_CLOSING_SPEED_MPS


def check_threat_speed(closing_speed_mps: float) -> bool:
    """Warn (OutsideEnvelopeWarning) if the speed is above the validated envelope; return whether it is inside."""
    ok = within_envelope(closing_speed_mps)
    if not ok:
        warnings.warn(f"closing speed {closing_speed_mps:g} m/s exceeds the validated envelope "
                      f"(<= {VALIDATED_MAX_CLOSING_SPEED_MPS:g} m/s, simulation only); measured collision "
                      f"rates above it were 4.5-21.2%", OutsideEnvelopeWarning, stacklevel=2)
    return ok
