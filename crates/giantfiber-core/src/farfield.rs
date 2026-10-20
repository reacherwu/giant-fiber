//! Far-field time-to-contact stage ("predictive arming").
//!
//! Engineering stage, **not** derived from the connectome. It runs at full sensor resolution
//! (up to 1280x720, e.g. a Prophesee IMX636) before the 64x64 LPLC2/LC4 -> Giant Fiber model and
//! can arm the escape hundreds of milliseconds before the Giant Fiber threshold is reached.
//!
//! Pipeline per evaluation:
//! 1. Events are binned into 16x16-pixel blocks over two tumbling 10 ms windows. A block is active
//!    when its count exceeds the noise floor estimated from the mean block count
//!    (mu + 4 sqrt(mu) + 4). The largest 8-connected cluster of active blocks is the candidate.
//! 2. A circle is fitted (algebraic Kasa fit) to the recent events inside the dilated cluster
//!    bounding box. That gives the image radius r and centre c with sub-pixel precision, even
//!    when only one side of the edge produces events.
//! 3. Over the last 80 ms, r(t) and c(t) are fitted with lines. tau = r / r_dot is the
//!    time-to-contact (small-angle approximation). The predicted miss vector, in object radii, is
//!    c_dot * tau / r (constant bearing = collision course). This holds for a constant-velocity
//!    object seen from a non-rotating camera and does not depend on the object's physical size.
//!    The events must therefore be rotation-compensated (derotated with the gyro / attitude
//!    estimate) before they are fed in; see `giantfiber.farfield.Derotator`.
//! 4. Arm when tau < `tau_arm_s`, the expansion is significant (r_dot / se > `min_t_stat`), the
//!    predicted miss is < `max_miss_radii` and all of this holds for `confirm_evals` consecutive
//!    evaluations.
use crate::math::sqrtf;

pub const BLOCK: usize = 16;
pub const MAX_W: usize = 1280;
pub const MAX_H: usize = 720;
const BW: usize = MAX_W / BLOCK;
const BH: usize = MAX_H / BLOCK;
const RING: usize = 1 << 15;
const HIST: usize = 256;
const WIN_US: u64 = 10_000;
const FIT_EVENTS_US: u64 = 12_000;

#[repr(C)]
#[derive(Debug, Clone, Copy)]
pub struct FarFieldConfig {
    pub width: u16,
    pub height: u16,
    pub tau_arm_s: f32,
    pub max_miss_radii: f32,
    pub min_t_stat: f32,
    pub fit_window_us: u32,
    pub min_fit_span_us: u32,
    pub confirm_evals: u32,
    pub min_radius_px: f32,
    /// No far-field arm below this tau: a multirotor cannot move meaningfully in less time, and
    /// near-field geometry (large bearing, edge of FOV) breaks the small-angle model.
    pub tau_min_s: f32,
}

impl Default for FarFieldConfig {
    fn default() -> Self {
        Self { width: 1280, height: 720, tau_arm_s: 1.0, max_miss_radii: 6.0, min_t_stat: 8.0,
               fit_window_us: 250_000, min_fit_span_us: 150_000, confirm_evals: 15, min_radius_px: 2.0, tau_min_s: 0.15 }
    }
}

#[repr(C)]
#[derive(Debug, Clone, Copy, Default)]
pub struct FarFieldOutput {
    pub tracking: u32,
    pub armed: u32,
    pub tau_s: f32,
    pub r_px: f32,
    pub r_dot_pps: f32,
    pub t_stat: f32,
    pub cx: f32,
    pub cy: f32,
    pub miss_radii: f32,
    /// unit vector (image x right, y down) of the predicted miss offset of the object relative to the camera axis
    pub miss_dir_x: f32,
    pub miss_dir_y: f32,
    pub fit_events: u32,
}

#[derive(Clone, Copy, Default)]
struct Sample { t: u64, r: f32, cx: f32, cy: f32 }

