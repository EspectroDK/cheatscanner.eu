"""Dense world-state reconstruction.

Converts the long normalized tick table into dense ``[player, tick]`` arrays
so geometry, knowledge and detectors can be vectorized. Index ``t`` maps to
demo tick ``tick0 + t``. Missing samples are NaN / False.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cs2_analyzer.features.weapons import is_gun
from cs2_analyzer.geometry.angles import view_vector
from cs2_analyzer.parser.base import ParsedDemo


@dataclass
class World:
    tick0: int
    tickrate: float
    steam_ids: np.ndarray  # [P] int64
    names: list[str]
    team: np.ndarray  # [P, T] int8 (2=T, 3=CT, 0=unknown)
    alive: np.ndarray  # [P, T] bool
    pos: np.ndarray  # [P, T, 3] float32 origin (feet)
    eye: np.ndarray  # [P, T, 3] float32
    pitch: np.ndarray  # [P, T] float64
    yaw: np.ndarray  # [P, T] float64
    vel: np.ndarray  # [P, T, 3] float32
    speed2d: np.ndarray  # [P, T]
    duck: np.ndarray  # [P, T]
    walking: np.ndarray  # [P, T] bool
    airborne: np.ndarray  # [P, T] bool
    scoped: np.ndarray  # [P, T] bool
    health: np.ndarray  # [P, T]
    weapon: np.ndarray  # [P, T] object (canonical id or None)
    weapon_class: np.ndarray  # [P, T] object
    shots_fired: np.ndarray  # [P, T]
    punch_pitch: np.ndarray  # [P, T]
    punch_yaw: np.ndarray  # [P, T]
    spotted: np.ndarray  # [P, T] bool: on enemy radar
    spotted_by: np.ndarray  # [P(target), P(observer), T] bool, game's approximate_spotted_by
    round_of: np.ndarray  # [T] int
    live: np.ndarray  # [T] bool
    flash_remaining: np.ndarray  # [P, T] seconds of blindness remaining (from player_blind)
    shot_mask: np.ndarray  # [P, T] bool: weapon_fire with a gun at this tick
    shot_ticks: dict[int, np.ndarray] = field(default_factory=dict)  # player idx -> tick indices
    index_of: dict[int, int] = field(default_factory=dict)

    @property
    def T(self) -> int:
        return self.alive.shape[1]

    @property
    def P(self) -> int:
        return self.alive.shape[0]

    @property
    def dt(self) -> float:
        return 1.0 / self.tickrate

    def t(self, tick: int) -> int:
        return int(tick) - self.tick0

    def tick(self, t: int) -> int:
        return int(t) + self.tick0

    def ticks_for_ms(self, ms: float) -> int:
        return int(round(ms / 1000.0 * self.tickrate))

    def ms_for_ticks(self, n: float) -> float:
        return float(n) * 1000.0 / self.tickrate

    def view_dir(self, p: int, sl=slice(None)) -> np.ndarray:
        return view_vector(self.pitch[p, sl], self.yaw[p, sl])

    def enemies(self, p: int, t: int) -> list[int]:
        tm = self.team[p, t]
        return [q for q in range(self.P) if q != p and self.team[q, t] in (2, 3) and self.team[q, t] != tm]

    def is_enemy(self, p: int, q: int, t: np.ndarray | slice = slice(None)) -> np.ndarray:
        a, b = self.team[p, t], self.team[q, t]
        return (a != b) & np.isin(a, (2, 3)) & np.isin(b, (2, 3))

    def body_points(self, q: int, sl=slice(None)) -> dict[str, np.ndarray]:
        """Approximate hit-sample points of player ``q`` (no hitbox data in demos).

        Head ~ eye position; chest/pelvis/knee are fractions of eye height.
        This is an approximation (documented in docs/geometry.md).
        """
        base = self.pos[q, sl].astype(np.float64)
        eye = self.eye[q, sl].astype(np.float64)
        h = eye[..., 2] - base[..., 2]
        pts = {"head": eye.copy()}
        for name, frac in (("chest", 0.72), ("pelvis", 0.5), ("knee", 0.25)):
            p = base.copy()
            p[..., 2] = base[..., 2] + frac * h
            pts[name] = p
        return pts


def _dense(df: pd.DataFrame, col: str, pidx: np.ndarray, tidx: np.ndarray, shape, fill, dtype):
    out = np.full(shape, fill, dtype=dtype)
    out[pidx, tidx] = df[col].to_numpy()
    return out


def _dense_labels(df: pd.DataFrame, col: str, pidx: np.ndarray, tidx: np.ndarray, shape, fill):
    """``_dense`` for a text column, with one shared object per distinct value.

    ``to_numpy()`` on a text column makes a new string object for every row, which for a
    match is millions of copies of a few weapon names (hundreds of MB).
    """
    s = df[col]
    codes, uniques = pd.factorize(s)
    values = np.empty(len(uniques) + 1, dtype=object)
    values[:-1] = list(uniques)
    vals = values[codes]  # missing values (code -1) are filled in below
    na = np.flatnonzero(codes == -1)
    if len(na):
        vals[na] = s.iloc[na].to_numpy()
    out = np.full(shape, fill, dtype=object)
    out[pidx, tidx] = vals
    return out


def build_world(demo: ParsedDemo) -> World:
    ticks = demo.ticks
    players = demo.players
    steam_ids = players["steam_id"].to_numpy(dtype=np.int64)
    index_of = {int(s): i for i, s in enumerate(steam_ids)}
    keep = ticks["steam_id"].isin(index_of)
    if not keep.all():  # filtering copies the whole table, so only when there is something to drop
        ticks = ticks[keep]
    tick0 = int(ticks["tick"].min())
    T = int(ticks["tick"].max()) - tick0 + 1
    P = len(steam_ids)
    pidx = ticks["steam_id"].map(index_of).to_numpy()
    tidx = (ticks["tick"] - tick0).to_numpy()
    shape = (P, T)

    def f(col, fill=np.nan, dtype=np.float64):
        return _dense(ticks, col, pidx, tidx, shape, fill, dtype)

    pos = np.stack([f("position_x"), f("position_y"), f("position_z")], axis=-1).astype(np.float32)
    eye = np.stack([f("eye_x"), f("eye_y"), f("eye_z")], axis=-1).astype(np.float32)
    vel = np.stack([f("velocity_x"), f("velocity_y"), f("velocity_z")], axis=-1).astype(np.float32)

    # spotted_by -> [target, observer, T]
    spotted_by = np.zeros((P, P, T), dtype=bool)
    sb = ticks[["steam_id", "tick", "spotted_by"]]
    sb = sb[sb["spotted_by"].map(len) > 0]
    for sid, tk, lst in sb.itertuples(index=False):
        for o in lst:
            oi = index_of.get(int(o))
            if oi is not None:
                spotted_by[index_of[int(sid)], oi, tk - tick0] = True

    round_of = np.zeros(T, dtype=np.int16)
    live = np.zeros(T, dtype=bool)
    per_tick = ticks.drop_duplicates("tick")
    round_of[(per_tick["tick"] - tick0).to_numpy()] = per_tick["round"].to_numpy()
    live[(per_tick["tick"] - tick0).to_numpy()] = per_tick["round_live"].to_numpy()

    tickrate = demo.meta.tickrate
    flash_remaining = np.zeros(shape, dtype=np.float32)
    blinds = demo.event("blinds")
    for r in blinds.itertuples() if len(blinds) else []:
        vi = index_of.get(int(r.victim_steam_id))
        if vi is None or not np.isfinite(r.blind_duration):
            continue
        t0 = int(r.tick) - tick0
        n = int(np.ceil(r.blind_duration * tickrate))
        t1 = min(T, t0 + n + 1)
        if t1 <= max(t0, 0):
            continue
        rem = r.blind_duration - (np.arange(max(t0, 0), t1) - t0) / tickrate
        flash_remaining[vi, max(t0, 0):t1] = np.maximum(flash_remaining[vi, max(t0, 0):t1], rem)

    shot_mask = np.zeros(shape, dtype=bool)
    shot_ticks: dict[int, np.ndarray] = {}
    shots = demo.event("shots")
    if len(shots):
        s = shots[shots["weapon"].map(is_gun) & shots["steam_id"].isin(index_of)]
        si = s["steam_id"].map(index_of).to_numpy()
        ti = (s["tick"] - tick0).to_numpy()
        ok = (ti >= 0) & (ti < T)
        shot_mask[si[ok], ti[ok]] = True
    for p in range(P):
        shot_ticks[p] = np.nonzero(shot_mask[p])[0]

    return World(
        tick0=tick0,
        tickrate=tickrate,
        steam_ids=steam_ids,
        names=players["name"].astype(str).tolist(),
        team=f("team", 0, np.int8),
        alive=f("alive", False, bool),
        pos=pos,
        eye=eye,
        pitch=f("view_pitch"),
        yaw=f("view_yaw"),
        vel=vel,
        speed2d=f("speed_2d"),
        duck=f("duck_amount"),
        walking=f("is_walking", False, bool),
        airborne=f("is_airborne", False, bool),
        scoped=f("is_scoped", False, bool),
        health=f("health", 0, np.int16),
        weapon=_dense_labels(ticks, "weapon", pidx, tidx, shape, None),
        weapon_class=_dense_labels(ticks, "weapon_class", pidx, tidx, shape, "unknown"),
        shots_fired=f("shots_fired", 0, np.int32),
        punch_pitch=f("aim_punch_pitch"),
        punch_yaw=f("aim_punch_yaw"),
        spotted=f("spotted", False, bool),
        spotted_by=spotted_by,
        round_of=round_of,
        live=live,
        flash_remaining=flash_remaining,
        shot_mask=shot_mask,
        shot_ticks=shot_ticks,
        index_of=index_of,
    )
