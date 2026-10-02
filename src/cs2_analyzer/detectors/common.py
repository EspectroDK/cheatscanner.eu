"""Shared helpers for detectors."""

from __future__ import annotations

import numpy as np

from cs2_analyzer.features.pair import PairSeries, target_point
from cs2_analyzer.geometry.angles import angular_distance, bearing, pearson, sign_changes, smooth
from cs2_analyzer.geometry.visibility import LOS
from cs2_analyzer.knowledge.model import NEVER, Knowledge


def runs(mask: np.ndarray) -> list[tuple[int, int]]:
    """Inclusive (start, end) index pairs of True runs."""
    m = np.asarray(mask, dtype=np.int8)
    d = np.diff(np.r_[0, m, 0])
    starts = np.nonzero(d == 1)[0]
    ends = np.nonzero(d == -1)[0] - 1
    return list(zip(starts.tolist(), ends.tolist()))


def hidden_mask(ctx, o: int, e: int, los_codes: tuple[int, ...]) -> np.ndarray:
    """Ticks where e is hidden from o AND o has no modelled legitimate information."""
    k = ctx.knowledge
    return (
        (k.level[o, e] == Knowledge.UNKNOWN)
        & (k.info_confidence[o, e] > 0)
        & np.isin(ctx.vis.los[o, e], los_codes)
        & ctx.world.alive[o]
        & ctx.world.alive[e]
    )


def windows(a: int, b: int, max_len: int, step: int) -> list[tuple[int, int]]:
    if b - a + 1 <= max_len:
        return [(a, b)]
    out = []
    s = a
    while s + max_len - 1 <= b:
        out.append((s, s + max_len - 1))
        s += step
    if out[-1][1] < b:
        out.append((b - max_len + 1, b))
    return out


def lagged_corr(aim: np.ndarray, target: np.ndarray, max_lag: int, min_n: int = 8) -> tuple[float, int]:
    """Max Pearson correlation of aim vs target shifted so aim LAGS target by 0..max_lag samples."""
    best, best_lag = float("nan"), 0
    for lag in range(0, max_lag + 1):
        a = aim[lag:]
        b = target[: len(target) - lag] if lag else target
        c = pearson(a, b, min_n=min_n)
        if np.isfinite(c) and (not np.isfinite(best) or c > best):
            best, best_lag = c, lag
    return best, best_lag


def tracking_metrics(ps: PairSeries, dt: float, smooth_n: int = 5, max_lag: int = 16,
                     min_target_speed_deg_s: float = 4.0, reversal_min_deg_s: float = 6.0,
                     reversal_match_ticks: int = 26) -> dict:
    """How well does the view follow the TARGET-induced bearing change?"""
    a = smooth(ps.d_aim_yaw, smooth_n)
    b = smooth(ps.d_bearing_target_yaw, smooth_n)
    moving = np.abs(b) >= min_target_speed_deg_s * dt
    a_m = np.where(moving, a, np.nan)
    b_m = np.where(moving, b, np.nan)
    corr0 = pearson(a_m, b_m)
    lag_corr, lag = lagged_corr(a_m, b_m, max_lag)
    # gain: how much the aim moves per degree of target motion (ideal tracking ~1)
    mm = ~(np.isnan(a_m) | np.isnan(b_m))
    gain = float(np.sum(a_m[mm] * b_m[mm]) / np.sum(b_m[mm] ** 2)) if mm.sum() >= 5 and np.sum(b_m[mm] ** 2) > 0 else float("nan")

    # direction reversals of the target (in target-induced bearing) and whether the aim follows
    bs = smooth(ps.d_bearing_target_yaw, 9)
    as_ = smooth(ps.d_aim_yaw, 9)
    thr = reversal_min_deg_s * dt
    rev = sign_changes(bs, min_abs=thr)
    matched = 0
    for r in rev:
        direction = np.sign(bs[r])
        seg = as_[r : r + reversal_match_ticks]
        if np.any((np.sign(seg) == direction) & (np.abs(seg) >= thr)):
            # require the aim to have been moving the other way (or still) before
            before = as_[max(0, r - reversal_match_ticks) : r]
            if before.size == 0 or np.nanmean(before) * direction <= thr:
                matched += 1

    err = ps.err
    fin = np.isfinite(err)
    n = max(int(fin.sum()), 1)
    return {
        "n_ticks": int(len(ps)),
        "duration_ms": len(ps) * dt * 1000.0,
        "moving_ticks": int(moving.sum()),
        "corr_zero_lag": corr0,
        "tracking_corr": lag_corr,
        "tracking_lag_ms": lag * dt * 1000.0,
        "tracking_gain": gain,
        "target_angular_path_deg": float(np.nansum(np.abs(ps.d_bearing_target_yaw))),
        "observer_induced_path_deg": float(np.nansum(np.abs(ps.d_bearing_observer_yaw))),
        "aim_path_deg": float(np.nansum(np.abs(ps.d_aim_yaw))),
        "target_physical_path_u": float(np.nansum(ps.target_speed) * dt),
        "observer_physical_path_u": float(np.nansum(ps.observer_speed) * dt),
        "target_reversals": int(len(rev)),
        "matched_reversals": int(matched),
        "mean_error_deg": float(np.nanmean(err)) if fin.any() else float("nan"),
        "median_error_deg": float(np.nanmedian(err)) if fin.any() else float("nan"),
        "min_error_deg": float(np.nanmin(err)) if fin.any() else float("nan"),
        "ms_within_1deg": float((err[fin] <= 1).sum() * dt * 1000.0),
        "ms_within_2deg": float((err[fin] <= 2).sum() * dt * 1000.0),
        "ms_within_5deg": float((err[fin] <= 5).sum() * dt * 1000.0),
        "frac_within_5deg": float((err[fin] <= 5).sum() / n),
        "mean_distance_u": float(np.nanmean(ps.distance)),
    }


def error_to_point(world, o: int, idx: np.ndarray, point: np.ndarray) -> np.ndarray:
    """Angular error from o's view to a fixed/moving world point (deg)."""
    by, bp = bearing(world.eye[o, idx].astype(np.float64), point)
    return angular_distance(world.pitch[o, idx], world.yaw[o, idx], bp, by)


def last_known_positions(ctx, o: int, e: int, idx: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Target head position at the last tick o had any information about e."""
    w = ctx.world
    last = ctx.knowledge.last_info[o, e, idx]
    has = last != NEVER
    li = np.where(has, last, idx)
    return target_point(w, e, li), has


HIDDEN_GEOMETRY = (int(LOS.GEOMETRY_OCCLUDED),)
HIDDEN_SMOKE = (int(LOS.SMOKE_OCCLUDED),)
