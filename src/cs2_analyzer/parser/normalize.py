"""Tick normalization: raw parser columns -> the analysis tick schema.

Only fields required for analysis are kept. Values that the demo does not
contain are either derived with the derivation documented here, or left
missing; nothing is silently fabricated.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cs2_analyzer.features.weapons import canonical_weapon, weapon_class

# Eye heights above the player origin. Standing 64u / crouched 46u are the
# engine's view offsets and were confirmed against fire_bullets.origin_z on a
# real demo (see docs/parser-notes.md).
EYE_HEIGHT_STANDING = 64.0
EYE_HEIGHT_CROUCH = 46.0

# Movement faster than this between consecutive ticks is a teleport (spawn,
# round reset) rather than movement; derived velocity is left missing there.
MAX_PLAUSIBLE_SPEED = 1500.0

NORMALIZED_COLUMNS = [
    "tick", "game_time", "round", "round_live", "steam_id", "team",
    "position_x", "position_y", "position_z", "eye_x", "eye_y", "eye_z",
    "view_yaw", "view_pitch", "velocity_x", "velocity_y", "velocity_z", "speed_2d",
    "alive", "health", "weapon", "weapon_class", "ammo",
    "is_scoped", "is_crouching", "duck_amount", "is_walking", "is_airborne",
    "flash_duration_prop", "flash_alpha", "shots_fired", "aim_punch_pitch", "aim_punch_yaw",
    "attack", "buttons", "spotted", "spotted_by",
]


def normalize_yaw(yaw):
    """Map yaw (degrees) to [-180, 180)."""
    return (np.asarray(yaw, dtype=np.float64) + 180.0) % 360.0 - 180.0


# Where newer CS2 builds network the aim punch angle (demoparser2's
# ``aim_punch_angle`` is empty there).
AIM_PUNCH_SERVICES_PROP = "CCSPlayerPawn.CCSPlayer_AimPunchServices.m_predictableBaseAngle"


def _punch(values: pd.Series, idx: int) -> np.ndarray:
    out = np.full(len(values), np.nan)
    for i, v in enumerate(values.to_numpy()):
        if v is not None and not (isinstance(v, float) and np.isnan(v)) and len(v) > idx:
            out[i] = v[idx]
    return out


def assign_rounds(ticks: np.ndarray, rounds: pd.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Return (round_number, live) arrays for tick values.

    A tick belongs to the last round whose ``start_tick <= tick``. It is live
    between ``freeze_end_tick`` and ``end_tick`` inclusive.
    """
    rnum = np.zeros(len(ticks), dtype=np.int16)
    live = np.zeros(len(ticks), dtype=bool)
    if rounds is None or not len(rounds):
        return rnum, live
    r = rounds.sort_values("start_tick")
    starts = r["start_tick"].to_numpy()
    pos = np.searchsorted(starts, ticks, side="right") - 1
    valid = pos >= 0
    rnum[valid] = r["round_number"].to_numpy()[pos[valid]]
    fe = r["freeze_end_tick"].to_numpy(dtype=float)
    en = r["end_tick"].to_numpy(dtype=float)
    p = np.clip(pos, 0, len(r) - 1)
    live = valid & ~np.isnan(fe[p]) & ~np.isnan(en[p]) & (ticks >= np.nan_to_num(fe[p], nan=np.inf)) & (
        ticks <= np.nan_to_num(en[p], nan=-np.inf)
    )
    return rnum, live


def derive_velocity(df: pd.DataFrame, tickrate: float) -> pd.DataFrame:
    """Central-difference velocity from positions, per player (u/s)."""
    df = df.sort_values(["steam_id", "tick"])
    out = {}
    for axis in ("x", "y", "z"):
        pos = df[f"position_{axis}"].to_numpy(dtype=float)
        tick = df["tick"].to_numpy(dtype=float)
        sid = df["steam_id"].to_numpy()
        prev_same = np.r_[False, sid[1:] == sid[:-1]]
        next_same = np.r_[sid[:-1] == sid[1:], False]
        p_prev = np.r_[np.nan, pos[:-1]]
        p_next = np.r_[pos[1:], np.nan]
        t_prev = np.r_[np.nan, tick[:-1]]
        t_next = np.r_[tick[1:], np.nan]
        both = prev_same & next_same
        v = np.full(len(pos), np.nan)
        v[both] = (p_next[both] - p_prev[both]) / ((t_next[both] - t_prev[both]) / tickrate)
        only_prev = prev_same & ~next_same
        v[only_prev] = (pos[only_prev] - p_prev[only_prev]) / ((tick[only_prev] - t_prev[only_prev]) / tickrate)
        out[axis] = v
    df["velocity_x"], df["velocity_y"], df["velocity_z"] = out["x"], out["y"], out["z"]
    speed3 = np.sqrt(df["velocity_x"] ** 2 + df["velocity_y"] ** 2 + df["velocity_z"] ** 2)
    bad = speed3 > MAX_PLAUSIBLE_SPEED
    df.loc[bad, ["velocity_x", "velocity_y", "velocity_z"]] = np.nan
    df["speed_2d"] = np.sqrt(df["velocity_x"] ** 2 + df["velocity_y"] ** 2)
    return df


