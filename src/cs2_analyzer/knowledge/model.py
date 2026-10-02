"""Legitimate-information (knowledge) model.

For every observer ``o``, enemy ``e`` and tick we estimate whether ``o`` could
plausibly know where ``e`` is, from sources a legitimate player has:

* own sight (line of sight AND inside the field of view), now or recently;
* teammates' sight and the in-game radar (the game's own ``spotted`` flag);
* voice callouts (modelled as teammate sight persisting for a few seconds);
* damage exchanged between the two;
* sound: gunfire, footsteps (recorded events + derived from movement speed),
  jumps/landings, reloads, bomb plant/defuse;
* objective/predictability context: round start (spawns), planted bomb site.

Levels: ``KNOWN`` > ``LIKELY_KNOWN`` > ``POSSIBLY_KNOWN`` > ``UNKNOWN``.

Purpose: this model exists mainly to prevent false positives. Every source is
modelled generously (larger radii, longer memory) because *over*-estimating
legitimate knowledge only costs sensitivity, while under-estimating it creates
false hidden-information evidence. Only UNKNOWN samples, weighted by
``info_confidence``, may feed hidden-information detectors.

All radii/durations are UNCALIBRATED (config ``knowledge``).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

import numpy as np
import pandas as pd

from cs2_analyzer.features.weapons import canonical_weapon, is_gun
from cs2_analyzer.geometry.visibility import LOS, VisibilityEngine
from cs2_analyzer.world import World


class Knowledge(IntEnum):
    NOT_APPLICABLE = 0
    KNOWN = 1
    LIKELY_KNOWN = 2
    POSSIBLY_KNOWN = 3
    UNKNOWN = 4


SILENCED = {"usp_silencer", "m4a1_silencer"}
NEVER = np.int32(-(1 << 30))


@dataclass
class KnowledgeParams:
    known_recent_ms: float = 600.0
    likely_recent_seen_ms: float = 3500.0
    possibly_recent_seen_ms: float = 9000.0
    team_now_ms: float = 1500.0
    team_callout_ms: float = 7000.0
    damage_likely_ms: float = 3500.0
    damage_possibly_ms: float = 9000.0
    sound_likely_ms: float = 2000.0
    sound_possibly_ms: float = 5000.0
    gunfire_radius: float = 2600.0
    silenced_gunfire_radius: float = 900.0
    footstep_radius: float = 1250.0
    footstep_min_speed: float = 135.0
    jump_radius: float = 1250.0
    reload_radius: float = 800.0
    round_start_possibly_s: float = 15.0
    bomb_site_possibly_radius: float = 900.0
    close_range_units: float = 350.0
    unknown_base_confidence: float = 0.9
    unknown_full_confidence_after_ms: float = 4000.0
    map_geometry_confidence: float = 0.95

    @classmethod
    def from_config(cls, cfg: dict) -> "KnowledgeParams":
        return cls(**{k: float(v) for k, v in cfg.items() if k in cls.__dataclass_fields__})


def _last_true_index(mask: np.ndarray, reset: np.ndarray | None = None) -> np.ndarray:
    """For each t, the last index <= t where ``mask`` was True (NEVER if none).

    ``reset`` marks indices where memory is cleared (new round).
    """
    n = mask.shape[-1]
    idx = np.where(mask, np.arange(n, dtype=np.int32), NEVER)
    if reset is None:
        return np.maximum.accumulate(idx, axis=-1)
    starts = sorted(set([0] + np.nonzero(reset)[0].tolist()))
    out = np.empty_like(idx)
    for a, b in zip(starts, starts[1:] + [n]):
        out[..., a:b] = np.maximum.accumulate(idx[..., a:b], axis=-1)
    return out


class KnowledgeModel:
    def __init__(self, world: World, vis: VisibilityEngine, events: dict[str, pd.DataFrame],
                 params: KnowledgeParams | None = None, bomb_sites: dict | None = None):
        self.w = world
        self.vis = vis
        self.ev = events
        self.p = params or KnowledgeParams()
        P, T = world.P, world.T
        self.level = np.zeros((P, P, T), dtype=np.uint8)
        self.info_confidence = np.zeros((P, P, T), dtype=np.float16)
        self.last_seen = np.full((P, P, T), NEVER, dtype=np.int32)  # own sight
        self.last_info = np.full((P, P, T), NEVER, dtype=np.int32)  # any KNOWN/LIKELY source
        self.last_sound = np.full((P, P, T), NEVER, dtype=np.int32)
        self.team_seen_now = np.zeros((P, P, T), dtype=bool)
        vis.knowledge = self

    # ---------------------------------------------------------------- sources

    def _round_reset(self) -> np.ndarray:
        r = self.w.round_of
        reset = np.zeros(self.w.T, dtype=bool)
        reset[0] = True
        reset[1:] = r[1:] != r[:-1]
        return reset

    def _noise_radius(self) -> np.ndarray:
        """[P, T] radius within which player's noise at t is audible (0 = silent)."""
        w, p = self.w, self.p
        rad = np.zeros((w.P, w.T), dtype=np.float32)
        # derived footsteps: running on ground, not shift-walking, not crouched
        foot = (w.speed2d >= p.footstep_min_speed) & ~w.walking & ~w.airborne & (w.duck < 0.5) & w.alive
        rad[foot] = np.maximum(rad[foot], p.footstep_radius)
        # landing from airborne
        land = np.zeros_like(foot)
        land[:, 1:] = w.airborne[:, :-1] & ~w.airborne[:, 1:] & w.alive[:, 1:]
        rad[land] = np.maximum(rad[land], p.jump_radius)

        def mark(df: pd.DataFrame, radius_fn, sid_col="steam_id"):
            if df is None or not len(df):
                return
            for r in df.itertuples():
                pi = w.index_of.get(int(getattr(r, sid_col)))
                t = int(r.tick) - w.tick0
                if pi is None or not (0 <= t < w.T):
                    continue
                rad[pi, t] = max(rad[pi, t], radius_fn(r))

        shots = self.ev.get("shots")
        if shots is not None and len(shots):
            s = shots[shots["weapon"].map(is_gun)]
            mark(s, lambda r: p.silenced_gunfire_radius if canonical_weapon(r.weapon) in SILENCED else p.gunfire_radius)
        mark(self.ev.get("footsteps"), lambda r: p.footstep_radius)
        mark(self.ev.get("jumps"), lambda r: p.jump_radius)
        mark(self.ev.get("reloads"), lambda r: p.reload_radius)
        return rad

    def _bomb_windows(self) -> np.ndarray:
        """[P, T] player is planting/defusing (audible + objective-known to all enemies)."""
        w = self.w
        out = np.zeros((w.P, w.T), dtype=bool)
        bomb = self.ev.get("bomb")
        if bomb is None or not len(bomb):
            return out
        b = bomb.sort_values("tick")
        for r in b.itertuples():
            if r.action not in ("beginplant", "begindefuse"):
                continue
            pi = w.index_of.get(int(r.steam_id))
            if pi is None:
                continue
            t0 = int(r.tick) - w.tick0
            t1 = t0 + int(11 * w.tickrate)  # max defuse 10s; plant 3.2s; ended early by later events
            later = b[(b["tick"] > r.tick) & (b["action"].isin(["planted", "defused", "exploded", "beginplant", "begindefuse"]))]
            if len(later):
                t1 = min(t1, int(later["tick"].iloc[0]) - w.tick0 + int(1 * w.tickrate))
            out[pi, max(0, t0):max(0, min(w.T, t1))] = True
        return out

    def _planted_site_center(self) -> list[tuple[int, int, np.ndarray]]:
        """[(t_from, t_to, bomb_position)] while a bomb is planted."""
        w = self.w
        bomb = self.ev.get("bomb")
        out = []
        if bomb is None or not len(bomb):
            return out
        for r in bomb[bomb["action"] == "planted"].itertuples():
            pi = w.index_of.get(int(r.steam_id))
            t0 = int(r.tick) - w.tick0
            if pi is None or not (0 <= t0 < w.T):
                continue
            rnd = w.round_of[t0]
            t1 = t0
            while t1 < w.T and w.round_of[t1] == rnd:
                t1 += 1
            out.append((t0, t1, w.pos[pi, t0].astype(np.float64)))
        return out

    # ---------------------------------------------------------------- compute

    def compute(self) -> "KnowledgeModel":
        w, p, vis = self.w, self.p, self.vis
        tr = w.tickrate
        ms = lambda n: int(round(n / 1000.0 * tr))  # noqa: E731
        reset = self._round_reset()
        seen = vis.seen()  # [o, e, T]
        los = vis.los
        radius = self._noise_radius()
        bombing = self._bomb_windows()
        planted = self._planted_site_center()
        t_idx = np.arange(w.T, dtype=np.int32)

        # round-start predictability window
        round_start = np.zeros(w.T, dtype=bool)
        fe = np.nonzero(w.live[1:] & ~w.live[:-1])[0] + 1
        if w.live[0]:
            fe = np.r_[0, fe]
        for s in fe:
            round_start[s : s + ms(p.round_start_possibly_s * 1000)] = True

        damage = self._damage_pairs()

        for o in range(w.P):
            teammates = [q for q in range(w.P) if q != o]
            for e in range(w.P):
                if o == e:
                    continue
                valid = w.live & w.alive[o] & w.alive[e] & w.is_enemy(o, e)
                if not valid.any():
                    continue
                # own sight memory
                ls = _last_true_index(seen[o, e], reset)
                # team sight / radar: teammates of o at that tick who see e, or game's spotted flag
                same_team = np.zeros((len(teammates), w.T), dtype=bool)
                for k, q in enumerate(teammates):
                    same_team[k] = (w.team[q] == w.team[o]) & w.alive[q]
                team_now = (seen[teammates, e] & same_team).any(axis=0) | (w.spotted[e] & valid)
                lt = _last_true_index(team_now, reset)
                # sound: e's noise within radius of o
                dist = np.linalg.norm(w.pos[o].astype(np.float64) - w.pos[e].astype(np.float64), axis=-1)
                audible = (radius[e] > 0) & (np.nan_to_num(dist, nan=1e9) <= radius[e])
                lsnd = _last_true_index(audible, reset)
                ld = _last_true_index(damage.get((o, e), np.zeros(w.T, bool)), reset)

                age = lambda last: np.where(last == NEVER, 1 << 30, t_idx - last)  # noqa: E731
                a_seen, a_team, a_snd, a_dmg = age(ls), age(lt), age(lsnd), age(ld)

                known = (a_seen <= ms(p.known_recent_ms)) | bombing[e]
                likely = (
                    (a_seen <= ms(p.likely_recent_seen_ms))
                    | (a_team <= ms(p.team_now_ms))
                    | (a_dmg <= ms(p.damage_likely_ms))
                    | (a_snd <= ms(p.sound_likely_ms))
                )
                possibly = (
                    (a_seen <= ms(p.possibly_recent_seen_ms))
                    | (a_team <= ms(p.team_callout_ms))
                    | (a_dmg <= ms(p.damage_possibly_ms))
                    | (a_snd <= ms(p.sound_possibly_ms))
                    | np.isin(los[o, e], (LOS.DIRECT_VISIBLE, LOS.VISIBLE_THROUGH_SMOKE, LOS.UNKNOWN))
                    | round_start
                )
                for t0, t1, bpos in planted:
                    near = np.zeros(w.T, dtype=bool)
                    seg = slice(t0, t1)
                    near[seg] = np.linalg.norm(w.pos[e, seg].astype(np.float64) - bpos, axis=-1) <= p.bomb_site_possibly_radius
                    possibly |= near

                lvl = np.full(w.T, Knowledge.UNKNOWN, dtype=np.uint8)
                lvl[possibly] = Knowledge.POSSIBLY_KNOWN
                lvl[likely] = Knowledge.LIKELY_KNOWN
                lvl[known] = Knowledge.KNOWN
                lvl[~valid] = Knowledge.NOT_APPLICABLE
                self.level[o, e] = lvl

                info_src = (seen[o, e] | team_now | audible | damage.get((o, e), np.zeros(w.T, bool)))
                li = _last_true_index(info_src, reset)
                self.last_seen[o, e] = ls
                self.last_info[o, e] = li
                self.last_sound[o, e] = lsnd
                self.team_seen_now[o, e] = team_now

                # confidence that an UNKNOWN sample is really unknown
                since = age(li).astype(np.float64) / tr * 1000.0
                ramp = np.clip(since / p.unknown_full_confidence_after_ms, 0.5, 1.0)
                close = np.nan_to_num(dist, nan=1e9) < p.close_range_units
                conf = p.unknown_base_confidence * p.map_geometry_confidence * ramp * np.where(close, 0.8, 1.0)
                conf[lvl != Knowledge.UNKNOWN] = 0.0
                self.info_confidence[o, e] = conf.astype(np.float16)
        return self

    def _damage_pairs(self) -> dict[tuple[int, int], np.ndarray]:
        w = self.w
        out: dict[tuple[int, int], np.ndarray] = {}
        hurts = self.ev.get("hurts")
        if hurts is None or not len(hurts):
            return out
        for r in hurts.itertuples():
            a = w.index_of.get(int(r.attacker_steam_id))
            v = w.index_of.get(int(r.victim_steam_id))
            t = int(r.tick) - w.tick0
            if a is None or v is None or a == v or not (0 <= t < w.T):
                continue
            for key in ((a, v), (v, a)):
                out.setdefault(key, np.zeros(w.T, bool))[t] = True
        return out

    # ---------------------------------------------------------------- queries

    def ms_since(self, arr: np.ndarray, o: int, e: int, t: int) -> float | None:
        last = int(arr[o, e, t])
        if last == NEVER:
            return None
        return (t - last) * 1000.0 / self.w.tickrate

    def describe(self, o: int, e: int, t: int) -> dict:
        lvl = Knowledge(int(self.level[o, e, t]))
        snd = self.ms_since(self.last_sound, o, e, t)
        return {
            "knowledge": lvl.name,
            "teammate_los": bool(self.team_seen_now[o, e, t]),
            "last_seen_ms": self.ms_since(self.last_seen, o, e, t),
            "last_info_ms": self.ms_since(self.last_info, o, e, t),
            "possible_sound": snd is not None and snd <= self.p.sound_possibly_ms,
            "last_sound_ms": snd,
            "knowledge_confidence": float(self.info_confidence[o, e, t]),
        }

    def derived_state(self, o: int, e: int, t: int) -> str:
        """Spec visibility vocabulary including history-derived states."""
        los = LOS(int(self.vis.los[o, e, t]))
        if los in (LOS.DIRECT_VISIBLE, LOS.VISIBLE_THROUGH_SMOKE, LOS.NOT_APPLICABLE):
            return los.name
        ls = self.ms_since(self.last_seen, o, e, t)
        if ls is not None and ls <= self.p.likely_recent_seen_ms:
            return "RECENTLY_VISIBLE"
        if self.team_seen_now[o, e, t]:
            return "TEAMMATE_VISIBLE"
        snd = self.ms_since(self.last_sound, o, e, t)
        if snd is not None and snd <= self.p.sound_possibly_ms:
            return "POSSIBLE_SOUND_INFORMATION"
        return los.name
