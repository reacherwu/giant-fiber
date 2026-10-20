"""Event pre-filters."""
import numpy as np


class BackgroundActivityFilter:
    """Nearest-neighbour background-activity filter (the standard DVS denoiser, e.g. Delbruck 2008):
    an event passes only if one of its 8 neighbours fired within `window_us`. Uncorrelated sensor
    noise at 0.1 Hz/px passes with probability ~8 * 0.1 * window, i.e. 0.4% for 5 ms, while a moving
    edge, whose neighbours fire together, passes."""

    def __init__(self, width, height, window_us=5000):
        self.w, self.h, self.win = width, height, window_us
        self.last = np.full((height + 2, width + 2), -(1 << 40), dtype=np.int64)

    def apply(self, ev):
        if len(ev) == 0:
            return ev
        x = ev["x"].astype(np.int64) + 1
        y = ev["y"].astype(np.int64) + 1
        t = ev["timestamp_us"].astype(np.int64)
        self.last[y, x] = np.maximum(self.last[y, x], t)
        best = np.full(len(ev), -(1 << 40), dtype=np.int64)
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                if dx == 0 and dy == 0:
                    continue
                best = np.maximum(best, self.last[y + dy, x + dx])
        keep = np.abs(t - best) <= self.win
        return ev[keep]
