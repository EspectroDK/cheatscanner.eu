"""Angular / vector math in Source-engine conventions.

Conventions
-----------
* Yaw: degrees, counter-clockwise from +X in the XY plane, normalized to [-180, 180).
* Pitch: degrees, **positive looks down** (Source). Range [-89, 89].
* Forward vector for (pitch, yaw):
  ``(cos p * cos y, cos p * sin y, -sin p)``.

All functions accept numpy arrays and broadcast.
"""

from __future__ import annotations

import numpy as np

RAD = np.pi / 180.0
DEG = 180.0 / np.pi


def wrap180(angle):
    """Wrap degrees to [-180, 180)."""
    return (np.asarray(angle, dtype=np.float64) + 180.0) % 360.0 - 180.0


def clamp_pitch(pitch):
    return np.clip(np.asarray(pitch, dtype=np.float64), -89.0, 89.0)


def yaw_delta(a, b):
    """Signed shortest yaw difference ``a - b`` in degrees."""
    return wrap180(np.asarray(a) - np.asarray(b))


def view_vector(pitch, yaw):
    """Unit forward vector(s) for Source angles (degrees)."""
    p = np.asarray(pitch, dtype=np.float64) * RAD
    y = np.asarray(yaw, dtype=np.float64) * RAD
    cp = np.cos(p)
    return np.stack([cp * np.cos(y), cp * np.sin(y), -np.sin(p)], axis=-1)


def bearing(src, dst):
    """(yaw, pitch) in degrees of the direction from ``src`` to ``dst``.

    ``src``/``dst`` are ``(..., 3)`` arrays. Pitch follows Source convention
    (positive when the target is *below* the source).
    """
    d = np.asarray(dst, dtype=np.float64) - np.asarray(src, dtype=np.float64)
    yaw = np.arctan2(d[..., 1], d[..., 0]) * DEG
    pitch = -np.arctan2(d[..., 2], np.hypot(d[..., 0], d[..., 1])) * DEG
    return wrap180(yaw), pitch


def angle_between_vectors(u, v):
    """Unsigned angle in degrees between vectors (numerically stable atan2 form)."""
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    cross = np.linalg.norm(np.cross(u, v), axis=-1)
    dot = np.sum(u * v, axis=-1)
    return np.arctan2(cross, dot) * DEG


def angular_distance(pitch1, yaw1, pitch2, yaw2):
    """Great-circle angle (degrees) between two view directions.

    Uses the haversine formulation, which is accurate for small angles (the
    regime that matters for aim analysis) unlike ``arccos(dot)``.
    """
    # Source pitch is inverted; elevation = -pitch.
    e1 = -np.asarray(pitch1, dtype=np.float64) * RAD
    e2 = -np.asarray(pitch2, dtype=np.float64) * RAD
    dy = wrap180(np.asarray(yaw1) - np.asarray(yaw2)) * RAD
    h = np.sin((e2 - e1) / 2) ** 2 + np.cos(e1) * np.cos(e2) * np.sin(dy / 2) ** 2
    return 2 * np.arcsin(np.sqrt(np.clip(h, 0.0, 1.0))) * DEG


def aim_error_components(view_pitch, view_yaw, target_pitch, target_yaw):
    """Signed error of the target relative to the crosshair.

    Returns ``(horizontal, vertical, total)`` in degrees. ``horizontal`` is the
    yaw difference scaled by ``cos(pitch)`` (so it is an on-screen angular
    distance), positive when the target is to the left (CCW). ``vertical`` is
    positive when the target is below the crosshair.
    """
    horiz = yaw_delta(target_yaw, view_yaw) * np.cos(np.asarray(target_pitch) * RAD)
    vert = np.asarray(target_pitch, dtype=np.float64) - np.asarray(view_pitch, dtype=np.float64)
    total = angular_distance(view_pitch, view_yaw, target_pitch, target_yaw)
    return horiz, vert, total


def unwrap_yaw(yaw):
    """Continuous yaw series (degrees) without ±180 jumps."""
    return np.unwrap(np.asarray(yaw, dtype=np.float64) * RAD) * DEG


def angular_speed(pitch, yaw, dt: float):
    """Per-sample great-circle angular speed (deg/s) of a view series.

    Output has the same length as the input; element ``i`` is the speed over
    ``[i-1, i]`` (element 0 is NaN).
    """
    pitch = np.asarray(pitch, dtype=np.float64)
    yaw = np.asarray(yaw, dtype=np.float64)
    out = np.full(pitch.shape, np.nan)
    if pitch.shape[-1] < 2:
        return out
    out[..., 1:] = angular_distance(pitch[..., 1:], yaw[..., 1:], pitch[..., :-1], yaw[..., :-1]) / dt
    return out


def smooth(x, window: int):
    """Centered moving average ignoring NaNs (window in samples, odd preferred)."""
    x = np.asarray(x, dtype=np.float64)
    if window <= 1 or x.size == 0:
        return x.copy()
    valid = ~np.isnan(x)
    k = np.ones(window)
    num = np.convolve(np.where(valid, x, 0.0), k, mode="same")
    den = np.convolve(valid.astype(float), k, mode="same")
    with np.errstate(invalid="ignore", divide="ignore"):
        out = num / den
    out[den == 0] = np.nan
    return out


def derivatives(x, dt: float, smoothing: int = 1):
    """First, second and third time derivatives of a 1-D series.

    Finite differences on 64-tick data amplify noise strongly (jerk especially);
    ``smoothing`` applies a moving average before each differentiation. The
    results are only comparable between observations processed identically.
    """
    x = smooth(np.asarray(x, dtype=np.float64), smoothing)
    v = np.gradient(x, dt) if x.size > 1 else np.full_like(x, np.nan)
    v = smooth(v, smoothing)
    a = np.gradient(v, dt) if x.size > 1 else np.full_like(x, np.nan)
    a = smooth(a, smoothing)
    j = np.gradient(a, dt) if x.size > 1 else np.full_like(x, np.nan)
    return v, a, j


def pearson(a, b, min_n: int = 5) -> float:
    """NaN-safe Pearson correlation; NaN when undefined."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    m = ~(np.isnan(a) | np.isnan(b))
    if m.sum() < min_n:
        return float("nan")
    a, b = a[m], b[m]
    sa, sb = a.std(), b.std()
    if sa < 1e-12 or sb < 1e-12:
        return float("nan")
    return float(np.mean((a - a.mean()) * (b - b.mean())) / (sa * sb))


def sign_changes(x, min_abs: float = 0.0) -> np.ndarray:
    """Indices where the sign of ``x`` flips (ignoring |x| < min_abs samples)."""
    x = np.asarray(x, dtype=np.float64)
    s = np.sign(np.where(np.abs(x) < min_abs, 0.0, x))
    idx = np.nonzero(s)[0]
    if idx.size < 2:
        return np.array([], dtype=int)
    flips = np.nonzero(s[idx][1:] != s[idx][:-1])[0]
    return idx[flips + 1]
