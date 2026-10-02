"""Synthetic scenario builder for tests and detector development.

Builds a :class:`ParsedDemo` directly (no .dem file) so detector behavior can
be checked on controlled situations such as "hidden enemy moving, crosshair
following it" versus "observer strafing, enemy stationary".
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cs2_analyzer.geometry.angles import bearing
from cs2_analyzer.geometry.mesh import MapGeometry, box_triangles
from cs2_analyzer.parser.base import MatchMeta, ParsedDemo
from cs2_analyzer.parser.normalize import (
    EYE_HEIGHT_STANDING,
    NORMALIZED_COLUMNS,
    assign_rounds,
    derive_velocity,
)

TICKRATE = 64.0


@dataclass
class SynthPlayer:
    steam_id: int
    team: int
    name: str
    pos: np.ndarray  # [T, 3] origin
    pitch: np.ndarray  # [T]
    yaw: np.ndarray  # [T]
    walking: bool = True
    alive: np.ndarray | None = None
    weapon: str = "ak47"
    punch: np.ndarray | None = None  # [T, 2]


@dataclass
class Scenario:
    seconds: float = 30.0
    tickrate: float = TICKRATE
    freeze_end_s: float = 0.5
    players: list[SynthPlayer] = field(default_factory=list)
    events: dict[str, list[dict]] = field(default_factory=dict)
    walls: list[tuple[tuple, tuple]] = field(default_factory=list)

    @property
    def T(self) -> int:
        return int(self.seconds * self.tickrate)

    def time(self) -> np.ndarray:
        return np.arange(self.T) / self.tickrate

    def s(self, seconds: float) -> int:
        return int(round(seconds * self.tickrate))

    def add(self, p: SynthPlayer) -> SynthPlayer:
        self.players.append(p)
        return p

    def add_event(self, name: str, **row):
        self.events.setdefault(name, []).append(row)

    def eye(self, p: SynthPlayer) -> np.ndarray:
        e = p.pos.astype(np.float64).copy()
        e[:, 2] += EYE_HEIGHT_STANDING
        return e

    def geometry(self) -> MapGeometry:
        tris = np.concatenate([box_triangles(a, b) for a, b in self.walls]) if self.walls else np.zeros((0, 3, 3), np.float32)
        return MapGeometry("de_synthetic", tris, source="synthetic", backend="numpy")

    def to_demo(self, match_id: str = "synthetic") -> ParsedDemo:
        T = self.T
        tick0 = 1
        rows = []
        for p in self.players:
            alive = p.alive if p.alive is not None else np.ones(T, dtype=bool)
            punch = p.punch if p.punch is not None else np.zeros((T, 2))
            df = pd.DataFrame(
                {
                    "tick": np.arange(T) + tick0,
                    "game_time": np.arange(T) / self.tickrate,
                    "steam_id": np.int64(p.steam_id),
                    "team": np.int16(p.team),
                    "position_x": p.pos[:, 0],
                    "position_y": p.pos[:, 1],
                    "position_z": p.pos[:, 2],
                    "view_yaw": (p.yaw + 180) % 360 - 180,
                    "view_pitch": p.pitch,
                    "alive": alive,
                    "health": np.where(alive, 100, 0).astype(np.int16),
                    "weapon": p.weapon,
                    "weapon_class": "rifle",
                    "ammo": 30.0,
                    "is_scoped": False,
                    "duck_amount": 0.0,
                    "is_walking": p.walking,
                    "is_airborne": False,
                    "flash_duration_prop": 0.0,
                    "flash_alpha": 0.0,
                    "shots_fired": 0,
                    "aim_punch_pitch": punch[:, 0],
                    "aim_punch_yaw": punch[:, 1],
                    "attack": False,
                    "buttons": 0,
                    "spotted": False,
                    "spotted_by": [[] for _ in range(T)],
                }
            )
            rows.append(df)
        ticks = pd.concat(rows, ignore_index=True)
        ticks["is_crouching"] = False
        ticks["eye_x"] = ticks["position_x"]
        ticks["eye_y"] = ticks["position_y"]
        ticks["eye_z"] = ticks["position_z"] + EYE_HEIGHT_STANDING
        rounds = pd.DataFrame(
            [{"round_number": 1, "start_tick": tick0, "freeze_end_tick": tick0 + self.s(self.freeze_end_s),
              "end_tick": tick0 + T - 1, "winner": None, "reason": None, "live": True}]
        )
        r, live = assign_rounds(ticks["tick"].to_numpy(), rounds)
        ticks["round"] = r
        ticks["round_live"] = live
        ticks = derive_velocity(ticks, self.tickrate).sort_values(["tick", "steam_id"]).reset_index(drop=True)
        ticks = ticks[NORMALIZED_COLUMNS]
        events = {}
        for name, lst in self.events.items():
            events[name] = pd.DataFrame(lst)
        for name in ("shots", "hurts", "deaths", "smokes", "footsteps", "jumps", "reloads", "bomb", "blinds", "he_grenades"):
            events.setdefault(name, pd.DataFrame())
        players = pd.DataFrame([{"steam_id": p.steam_id, "name": p.name, "start_team": p.team} for p in self.players])
        meta = MatchMeta(match_id=match_id, source="synthetic", map_name="de_synthetic", mode="synthetic", mode_source=None,
                         played_at=None, server_name=None, patch_version=None, tickrate=self.tickrate,
                         demo_sha256=match_id.ljust(64, "0")[:64], first_tick=tick0, last_tick=tick0 + T - 1,
                         parser_name="synthetic", parser_version="0", extra={})
        return ParsedDemo(meta=meta, players=players, rounds=rounds, ticks=ticks, events=events)


def aim_at(observer_eye: np.ndarray, target: np.ndarray, lag_ticks: int = 0, noise_deg: float = 0.0,
           seed: int = 0) -> tuple[np.ndarray, np.ndarray]:
    """(pitch, yaw) that point at ``target`` with a reaction lag and optional noise."""
    yaw, pitch = bearing(observer_eye, target)
    if lag_ticks:
        yaw = np.r_[np.repeat(yaw[:1], lag_ticks), yaw[:-lag_ticks]]
        pitch = np.r_[np.repeat(pitch[:1], lag_ticks), pitch[:-lag_ticks]]
    if noise_deg:
        rng = np.random.default_rng(seed)
        k = np.ones(9) / 9
        yaw = yaw + np.convolve(rng.normal(0, noise_deg, len(yaw)), k, mode="same")
        pitch = pitch + np.convolve(rng.normal(0, noise_deg, len(pitch)), k, mode="same")
    return pitch, yaw


def wall_scenario(seconds: float = 30.0) -> Scenario:
    """Observer at the origin, a tall wall at x=400..420 hiding an enemy at x~800."""
    sc = Scenario(seconds=seconds)
    sc.walls.append(((400, -2000, -100), (420, 2000, 400)))
    return sc


def static(T: int, xyz) -> np.ndarray:
    return np.tile(np.asarray(xyz, dtype=np.float64), (T, 1))


def weave(T: int, tickrate: float, x: float, y0: float, amp: float, period_s: float, start: int = 0) -> np.ndarray:
    """Position moving back and forth along y (walking pace) from tick ``start``."""
    t = np.maximum(np.arange(T) - start, 0) / tickrate
    y = y0 + amp * np.sin(2 * np.pi * t / period_s)
    return np.stack([np.full(T, x), y, np.zeros(T)], axis=1)
