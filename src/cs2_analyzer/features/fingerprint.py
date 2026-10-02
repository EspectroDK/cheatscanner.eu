"""Per-player behavioral fingerprint and behavior-shift detection.

A fingerprint summarizes a player's raw observations in one match (aim
velocity, overshoot, corrections, trigger timing, recoil, ...). Stored per
match, it lets the system compare a player to their *own* history.

A large shift is exposed as a feature (``behavior_shift``). It is NOT
evidence by itself: players change sensitivity, hardware, form and role.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cs2_analyzer.detectors.base import ObservationSink

# (detector, column, statistic)
FINGERPRINT_SPEC = [
    ("snap", "peak_velocity_deg_s", "median"),
    ("snap", "peak_velocity_deg_s", "p90"),
    ("snap", "peak_jerk_deg_s3", "median"),
    ("snap", "overshoot_deg", "median"),
    ("snap", "correction_count", "mean"),
    ("snap", "target_error_after_deg", "median"),
    ("aim_acquisition", "reaction_ms", "median"),
    ("aim_acquisition", "acquisition_ms", "median"),
    ("aim_acquisition", "corrections", "mean"),
    ("aim_acquisition", "overshoot_deg", "median"),
    ("aim_acquisition", "peak_acceleration_deg_s2", "median"),
    ("trigger_timing", "trigger_ms", "median"),
    ("trigger_timing", "trigger_ms", "std"),
    ("recoil", "residual_ratio", "median"),
    ("recoil", "compensation_corr", "median"),
    ("hidden_tracking", "tracking_corr", "median"),
    ("hidden_tracking", "mean_error_deg", "median"),
    ("attraction", "visible_near_p_toward", "mean"),
    ("attraction", "hidden_near_p_toward", "mean"),
    ("remembered_position", "advantage_2000ms_deg", "mean"),
]


def _stat(s: pd.Series, how: str):
    s = pd.to_numeric(s, errors="coerce").dropna()
    if s.empty:
        return None
    if how == "median":
        return float(s.median())
    if how == "mean":
        return float(s.mean())
    if how == "std":
        return float(s.std()) if len(s) > 1 else None
    if how == "p90":
        return float(s.quantile(0.9))
    raise ValueError(how)


def fingerprint(obs: ObservationSink, steam_id: int) -> dict:
    out: dict = {}
    for det, col, how in FINGERPRINT_SPEC:
        df = obs.frame(det)
        if df.empty or col not in df or "steam_id" not in df:
            continue
        sub = df[df["steam_id"] == steam_id]
        if det == "trigger_timing" and "trigger_class" in sub:
            sub = sub[sub["trigger_class"] == "crosshair_moved"]
        val = _stat(sub[col], how) if len(sub) else None
        out[f"{det}.{col}.{how}"] = val
        out[f"{det}.n"] = int(len(sub))
    return out


def behavior_shift(current: dict, history: list[dict], min_matches: int = 3, z_flag: float = 3.0) -> dict:
    """Robust z-scores of the current fingerprint vs the player's previous matches."""
    if len(history) < min_matches:
        return {"available": False, "reason": f"needs {min_matches} previous matches, have {len(history)}"}
    shifts = {}
    for key, val in current.items():
        if val is None or key.endswith(".n"):
            continue
        prev = np.array([h.get(key) for h in history if h.get(key) is not None], dtype=float)
        if prev.size < min_matches:
            continue
        med = np.median(prev)
        mad = np.median(np.abs(prev - med)) * 1.4826
        if mad <= 1e-9:
            continue
        z = (val - med) / mad
        shifts[key] = {"value": val, "history_median": float(med), "robust_z": float(z), "flag": bool(abs(z) >= z_flag)}
    return {"available": True, "metrics": shifts, "flagged": sorted(k for k, v in shifts.items() if v["flag"])}
