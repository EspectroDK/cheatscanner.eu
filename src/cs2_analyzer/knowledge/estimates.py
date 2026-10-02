"""Information with a margin of error: how close could a legitimate guess be?

The knowledge model (``knowledge/model.py``) answers *whether* an observer
has some legitimate information about an enemy. That is a yes/no answer, and
in a real fight it is almost always "yes": a footstep two seconds ago, a
sighting three seconds ago, a teammate who sees the enemy. Hidden-information
detectors that only use moments with *no* information therefore measure
almost nothing in smoke and wall fights.

This module turns every legitimate source into an **estimate of where the
enemy is**, and measures how far the best of those estimates is from the
enemy's true position, as an angle seen from the observer:

* own sight: the enemy's last seen position, and that position extrapolated
  along the enemy's velocity at that moment for up to ``extrapolate_s``;
* teammates' sight and the radar (the game's ``spotted`` flag): the same,
  plus ``team_floor_deg`` because radar and callouts are coarse;
* sound (footsteps, gunfire, jumps, reloads): where the enemy was when the
  noise was made, plus ``sound_floor_deg`` because hearing gives a direction
  and a rough distance, not a point;
* damage exchanged: where the enemy was, plus ``damage_floor_deg``.

The best estimate is chosen *knowing the true position* (the source that
happens to be closest wins), which over-states what a legitimate player can
know. The resulting ``gap`` is therefore a lower bound on a legitimate
player's uncertainty. When a player's crosshair follows the enemy's true
position much more closely than this gap, again and again, the information
came from somewhere the model does not know about.

All values are UNCALIBRATED unless stated in config ``detectors.information_gap``.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cs2_analyzer.geometry.angles import angular_distance, bearing
from cs2_analyzer.knowledge.model import NEVER, _last_true_index


@dataclass
class EstimateParams:
    sight_floor_deg: float = 0.0
    team_floor_deg: float = 1.5
    sound_floor_deg: float = 6.0
    damage_floor_deg: float = 10.0
    extrapolate_s: float = 0.6

    @classmethod
    def from_config(cls, cfg: dict) -> "EstimateParams":
        return cls(**{k: float(v) for k, v in cfg.items() if k in cls.__dataclass_fields__})


class InformationSources:
    """Per-pair "last tick with this kind of information" arrays, built once per match."""

    def __init__(self, knowledge):
        self.kn = knowledge
        self.w = knowledge.w
        self._reset = knowledge._round_reset()
        self._damage = knowledge._damage_pairs()

    def last(self, o: int, e: int) -> dict[str, np.ndarray]:
        kn, w = self.kn, self.w
        dmg = self._damage.get((o, e))
        return {
            "sight": kn.last_seen[o, e],
            "team": _last_true_index(kn.team_seen_now[o, e], self._reset),
            "sound": kn.last_sound[o, e],
            "damage": _last_true_index(dmg, self._reset) if dmg is not None else np.full(w.T, NEVER, dtype=np.int32),
        }


def information_gap(world, last: dict[str, np.ndarray], o: int, e: int, idx: np.ndarray,
                    params: EstimateParams) -> tuple[np.ndarray, np.ndarray]:
    """Angle (deg) between the enemy's true head and the best legitimate estimate.

    Only information available *before* each tick is used (``idx - 1``), so a
    sighting that is caused by the aim itself (a peek, a bullet hole) does not
    explain that same tick. Returns ``(gap_deg, source)`` where ``source`` is
    the index into ``("sight", "team", "sound", "damage")`` of the best
    estimate, or -1 when the observer has no information at all (gap = inf).
    """
    idx = np.asarray(idx)
    prev = np.maximum(idx - 1, 0)
    eye_o = world.eye[o, idx].astype(np.float64)
    true = world.eye[e, idx].astype(np.float64)
    ty, tp = bearing(eye_o, true)
    gap = np.full(len(idx), np.inf)
    src = np.full(len(idx), -1, dtype=np.int8)
    floors = {"sight": params.sight_floor_deg, "team": params.team_floor_deg,
              "sound": params.sound_floor_deg, "damage": params.damage_floor_deg}
    for k, (name, arr) in enumerate(last.items()):
        li = arr[prev]
        has = li != NEVER
        if not has.any():
            continue
        li = np.where(has, li, idx)
        base = world.eye[e, li].astype(np.float64)
        cands = [base]
        if name in ("sight", "team") and params.extrapolate_s > 0:
            age = np.minimum((idx - li) / world.tickrate, params.extrapolate_s)
            cands.append(base + world.vel[e, li].astype(np.float64) * age[:, None])
        for c in cands:
            cy, cp = bearing(eye_o, c)
            g = angular_distance(tp, ty, cp, cy) + floors[name]
            g = np.where(has & np.isfinite(g), g, np.inf)
            better = g < gap
            gap[better] = g[better]
            src[better] = k
    return gap, src


SOURCE_NAMES = ("sight", "team", "sound", "damage")
