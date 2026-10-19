//! Minimal float math shim so the core builds both with `std` and on `no_std` targets (via `libm`).

#[cfg(feature = "std")]
mod imp {
    #[inline(always)] pub fn expf(x: f32) -> f32 { x.exp() }
    #[inline(always)] pub fn sqrtf(x: f32) -> f32 { x.sqrt() }
    #[inline(always)] pub fn sinf(x: f32) -> f32 { x.sin() }
    #[inline(always)] pub fn cosf(x: f32) -> f32 { x.cos() }
    #[inline(always)] pub fn atanf(x: f32) -> f32 { x.atan() }
    #[inline(always)] pub fn atan2f(y: f32, x: f32) -> f32 { y.atan2(x) }
    #[inline(always)] pub fn tanf(x: f32) -> f32 { x.tan() }
    #[inline(always)] pub fn acosf(x: f32) -> f32 { x.acos() }
    #[inline(always)] pub fn floorf(x: f32) -> f32 { x.floor() }
}

#[cfg(not(feature = "std"))]
mod imp {
    #[inline(always)] pub fn expf(x: f32) -> f32 { libm::expf(x) }
    #[inline(always)] pub fn sqrtf(x: f32) -> f32 { libm::sqrtf(x) }
    #[inline(always)] pub fn sinf(x: f32) -> f32 { libm::sinf(x) }
    #[inline(always)] pub fn cosf(x: f32) -> f32 { libm::cosf(x) }
    #[inline(always)] pub fn atanf(x: f32) -> f32 { libm::atanf(x) }
    #[inline(always)] pub fn atan2f(y: f32, x: f32) -> f32 { libm::atan2f(y, x) }
    #[inline(always)] pub fn tanf(x: f32) -> f32 { libm::tanf(x) }
    #[inline(always)] pub fn acosf(x: f32) -> f32 { libm::acosf(x) }
    #[inline(always)] pub fn floorf(x: f32) -> f32 { libm::floorf(x) }
}

pub use imp::*;

/// Euclidean remainder for f32 (`f32::rem_euclid` is std-only).
#[inline(always)]
pub fn rem_euclidf(a: f32, b: f32) -> f32 {
    let r = a - b * floorf(a / b);
    if r >= b { r - b } else { r }
}

#[inline(always)]
pub fn sigmoid(x: f32) -> f32 {
    1.0 / (1.0 + expf(-x))
}
