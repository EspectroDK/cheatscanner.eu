"""Per (observer, target) angular time series.

This is the core kinematic representation used by the detectors and exported
as the per-tick channels of encounter windows (future ML input).

Crucially it separates the change of the target's bearing into

* ``d_bearing_target``: caused by the TARGET moving (observer held fixed), and
* ``d_bearing_observer``: caused by the OBSERVER moving (target held fixed),

so "the enemy appears to move because the observer moved" is never mistaken
for "the enemy moved and the crosshair followed it".
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cs2_analyzer.geometry.angles import aim_error_components, angular_speed, bearing, yaw_delta
from cs2_analyzer.world import World


@dataclass
class PairSeries:
    o: int
    e: int
    t: np.ndarray  # tick indices
    target_yaw: np.ndarray
    target_pitch: np.ndarray
    err_h: np.ndarray  # signed horizontal error (deg, + = target left of crosshair)
    err_v: np.ndarray  # signed vertical error (deg, + = target below)
    err: np.ndarray  # total angular error (deg)
    d_aim_yaw: np.ndarray  # per-tick view yaw change
    d_aim_pitch: np.ndarray
    d_bearing_target_yaw: np.ndarray
    d_bearing_observer_yaw: np.ndarray
    d_bearing_target_pitch: np.ndarray
    distance: np.ndarray
    target_speed: np.ndarray
    observer_speed: np.ndarray
    aim_speed: np.ndarray  # deg/s great-circle

    def __len__(self) -> int:
        return len(self.t)


def target_point(world: World, e: int, idx) -> np.ndarray:
    """Aim reference point on the target: the head (eye) position."""
    return world.eye[e, idx].astype(np.float64)


def pair_series(world: World, o: int, e: int, t0: int, t1: int) -> PairSeries:
    """Series over tick indices [t0, t1] (inclusive). NaN where data is missing."""
    t0 = max(0, t0)
    t1 = min(world.T - 1, t1)
    idx = np.arange(t0, t1 + 1)
    prev = np.maximum(idx - 1, 0)
    eye = world.eye[o, idx].astype(np.float64)
    eye_prev = world.eye[o, prev].astype(np.float64)
    tgt = target_point(world, e, idx)
    tgt_prev = target_point(world, e, prev)

    by, bp = bearing(eye, tgt)
    h, v, tot = aim_error_components(world.pitch[o, idx], world.yaw[o, idx], bp, by)

    # bearing decomposition around the previous observer position
    by_tt, bp_tt = bearing(eye_prev, tgt)  # observer fixed at t-1, target at t
    by_pp, bp_pp = bearing(eye_prev, tgt_prev)  # both at t-1
    d_tgt_yaw = yaw_delta(by_tt, by_pp)
    d_obs_yaw = yaw_delta(by, by_tt)
    d_tgt_pitch = bp_tt - bp_pp
    d_aim_yaw = yaw_delta(world.yaw[o, idx], world.yaw[o, prev])
    d_aim_pitch = world.pitch[o, idx] - world.pitch[o, prev]
    for arr in (d_tgt_yaw, d_obs_yaw, d_tgt_pitch, d_aim_yaw, d_aim_pitch):
        arr[0] = np.nan

    dist = np.linalg.norm(tgt - eye, axis=-1)
    return PairSeries(
        o=o,
        e=e,
        t=idx,
        target_yaw=by,
        target_pitch=bp,
        err_h=h,
        err_v=v,
        err=tot,
        d_aim_yaw=d_aim_yaw,
        d_aim_pitch=d_aim_pitch,
        d_bearing_target_yaw=d_tgt_yaw,
        d_bearing_observer_yaw=d_obs_yaw,
        d_bearing_target_pitch=d_tgt_pitch,
        distance=dist,
        target_speed=world.speed2d[e, idx].astype(np.float64),
        observer_speed=world.speed2d[o, idx].astype(np.float64),
        aim_speed=angular_speed(world.pitch[o, idx], world.yaw[o, idx], world.dt),
    )


def angular_radius(distance, radius_units: float = 5.0):
    """Angular radius (deg) of a sphere of ``radius_units`` at ``distance``."""
    d = np.maximum(np.asarray(distance, dtype=np.float64), 1.0)
    return np.degrees(np.arctan2(radius_units, d))
