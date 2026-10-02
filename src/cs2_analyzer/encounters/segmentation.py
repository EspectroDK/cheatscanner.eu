"""Encounter segmentation.

Instead of scoring ticks independently, analysis happens in windows around
meaningful moments between an observer and one enemy:

* ``FIRST_SEEN``  enemy enters observer's sight (LOS + FOV) after a gap
* ``FIRST_SHOT``  observer fires with this enemy nearest to the crosshair
* ``DAMAGE``      observer damages the enemy

Windows span ``[anchor - pre_ms, anchor + post_ms]`` (configurable, default
-2000/+1000 ms), are clipped to the live round and to the observer being
alive, and are merged when they overlap for the same pair.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from cs2_analyzer.features.pair import pair_series
from cs2_analyzer.geometry.visibility import VisibilityEngine
from cs2_analyzer.knowledge.model import KnowledgeModel
from cs2_analyzer.world import World


@dataclass
class Encounter:
    id: str
    observer: int
    target: int
    round_number: int
    t_start: int
    t_end: int
    t_anchor: int
    anchor_type: str
    t_first_seen: int | None = None
    t_first_shot: int | None = None
    t_first_damage: int | None = None
    t_kill: int | None = None
    anchors: list[tuple[str, int]] = field(default_factory=list)

    def to_dict(self, world: World) -> dict:
        tk = lambda t: None if t is None else world.tick(t)  # noqa: E731
        return {
            "encounter_id": self.id,
            "observer_steam_id": int(world.steam_ids[self.observer]),
            "target_steam_id": int(world.steam_ids[self.target]),
            "round_number": self.round_number,
            "tick_start": tk(self.t_start),
            "tick_end": tk(self.t_end),
            "tick_anchor": tk(self.t_anchor),
            "anchor_type": self.anchor_type,
            "tick_first_seen": tk(self.t_first_seen),
            "tick_first_shot": tk(self.t_first_shot),
            "tick_first_damage": tk(self.t_first_damage),
            "tick_kill": tk(self.t_kill),
        }


def _onsets(mask: np.ndarray, min_gap: int) -> np.ndarray:
    """Indices where mask turns True after being False for >= min_gap samples."""
    on = np.nonzero(mask & ~np.r_[False, mask[:-1]])[0]
    if on.size == 0:
        return on
    true_idx = np.nonzero(mask)[0]
    keep = []
    for t in on:
        k = np.searchsorted(true_idx, t) - 1
        if k < 0 or t - true_idx[k] > min_gap:
            keep.append(t)
    return np.array(keep, dtype=int)


def segment_encounters(world: World, vis: VisibilityEngine, knowledge: KnowledgeModel,
                       events: dict[str, pd.DataFrame], cfg: dict) -> list[Encounter]:
    pre = world.ticks_for_ms(cfg.get("pre_ms", 2000))
    post = world.ticks_for_ms(cfg.get("post_ms", 1000))
    gap = world.ticks_for_ms(cfg.get("reappear_gap_ms", 1000))
    shot_max_err = float(cfg.get("shot_target_max_error_deg", 15.0))
    seen = vis.seen()

    hurts = events.get("hurts", pd.DataFrame())
    deaths = events.get("deaths", pd.DataFrame())
    anchors: dict[tuple[int, int], list[tuple[str, int]]] = {}

    def add(o, e, kind, t):
        anchors.setdefault((o, e), []).append((kind, int(t)))

    for o in range(world.P):
        for e in range(world.P):
            if o != e and seen[o, e].any():
                for t in _onsets(seen[o, e], gap):
                    add(o, e, "FIRST_SEEN", t)
        # shots: attribute to the enemy nearest the crosshair
        for t in world.shot_ticks.get(o, []):
            if not world.live[t] or not world.alive[o, t]:
                continue
            best, best_err = None, shot_max_err
            for e in world.enemies(o, t):
                if not world.alive[e, t]:
                    continue
                ps = pair_series(world, o, e, t, t)
                err = float(ps.err[0])
                if np.isfinite(err) and err < best_err:
                    best, best_err = e, err
            if best is not None:
                add(o, best, "FIRST_SHOT", t)
    for df, kind in ((hurts, "DAMAGE"), (deaths, "KILL")):
        if df is None or not len(df):
            continue
        for r in df.itertuples():
            a = world.index_of.get(int(r.attacker_steam_id))
            v = world.index_of.get(int(r.victim_steam_id))
            t = int(r.tick) - world.tick0
            if a is None or v is None or a == v or not (0 <= t < world.T):
                continue
            if world.team[a, t] == world.team[v, t]:
                continue
            add(a, v, kind, t)

    encounters: list[Encounter] = []
    for (o, e), lst in anchors.items():
        lst.sort(key=lambda x: x[1])
        windows: list[list] = []
        for kind, t in lst:
            if kind == "KILL" and not windows:
                continue
            a, b = t - pre, t + post
            if windows and a <= windows[-1][1]:
                windows[-1][1] = max(windows[-1][1], b)
                windows[-1][2].append((kind, t))
            else:
                windows.append([a, b, [(kind, t)]])
        for a, b, items in windows:
            first = {k: min((t for kk, t in items if kk == k), default=None) for k in ("FIRST_SEEN", "FIRST_SHOT", "DAMAGE", "KILL")}
            anchor_kind, anchor_t = min(((k, t) for k, t in items if k != "KILL"), key=lambda x: x[1], default=items[0])
            rnd = int(world.round_of[anchor_t])
            live_idx = np.nonzero((world.round_of == rnd) & world.live)[0]
            if live_idx.size == 0:
                continue
            a = max(a, int(live_idx[0]))
            b = min(b, int(live_idx[-1]))
            # clip to observer alive around the anchor
            alive = world.alive[o]
            while a < anchor_t and not alive[a]:
                a += 1
            end_alive = anchor_t
            while end_alive < b and alive[end_alive + 1]:
                end_alive += 1
            b = end_alive
            if b - a < 4:
                continue
            enc = Encounter(
                id=f"{world.steam_ids[o]}-{world.steam_ids[e]}-{world.tick(anchor_t)}",
                observer=o,
                target=e,
                round_number=rnd,
                t_start=a,
                t_end=b,
                t_anchor=anchor_t,
                anchor_type=anchor_kind,
                t_first_seen=first["FIRST_SEEN"],
                t_first_shot=first["FIRST_SHOT"],
                t_first_damage=first["DAMAGE"],
                t_kill=first["KILL"],
                anchors=items,
            )
            encounters.append(enc)
    encounters.sort(key=lambda x: (x.t_anchor, x.observer, x.target))
    return encounters


CHANNELS = [
    "aim_error", "aim_error_h", "aim_error_v", "yaw_delta", "pitch_delta", "angular_velocity",
    "angular_acceleration", "angular_jerk", "target_yaw", "target_pitch", "target_bearing_delta_target",
    "target_bearing_delta_observer", "target_speed", "target_distance", "visibility", "knowledge",
    "info_confidence", "player_speed", "player_accel", "weapon", "shot", "shots_fired", "recoil_pitch",
    "recoil_yaw", "flash_remaining", "scoped", "crouching",
]


def encounter_frame(world: World, vis: VisibilityEngine, knowledge: KnowledgeModel, enc: Encounter) -> pd.DataFrame:
    """Per-tick channel table for an encounter window (ML-ready)."""
    from cs2_analyzer.geometry.angles import derivatives

    ps = pair_series(world, enc.observer, enc.target, enc.t_start, enc.t_end)
    idx = ps.t
    o, e = enc.observer, enc.target
    speed = np.nan_to_num(ps.aim_speed)
    _, acc, jerk = derivatives(np.nancumsum(speed) * world.dt, world.dt, smoothing=3)
    pspeed = world.speed2d[o, idx].astype(np.float64)
    df = pd.DataFrame(
        {
            "encounter_id": enc.id,
            "tick": idx + world.tick0,
            "rel_ms": (idx - enc.t_anchor) * 1000.0 / world.tickrate,
            "aim_error": ps.err,
            "aim_error_h": ps.err_h,
            "aim_error_v": ps.err_v,
            "yaw_delta": ps.d_aim_yaw,
            "pitch_delta": ps.d_aim_pitch,
            "angular_velocity": ps.aim_speed,
            "angular_acceleration": acc,
            "angular_jerk": jerk,
            "target_yaw": ps.target_yaw,
            "target_pitch": ps.target_pitch,
            "target_bearing_delta_target": ps.d_bearing_target_yaw,
            "target_bearing_delta_observer": ps.d_bearing_observer_yaw,
            "target_speed": ps.target_speed,
            "target_distance": ps.distance,
            "visibility": vis.los[o, e, idx],
            "knowledge": knowledge.level[o, e, idx],
            "info_confidence": knowledge.info_confidence[o, e, idx].astype(np.float32),
            "player_speed": pspeed,
            "player_accel": np.gradient(np.nan_to_num(pspeed), world.dt) if len(idx) > 1 else 0.0,
            "weapon": world.weapon[o, idx],
            "shot": world.shot_mask[o, idx],
            "shots_fired": world.shots_fired[o, idx],
            "recoil_pitch": world.punch_pitch[o, idx],
            "recoil_yaw": world.punch_yaw[o, idx],
            "flash_remaining": world.flash_remaining[o, idx],
            "scoped": world.scoped[o, idx],
            "crouching": world.duck[o, idx] > 0.5,
        }
    )
    return df