pub struct FarFieldTtc {
    pub cfg: FarFieldConfig,
    ex: [u16; RING],
    ey: [u16; RING],
    et: [u64; RING],
    head: usize,
    len: usize,
    cur: [[u16; BW]; BH],
    prev: [[u16; BW]; BH],
    cur_total: u32,
    prev_total: u32,
    win_start: u64,
    hist: [Sample; HIST],
    h_head: usize,
    h_len: usize,
    confirm: u32,
    rejected: u32,
    pub last: FarFieldOutput,
}

impl FarFieldTtc {
    pub fn new(cfg: FarFieldConfig) -> Self {
        let mut c = cfg;
        c.width = c.width.min(MAX_W as u16);
        c.height = c.height.min(MAX_H as u16);
        Self { cfg: c, ex: [0; RING], ey: [0; RING], et: [0; RING], head: 0, len: 0,
               cur: [[0; BW]; BH], prev: [[0; BW]; BH], cur_total: 0, prev_total: 0, win_start: 0,
               hist: [Sample::default(); HIST], h_head: 0, h_len: 0, confirm: 0, rejected: 0, last: FarFieldOutput::default() }
    }

    pub fn reset(&mut self) {
        let cfg = self.cfg;
        self.len = 0; self.head = 0; self.h_len = 0; self.h_head = 0; self.confirm = 0; self.rejected = 0;
        self.cur = [[0; BW]; BH]; self.prev = [[0; BW]; BH];
        self.cur_total = 0; self.prev_total = 0; self.win_start = 0;
        self.last = FarFieldOutput::default();
        self.cfg = cfg;
    }

    fn roll(&mut self, t: u64) {
        if self.win_start == 0 { self.win_start = t; }
        while t >= self.win_start + WIN_US {
            if t >= self.win_start + 2 * WIN_US {
                self.prev = [[0; BW]; BH]; self.prev_total = 0;
            } else {
                self.prev = self.cur; self.prev_total = self.cur_total;
            }
            self.cur = [[0; BW]; BH]; self.cur_total = 0;
            self.win_start = if t >= self.win_start + 2 * WIN_US { t } else { self.win_start + WIN_US };
        }
    }

    pub fn feed(&mut self, x: u16, y: u16, t: u64) {
        if x >= self.cfg.width || y >= self.cfg.height { return; }
        self.roll(t);
        let (bx, by) = (x as usize / BLOCK, y as usize / BLOCK);
        self.cur[by][bx] = self.cur[by][bx].saturating_add(1);
        self.cur_total += 1;
        self.ex[self.head] = x; self.ey[self.head] = y; self.et[self.head] = t;
        self.head = (self.head + 1) % RING;
        if self.len < RING { self.len += 1; }
    }

    fn push_sample(&mut self, s: Sample) {
        self.hist[self.h_head] = s;
        self.h_head = (self.h_head + 1) % HIST;
        if self.h_len < HIST { self.h_len += 1; }
    }

    fn last_sample(&self) -> Option<Sample> {
        if self.h_len == 0 { None } else { Some(self.hist[(self.h_head + HIST - 1) % HIST]) }
    }

