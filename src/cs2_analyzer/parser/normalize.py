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


def _velocity(pos: dict[str, np.ndarray], tick: np.ndarray, sid: np.ndarray, tickrate: float) -> dict[str, np.ndarray]:
    """Central-difference velocity (u/s) of rows already ordered by (steam_id, tick).

    Returns ``velocity_x/y/z`` and ``speed_2d``; teleports (faster than
    ``MAX_PLAUSIBLE_SPEED``) get NaN velocity.
    """
    tick = tick.astype(float)
    prev_same = np.r_[False, sid[1:] == sid[:-1]]
    next_same = np.r_[sid[:-1] == sid[1:], False]
    both = prev_same & next_same
    only_prev = prev_same & ~next_same
    t_prev = np.r_[np.nan, tick[:-1]]
    t_next = np.r_[tick[1:], np.nan]
    out = {}
    for axis in ("x", "y", "z"):
        p = pos[axis]
        p_prev = np.r_[np.nan, p[:-1]]
        p_next = np.r_[p[1:], np.nan]
        v = np.full(len(p), np.nan)
        v[both] = (p_next[both] - p_prev[both]) / ((t_next[both] - t_prev[both]) / tickrate)
        v[only_prev] = (p[only_prev] - p_prev[only_prev]) / ((tick[only_prev] - t_prev[only_prev]) / tickrate)
        out[f"velocity_{axis}"] = v
    vx, vy, vz = out["velocity_x"], out["velocity_y"], out["velocity_z"]
    bad = np.sqrt(vx ** 2 + vy ** 2 + vz ** 2) > MAX_PLAUSIBLE_SPEED
    for v in (vx, vy, vz):
        v[bad] = np.nan
    out["speed_2d"] = np.sqrt(vx ** 2 + vy ** 2)
    return out


def derive_velocity(df: pd.DataFrame, tickrate: float) -> pd.DataFrame:
    """Central-difference velocity from positions, per player (u/s)."""
    df = df.sort_values(["steam_id", "tick"])
    vel = _velocity({a: df[f"position_{a}"].to_numpy(dtype=float) for a in ("x", "y", "z")},
                    df["tick"].to_numpy(), df["steam_id"].to_numpy(), tickrate)
    for c, v in vel.items():
        df[c] = v
    return df


def _spotted_lists(values: pd.Series) -> pd.Series:
    """Observer steam ids per row as a list of ints (``[]`` when missing).

    Rows that already hold a list of ints (demoparser2) are kept as they are
    instead of copied: a match has millions of rows.
    """
    def conv(v):
        if type(v) is list and all(type(x) is int for x in v):
            return v
        return [int(x) for x in v] if isinstance(v, (list, np.ndarray)) else []

    return values.map(conv)


def _opt(raw: pd.DataFrame, name: str, default, missing: list[str]) -> pd.Series:
    """An optional prop; demos and parser versions differ in which props they carry."""
    if name in raw:
        return raw[name].fillna(default) if default is not None and default == default else raw[name]
    missing.append(name)
    return pd.Series([default] * len(raw), index=raw.index, dtype=object if default is None else None)