def _opt(raw: pd.DataFrame, name: str, default, missing: list[str]) -> pd.Series:
    """An optional prop; demos and parser versions differ in which props they carry."""
    if name in raw:
        return raw[name].fillna(default) if default is not None and default == default else raw[name]
    missing.append(name)
    return pd.Series([default] * len(raw), index=raw.index, dtype=object if default is None else None)


def normalize_ticks(raw: pd.DataFrame, rounds: pd.DataFrame, tickrate: float) -> pd.DataFrame:
    """Required: tick, game_time, steamid, team_num, X, Y, Z, pitch, yaw. Missing
    optional props get neutral defaults and are listed in ``df.attrs["missing_props"]``."""
    missing: list[str] = []
    df = pd.DataFrame(
        {
            "tick": raw["tick"].astype("int64"),
            "game_time": raw["game_time"].astype("float64"),
            "steam_id": raw["steamid"].astype("int64"),
            "team": raw["team_num"].fillna(0).astype("int16"),
            "position_x": raw["X"].astype("float64"),
            "position_y": raw["Y"].astype("float64"),
            "position_z": raw["Z"].astype("float64"),
            "view_yaw": normalize_yaw(raw["yaw"]),
            "view_pitch": raw["pitch"].astype("float64"),
            "alive": _opt(raw, "is_alive", False, missing).astype(bool),
            "health": _opt(raw, "health", 0, missing).astype("int16"),
            "ammo": _opt(raw, "m_iClip1", np.nan, missing).astype("float64"),
            "is_scoped": _opt(raw, "is_scoped", False, missing).astype(bool),
            "duck_amount": _opt(raw, "duck_amount", 0.0, missing).astype("float64"),
            "is_walking": _opt(raw, "is_walking", False, missing).astype(bool),
            "is_airborne": _opt(raw, "is_airborne", False, missing).astype(bool),
            "flash_duration_prop": _opt(raw, "flash_duration", 0.0, missing).astype("float64"),
            "flash_alpha": _opt(raw, "flash_max_alpha", 0.0, missing).astype("float64"),
            "shots_fired": _opt(raw, "shots_fired", 0, missing).astype("int32"),
            "attack": _opt(raw, "FIRE", False, missing).astype(bool),
            "buttons": _opt(raw, "buttons", 0, missing).astype("int64"),
            "spotted": _opt(raw, "spotted", False, missing).astype(bool),
            "spotted_by": _opt(raw, "approximate_spotted_by", None, missing).map(
                lambda v: [int(x) for x in v] if isinstance(v, (list, np.ndarray)) else []
            ),
        }
    )
    if "aim_punch_angle" not in raw and AIM_PUNCH_SERVICES_PROP in raw:
        punch = raw[AIM_PUNCH_SERVICES_PROP]
    else:
        punch = _opt(raw, "aim_punch_angle", None, missing)
    df["aim_punch_pitch"] = _punch(punch, 0)
    df["aim_punch_yaw"] = _punch(punch, 1)
    weapons = _opt(raw, "active_weapon_name", None, missing)
    uniq = {w: canonical_weapon(w) for w in weapons.dropna().unique()}
    df["weapon"] = weapons.map(uniq)
    classes = {w: weapon_class(w) for w in set(uniq.values()) if w}
    df["weapon_class"] = df["weapon"].map(classes).fillna("unknown")
    df["is_crouching"] = df["duck_amount"] > 0.5
    df["eye_x"] = df["position_x"]
    df["eye_y"] = df["position_y"]
    df["eye_z"] = df["position_z"] + EYE_HEIGHT_STANDING - (EYE_HEIGHT_STANDING - EYE_HEIGHT_CROUCH) * df["duck_amount"]
    rnum, live = assign_rounds(df["tick"].to_numpy(), rounds)
    df["round"] = rnum
    df["round_live"] = live
    df = derive_velocity(df, tickrate)
    df = df.sort_values(["tick", "steam_id"]).reset_index(drop=True)
    out = df[NORMALIZED_COLUMNS]
    out.attrs["missing_props"] = sorted(set(missing))
    return out