    pub fn eval(&mut self, now: u64) -> FarFieldOutput {
        self.roll(now);
        let mut out = FarFieldOutput::default();
        let bw = (self.cfg.width as usize).div_ceil(BLOCK);
        let bh = (self.cfg.height as usize).div_ceil(BLOCK);
        let nb = (bw * bh) as f32;
        let mu = (self.cur_total + self.prev_total) as f32 / nb;
        let k = mu + 4.0 * sqrtf(mu) + 4.0;
        // active map + largest connected cluster (by event count)
        let mut label = [[0u16; BW]; BH];
        let mut stack = [(0u8, 0u8); BW * BH];
        let (mut best_sum, mut best_lab, mut best_box) = (0u32, 0u16, (0usize, 0usize, 0usize, 0usize));
        let mut next: u16 = 1;
        for y in 0..bh {
            for x in 0..bw {
                let c = (self.cur[y][x] + self.prev[y][x]) as f32;
                if c < k || label[y][x] != 0 { continue; }
                let mut sp = 0usize;
                stack[sp] = (x as u8, y as u8); sp += 1;
                label[y][x] = next;
                let (mut sum, mut nblk) = (0u32, 0u32);
                let (mut x0, mut x1, mut y0, mut y1) = (x, x, y, y);
                while sp > 0 {
                    sp -= 1;
                    let (cx, cy) = (stack[sp].0 as usize, stack[sp].1 as usize);
                    sum += (self.cur[cy][cx] + self.prev[cy][cx]) as u32;
                    nblk += 1;
                    x0 = x0.min(cx); x1 = x1.max(cx); y0 = y0.min(cy); y1 = y1.max(cy);
                    for dy in -1i32..=1 {
                        for dx in -1i32..=1 {
                            let (nx, ny) = (cx as i32 + dx, cy as i32 + dy);
                            if nx < 0 || ny < 0 || nx >= bw as i32 || ny >= bh as i32 { continue; }
                            let (nx, ny) = (nx as usize, ny as usize);
                            if label[ny][nx] == 0 && (self.cur[ny][nx] + self.prev[ny][nx]) as f32 >= k {
                                label[ny][nx] = next;
                                stack[sp] = (nx as u8, ny as u8); sp += 1;
                            }
                        }
                    }
                }
                if nblk >= 2 && sum > best_sum { best_sum = sum; best_lab = next; best_box = (x0, x1, y0, y1); }
                next = next.saturating_add(1);
            }
        }
        let _ = best_lab;
        let mut fit_ok = false;
        if best_sum >= 20 {
            let (x0, x1, y0, y1) = best_box;
            let px0 = (x0.saturating_sub(1) * BLOCK) as f32;
            let px1 = ((x1 + 2) * BLOCK) as f32;
            let py0 = (y0.saturating_sub(1) * BLOCK) as f32;
            let py1 = ((y1 + 2) * BLOCK) as f32;
            let (ox, oy) = ((px0 + px1) * 0.5, (py0 + py1) * 0.5);
            let horizon = now.saturating_sub(FIT_EVENTS_US);
            // Kasa circle fit in coordinates centred on the box, then one refit without outliers
            let mut circle: Option<(f32, f32, f32)> = None;
            for pass in 0..2 {
                let gate = circle;
                let (mut n, mut sx, mut sy, mut sxx, mut syy, mut sxy, mut sxz, mut syz, mut sz) =
                    (0u32, 0f64, 0f64, 0f64, 0f64, 0f64, 0f64, 0f64, 0f64);
                let mut i = 0;
                while i < self.len {
                    let idx = (self.head + RING - 1 - i) % RING;
                    if self.et[idx] < horizon { break; }
                    i += 1;
                    let (x, y) = (self.ex[idx] as f32 + 0.5, self.ey[idx] as f32 + 0.5);
                    if !(x >= px0 && x < px1 && y >= py0 && y < py1) { continue; }
                    let (uf, vf) = (x - ox, y - oy);
                    if let Some((gu, gv, gr)) = gate {
                        let dd = sqrtf((uf - gu) * (uf - gu) + (vf - gv) * (vf - gv));
                        if (dd - gr).abs() > (0.25 * gr).max(3.0) { continue; }
                    }
                    let (u, v) = (uf as f64, vf as f64);
                    let z = u * u + v * v;
                    n += 1; sx += u; sy += v; sxx += u * u; syy += v * v; sxy += u * v;
                    sxz += u * z; syz += v * z; sz += z;
                }
                out.fit_events = n;
                if n < 30 { circle = None; break; }
                let nf = n as f64;
                let a = [[sxx, sxy, sx], [sxy, syy, sy], [sx, sy, nf]];
                let b = [-sxz, -syz, -sz];
                circle = match solve3(a, b) {
                    Some([d, e, f]) => {
                        let (cu, cv) = ((-d * 0.5) as f32, (-e * 0.5) as f32);
                        let r2 = cu * cu + cv * cv - f as f32;
                        if r2 > 0.25 { Some((cu, cv, sqrtf(r2))) } else { None }
                    }
                    None => None,
                };
                if circle.is_none() || pass == 1 { break; }
            }
            if let Some((cu, cv, r)) = circle {
                let half = 0.5 * (px1 - px0).max(py1 - py0);
                let (gx, gy) = (cu + ox, cv + oy);
                // A disk clipped by the image border gives a biased circle fit: reject it
                let inside = gx - r > 2.0 && gy - r > 2.0 && gx + r < self.cfg.width as f32 - 2.0 && gy + r < self.cfg.height as f32 - 2.0;
                if inside && r < 2.0 * half && cu.abs() < 2.0 * half && cv.abs() < 2.0 * half {
                    let s = Sample { t: now, r, cx: cu + ox, cy: cv + oy };
                    if let Some(l) = self.last_sample() {
                        let jump = sqrtf((s.cx - l.cx) * (s.cx - l.cx) + (s.cy - l.cy) * (s.cy - l.cy));
                        if jump > (3.0 * l.r).max(24.0) || now > l.t + 40_000 { self.h_len = 0; }
                    }
                    // Outlier gate against the median of the last 5 samples
                    if self.h_len >= 5 {
                        let mut v = [0f32; 5];
                        for (k, vk) in v.iter_mut().enumerate() { *vk = self.hist[(self.h_head + HIST - 1 - k) % HIST].r; }
                        v.sort_unstable_by(|a, b| a.partial_cmp(b).unwrap_or(core::cmp::Ordering::Equal));
                        let med = v[2];
                        if (s.r - med).abs() <= 0.3 * med { self.push_sample(s); fit_ok = true; }
                        else { self.rejected += 1; if self.rejected > 10 { self.h_len = 0; self.rejected = 0; } fit_ok = true; }
                    } else {
                        self.push_sample(s);
                        fit_ok = true;
                    }
                    if fit_ok && self.h_len > 0 && self.last_sample().map(|l| l.t == now).unwrap_or(false) { self.rejected = 0; }
                }
            }
        }
        if !fit_ok {
            if let Some(l) = self.last_sample() { if now > l.t + 40_000 { self.h_len = 0; } }
        }
        // Fits over the history window. For a constant-velocity approach 1/r is linear in time, so
        // tau = (1/r)(now) / -(d(1/r)/dt) without small-tau curvature bias. Weighted least squares with
        // w = r^4 (var(1/r) ~ var(r) / r^4). The centre is fitted with an unweighted line.
        // Adaptive window: the constant-velocity model holds over a fraction of tau, so shrink the
        // window (and the required span) as the object closes in.
        let prev_tau = self.last.tau_s;
        let win_us = if prev_tau.is_finite() && prev_tau > 0.0 {
            ((0.3 * prev_tau * 1e6) as u64).clamp(60_000, self.cfg.fit_window_us as u64)
        } else { self.cfg.fit_window_us as u64 };
        let span_us = (self.cfg.min_fit_span_us as u64).min(win_us * 6 / 10);
        let w0 = now.saturating_sub(win_us);
        let (mut n, mut sw, mut swt, mut swtt, mut swy, mut swty) = (0u32, 0f64, 0f64, 0f64, 0f64, 0f64);
        let (mut st, mut stt, mut scx, mut stcx, mut scy, mut stcy, mut srn) = (0f64, 0f64, 0f64, 0f64, 0f64, 0f64, 0f32);
        let mut tmin = u64::MAX;
        let r_ref = self.last_sample().map(|l| l.r).unwrap_or(1.0).max(1.0) as f64;
        for j in 0..self.h_len {
            let s = self.hist[(self.h_head + HIST - 1 - j) % HIST];
            if s.t < w0 { break; }
            tmin = tmin.min(s.t);
            if j == 0 { srn = s.r; }
            let t = (now - s.t) as f64 * -1e-6; // seconds, <= 0
            let r = s.r as f64;
            let y = r_ref / r; // scaled 1/r
            let w = pow4(r / r_ref);
            n += 1; sw += w; swt += w * t; swtt += w * t * t; swy += w * y; swty += w * t * y;
            st += t; stt += t * t; scx += s.cx as f64; stcx += t * s.cx as f64; scy += s.cy as f64; stcy += t * s.cy as f64;
        }
        let _ = srn;
        out.tracking = (n > 0) as u32;
        if n >= 8 && now - tmin >= span_us {
            let nf = n as f64;
            let den_w = sw * swtt - swt * swt;
            let den = nf * stt - st * st;
            if den_w > 1e-15 && den > 1e-15 {
                let slope = (sw * swty - swt * swy) / den_w; // d(r_ref/r)/dt, negative when approaching
                let icpt = (swy - slope * swt) / sw;         // r_ref/r now
                // weighted residual variance -> slope standard error
                let mut sse = 0f64;
                for j in 0..self.h_len {
                    let s = self.hist[(self.h_head + HIST - 1 - j) % HIST];
                    if s.t < w0 { break; }
                    let t = (now - s.t) as f64 * -1e-6;
                    let r = s.r as f64;
                    let e = r_ref / r - (icpt + slope * t);
                    sse += pow4(r / r_ref) * e * e;
                }
                let se = sqrt64(sse / (nf - 2.0).max(1.0) / den_w * sw);
                let vx = (nf * stcx - st * scx) / den;
                let vy = (nf * stcy - st * scy) / den;
                let r_now = if icpt > 1e-6 { (r_ref / icpt) as f32 } else { 0.0 };
                out.r_px = r_now;
                out.r_dot_pps = if icpt > 1e-6 { (-slope / icpt * r_now as f64) as f32 } else { 0.0 };
                out.t_stat = if se > 1e-12 { (-slope / se) as f32 } else { 1e6 };
                out.cx = ((scx - vx * st) / nf) as f32;
                out.cy = ((scy - vy * st) / nf) as f32;
                if slope < 0.0 && icpt > 0.0 {
                    let tau = (icpt / -slope) as f32;
                    out.tau_s = tau;
                    let mx = vx as f32 * tau;
                    let my = vy as f32 * tau;
                    let m = sqrtf(mx * mx + my * my);
                    out.miss_radii = if r_now > 0.0 { m / r_now } else { f32::INFINITY };
                    if m > 1e-6 { out.miss_dir_x = mx / m; out.miss_dir_y = my / m; }
                    let threat = tau < self.cfg.tau_arm_s && tau >= self.cfg.tau_min_s && out.t_stat > self.cfg.min_t_stat
                        && out.miss_radii < self.cfg.max_miss_radii && r_now >= self.cfg.min_radius_px;
                    self.confirm = if threat { self.confirm + 1 } else { 0 };
                } else {
                    out.tau_s = f32::INFINITY;
                    self.confirm = 0;
                }
            } else {
                self.confirm = 0;
            }
        } else {
            self.confirm = 0;
        }
        out.armed = (self.confirm >= self.cfg.confirm_evals) as u32;
        self.last = out;
        out
    }
}

#[cfg(feature = "std")]
#[inline]
fn sqrt64(x: f64) -> f64 { x.sqrt() }
#[cfg(not(feature = "std"))]
#[inline]
fn sqrt64(x: f64) -> f64 { libm::sqrt(x) }
#[cfg(feature = "std")]
#[inline]
fn pow4(x: f64) -> f64 { x.powi(4) }
#[cfg(not(feature = "std"))]
#[inline]
fn pow4(x: f64) -> f64 { let y = x * x; y * y }

fn solve3(a: [[f64; 3]; 3], b: [f64; 3]) -> Option<[f64; 3]> {
    let det = |m: [[f64; 3]; 3]| m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]);
    let d = det(a);
    if d.abs() < 1e-9 { return None; }
    let mut x = [0f64; 3];
    for (c, xc) in x.iter_mut().enumerate() {
        let mut m = a;
        for r in 0..3 { m[r][c] = b[r]; }
        *xc = det(m) / d;
    }
    Some(x)
}