def normalize_ticks(raw: pd.DataFrame, rounds: pd.DataFrame, tickrate: float) -> pd.DataFrame:
    """Required: tick, game_time, steamid, team_num, X, Y, Z, pitch, yaw. Missing
    optional props get neutral defaults and are listed in ``df.attrs["missing_props"]``.

    ``raw`` may already carry ``aim_punch_pitch``/``aim_punch_yaw`` (split by the
    parser backend while parsing, to save memory) instead of an aim punch prop.

    Rows are ordered by (tick, steam_id). Each column is put in that order as soon
    as it is made, so the table is never held in several copies (it is the largest
    object of an analysis).
    """
    missing: list[str] = []
    tick = raw["tick"].to_numpy().astype("int64")
    sid = raw["steamid"].to_numpy().astype("int64")
    order = np.lexsort((sid, tick))  # stable, like pandas' multi-column sort_values
    out = pd.DataFrame(index=pd.RangeIndex(len(raw)))

    def put(name: str, values) -> None:
        # one column at a time: assigning to a DataFrame keeps it as its own block,
        # while building one from a dict would copy everything into 2-D blocks
        out[name] = _take(values, order)

    rnum, live = assign_rounds(tick, rounds)
    put("tick", tick)
    put("game_time", raw["game_time"].astype("float64"))
    put("round", rnum)
    put("round_live", live)
    del rnum, live
    put("steam_id", sid)
    put("team", raw["team_num"].fillna(0).astype("int16"))
    pos = {a: raw[c].astype("float64") for a, c in (("x", "X"), ("y", "Y"), ("z", "Z"))}
    for a in ("x", "y", "z"):
        put(f"position_{a}", pos[a])
    duck = _opt(raw, "duck_amount", 0.0, missing).astype("float64")
    put("eye_x", pos["x"])
    put("eye_y", pos["y"])
    put("eye_z", pos["z"] + EYE_HEIGHT_STANDING - (EYE_HEIGHT_STANDING - EYE_HEIGHT_CROUCH) * duck)
    put("view_yaw", normalize_yaw(raw["yaw"]))
    put("view_pitch", raw["pitch"].astype("float64"))
    # velocity per player: rows by (steam_id, tick)
    by_player = np.lexsort((tick, sid))
    vel = _velocity({a: pos.pop(a).to_numpy(dtype=float)[by_player] for a in ("x", "y", "z")},
                    tick[by_player], sid[by_player], tickrate)
    for c, v in vel.items():
        unsorted = np.empty_like(v)
        unsorted[by_player] = v
        put(c, unsorted)
    del by_player, vel, unsorted
    put("alive", _opt(raw, "is_alive", False, missing).astype(bool))
    put("health", _opt(raw, "health", 0, missing).astype("int16"))
    weapons = _opt(raw, "active_weapon_name", None, missing)
    uniq = {w: canonical_weapon(w) for w in weapons.dropna().unique()}
    weapon = weapons.map(uniq)
    put("weapon", weapon)
    classes = {w: weapon_class(w) for w in set(uniq.values()) if w}
    put("weapon_class", weapon.map(classes).fillna("unknown"))
    del weapons, weapon
    put("ammo", _opt(raw, "m_iClip1", np.nan, missing).astype("float64"))
    put("is_scoped", _opt(raw, "is_scoped", False, missing).astype(bool))
    put("is_crouching", duck > 0.5)
    put("duck_amount", duck)
    del duck
    put("is_walking", _opt(raw, "is_walking", False, missing).astype(bool))
    put("is_airborne", _opt(raw, "is_airborne", False, missing).astype(bool))
    put("flash_duration_prop", _opt(raw, "flash_duration", 0.0, missing).astype("float64"))
    put("flash_alpha", _opt(raw, "flash_max_alpha", 0.0, missing).astype("float64"))
    put("shots_fired", _opt(raw, "shots_fired", 0, missing).astype("int32"))
    if "aim_punch_pitch" in raw and "aim_punch_yaw" in raw:
        put("aim_punch_pitch", raw["aim_punch_pitch"].to_numpy(dtype=np.float64))
        put("aim_punch_yaw", raw["aim_punch_yaw"].to_numpy(dtype=np.float64))
    else:
        pitch, yaw = punch_angles(raw, missing)
        put("aim_punch_pitch", pitch)
        put("aim_punch_yaw", yaw)
        del pitch, yaw
    put("attack", _opt(raw, "FIRE", False, missing).astype(bool))
    put("buttons", _opt(raw, "buttons", 0, missing).astype("int64"))
    put("spotted", _opt(raw, "spotted", False, missing).astype(bool))
    put("spotted_by", _spotted_lists(_opt(raw, "approximate_spotted_by", None, missing)))
    assert list(out.columns) == NORMALIZED_COLUMNS
    out.attrs["missing_props"] = sorted(set(missing))
    return out


def punch_angles(raw: pd.DataFrame, missing: list[str]) -> tuple[np.ndarray, np.ndarray]:
    """(pitch, yaw) aim punch from whichever prop the demo networks it in."""
    if "aim_punch_angle" not in raw and AIM_PUNCH_SERVICES_PROP in raw:
        punch = raw[AIM_PUNCH_SERVICES_PROP]
    else:
        punch = _opt(raw, "aim_punch_angle", None, missing)
    return _punch(punch, 0), _punch(punch, 1)


def _take(col: pd.Series | np.ndarray, order: np.ndarray):
    if isinstance(col, pd.Series):
        return col.take(order).reset_index(drop=True)
    return col[order]
