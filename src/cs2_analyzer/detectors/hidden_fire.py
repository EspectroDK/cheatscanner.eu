"""Detector 25 - shooting at hidden enemies more precisely than legitimate information allows.

Why it exists
-------------
A careful wallhacker does not follow enemies through walls all match (that is
``information_gap``) and does not take the kill through the wall. He looks
and shoots at enemies behind walls and smokes now and then, does damage there,
and waits for them to come into view for the kill. Replay watchers spot him by
"he shoots exactly where they are behind the wall". This detector counts that,
over the whole match.

What is measured
----------------
Two counts per player, both at match scope:

* **precise blind bursts**: bursts of fire while no enemy is visible to the
  shooter (now or ``visible_lookback_ms`` before). A burst counts when one of
  its first ``first_shots`` shots is within ``on_target_deg`` of a hidden
  enemy (head, chest or pelvis) while the best legitimate estimate of that
  enemy's position (``knowledge/estimates.py``: sight, teammates and radar,
  sound, damage, each with its margin of error) is at least ``min_gap_deg``
  off. The same is counted against **ghost enemies**: the enemy team's
  positions at the same moment of up to ``ghost_rounds`` other rounds in which
  the shooter played the same side. Ghosts cannot be known, so they measure
  how often this player's shots hit enemy positions by habit (common
  wallbang spots, pre-fires). ``excess`` = real count - expected ghost count.
* **hidden hits**: hits (player_hurt with a gun) on an enemy who was behind a
  wall or a stable smoke core from the shooter's eye the tick before.

False-positive controls
-----------------------
* The ghost comparison removes spots that are shot at by habit, per player and
  per map.
* Only information from before the shot counts, but every legitimate source is
  generous (the closest estimate to the truth wins), and a sound gives at best
  a 6 deg estimate.
* Both counts must be high in the same match: a player who sprays common
  wallbang spots gets hidden hits but no excess, and a few lucky precise
  bursts do not come with many hidden hits.

Calibration (CS2CD, 174 de_mirage and de_nuke matches with map meshes,
``excess >= 3`` and ``hidden hits >= 10``): 1 of 714 clean players (6 of his 16
kills went through walls, in a Gold Nova match; possibly an unlabelled
cheater), 0 of 600 unlabelled players in cheater matches and 32 of 420 labelled
cheaters. Clean players' 99th percentiles are an excess of 2.8 and 10 hidden
hits. Raw observations: ``hidden_fire`` (one row per player).
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp
from cs2_analyzer.features.weapons import is_gun
from cs2_analyzer.geometry.angles import angular_distance, bearing
from cs2_analyzer.geometry.visibility import LOS
from cs2_analyzer.knowledge.estimates import EstimateParams, InformationSources, information_gap

VISIBLE = (int(LOS.DIRECT_VISIBLE), int(LOS.VISIBLE_THROUGH_SMOKE), int(LOS.UNKNOWN))
HIDDEN = (int(LOS.GEOMETRY_OCCLUDED), int(LOS.SMOKE_OCCLUDED))
BODY_DZ = (0.0, -18.0, -32.0)  # head, chest, pelvis below the eye


def _round_starts(w) -> dict[int, int]:
    starts: dict[int, int] = {}
    if w.live[0]:
        starts[int(w.round_of[0])] = 0
    for t in np.nonzero(w.live[1:] & ~w.live[:-1])[0] + 1:
        starts.setdefault(int(w.round_of[t]), int(t))
    return starts


def _min_error(eye: np.ndarray, pitch: float, yaw: float, head: np.ndarray) -> tuple[float, float]:
    """Smallest angle from the view to the head, chest or pelvis of a player, and that point's offset."""
    pts = np.repeat(head[None], len(BODY_DZ), axis=0)
    pts[:, 2] += BODY_DZ
    by, bp = bearing(np.repeat(eye[None], len(BODY_DZ), axis=0), pts)
    err = angular_distance(np.full(len(BODY_DZ), pitch), np.full(len(BODY_DZ), yaw), bp, by)
    k = int(np.nanargmin(err)) if np.isfinite(err).any() else 0
    return float(err[k]), BODY_DZ[k]


class HiddenFireDetector(Detector):
    name = "hidden_fire"
    axis = EvidenceAxis.HIDDEN_INFORMATION
    group = EvidenceGroup.INFORMATION
    default_reliability = 0.6
    label = "Precise shots at hidden enemies"

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        geom = getattr(ctx.vis, "geometry", None)
        if geom is None or not geom.available:
            return []
        self._cfg = cfg
        self._params = EstimateParams.from_config(ctx.cfg("information_gap"))
        self._sources = InformationSources(ctx.knowledge)
        self._starts = _round_starts(w)
        rounds = sorted(self._starts)
        self._ends = {r: (self._starts[rounds[i + 1]] if i + 1 < len(rounds) else w.T) for i, r in enumerate(rounds)}
        self._rounds = rounds
        hidden_hits = self._hidden_hits(ctx)
        events = []
        for o in range(w.P):
            if not ctx.analyze_player(o):
                continue
            bursts, real, expected, moments = self._bursts(ctx, o, geom)
            hits = hidden_hits.get(o, [])
            excess = real - expected
            dmg = float(sum(d for _, d in hits))
            ctx.observe(self.name, o, blind_bursts=bursts, precise_bursts=real, expected_precise_bursts=expected,
                        excess=excess, hidden_hits=len(hits), hidden_damage=dmg)
            min_excess = float(cfg.get("min_excess", 3.0))
            min_hits = int(cfg.get("min_hidden_hits", 10))
            if excess < min_excess or len(hits) < min_hits:
                continue
            sev = min(1.0, 0.35 + 0.35 * ramp(excess, min_excess, float(cfg.get("full_excess", 8.0)))
                      + 0.3 * ramp(len(hits), min_hits, int(cfg.get("full_hidden_hits", 25))))
            ticks = sorted(t for _, t in moments) or sorted(t for t, _ in hits)
            metrics = {"blind_bursts": bursts, "precise_bursts": real, "expected_precise_bursts": round(expected, 2),
                       "excess": round(excess, 2), "hidden_hits": len(hits), "hidden_damage": dmg}
            expl = (
                f"{self.label}: {real} times this player fired, with no enemy in view, within "
                f"{cfg.get('on_target_deg', 1.5):g} deg of an enemy hidden behind a wall or smoke while the best legitimate "
                f"information (sight, teammates, radar, sound, damage) was at least {cfg.get('min_gap_deg', 5.0):g} deg off. "
                f"Shooting at the same moments of other rounds explains {expected:.1f} of them. {len(hits)} hits "
                f"({dmg:.0f} damage) landed on enemies hidden from him. Of 714 clean players, one reached both "
                f"{min_excess:g} more than expected and {min_hits} hidden hits."
            )
            events.append(self.event(
                ctx, o, int(ticks[0]), int(ticks[len(ticks) // 2]), int(ticks[-1]), sev, 1.0, None, metrics,
                {"scope": "match",
                 "precise_bursts": [{"target_steam_id": ctx.sid(e), "tick": w.tick(t)} for e, t in moments[:20]],
                 "hidden_hits": [{"tick": w.tick(t), "damage": d} for t, d in hits[:30]]},
                expl))
        return events

    # ------------------------------------------------------------------ hidden hits

    def _hidden_hits(self, ctx) -> dict[int, list[tuple[int, float]]]:
        w = ctx.world
        hurts = ctx.demo.event("hurts")
        out: dict[int, list[tuple[int, float]]] = {}
        if hurts is None or not len(hurts):
            return out
        for r in hurts.itertuples():
            a, v = w.index_of.get(int(r.attacker_steam_id)), w.index_of.get(int(r.victim_steam_id))
            t = int(r.tick) - w.tick0
            if a is None or v is None or a == v or not is_gun(str(r.weapon)) or not (1 <= t < w.T):
                continue
            if not w.live[t] or not w.is_enemy(a, v, t):
                continue
            if int(ctx.vis.los[a, v, t - 1]) in HIDDEN:
                out.setdefault(a, []).append((t, float(r.dmg_health)))
        return out

    # ------------------------------------------------------------------ blind bursts

    def _side(self, w, o: int, r: int) -> int:
        s = self._starts[r]
        a = w.team[o, s:min(self._ends[r], s + 64)].astype(int)
        return int(np.bincount(a, minlength=4).argmax()) if a.size else 0

    def _bursts(self, ctx, o: int, geom):
        cfg, w, los = self._cfg, ctx.world, ctx.vis.los
        on_deg = float(cfg.get("on_target_deg", 1.5))
        min_gap = float(cfg.get("min_gap_deg", 5.0))
        n_first = int(cfg.get("first_shots", 3))
        max_dist = float(cfg.get("max_distance_u", 3000.0))
        lookback = w.ticks_for_ms(cfg.get("visible_lookback_ms", 250))
        burst_gap = w.ticks_for_ms(cfg.get("burst_gap_ms", 500))
        n_ghost = int(cfg.get("ghost_rounds", 6))
        shots = w.shot_ticks.get(o, np.array([], dtype=int))
        if not shots.size:
            return 0, 0, 0.0, []
        burst_id = np.cumsum(np.r_[True, np.diff(shots) > burst_gap])
        lasts = {}
        n_bursts, real, expected, moments = 0, 0, 0.0, []
        for b in np.unique(burst_id):
            taken, hit, ghost_hits = 0, None, None
            for f in shots[burst_id == b]:
                if taken >= n_first:
                    break
                f = int(f)
                r = int(w.round_of[f])
                if not w.live[f] or not w.alive[o, f] or r not in self._starts:
                    continue
                side = int(w.team[o, f])
                ens = [e for e in range(w.P) if e != o and w.alive[e, f] and w.team[e, f] in (2, 3) and w.team[e, f] != side]
                if any(int(los[o, e, f]) in VISIBLE or int(los[o, e, max(f - lookback, 0)]) in VISIBLE for e in ens):
                    continue
                taken += 1
                eye = w.eye[o, f].astype(np.float64)
                p, y = float(w.pitch[o, f]), float(w.yaw[o, f])
                if hit is None:
                    for e in ens:
                        if int(los[o, e, f]) not in HIDDEN:
                            continue
                        head = w.eye[e, f].astype(np.float64)
                        if np.linalg.norm(head - eye) > max_dist:
                            continue
                        err, _ = _min_error(eye, p, y, head)
                        if err > on_deg:
                            continue
                        if e not in lasts:
                            lasts[e] = self._sources.last(o, e)
                        gap, _ = information_gap(w, lasts[e], o, e, np.array([f]), self._params)
                        if float(gap[0]) >= min_gap:
                            hit = (e, f)
                            break
                g = self._ghost_hits(w, o, f, r, side, eye, p, y, geom, on_deg, max_dist, n_ghost)
                if ghost_hits is None:
                    ghost_hits = g
                else:
                    ghost_hits = [a or c for a, c in zip(ghost_hits, g)] + ghost_hits[len(g):] + g[len(ghost_hits):]
            if taken == 0 or not ghost_hits:
                continue
            n_bursts += 1
            expected += float(np.mean(ghost_hits))
            if hit is not None:
                real += 1
                moments.append(hit)
        return n_bursts, real, expected, moments

    def _ghost_hits(self, w, o, f, r, side, eye, p, y, geom, on_deg, max_dist, n_ghost) -> list[bool]:
        """For each of the nearest other rounds with o on the same side: would this shot have been on a
        hidden enemy standing where the enemy team stood at the same time after freeze end?"""
        tau = f - self._starts[r]
        cands = sorted((q for q in self._rounds if q != r and self._side(w, o, q) == side), key=lambda q: abs(q - r))
        out = []
        for q in cands[:n_ghost]:
            j = self._starts[q] + tau
            if j >= self._ends[q] or not w.live[j]:
                continue
            on = False
            for e in range(w.P):
                if e == o or not w.alive[e, j] or w.team[e, j] not in (2, 3) or w.team[e, j] == side:
                    continue
                head = w.eye[e, j].astype(np.float64)
                if np.linalg.norm(head - eye) > max_dist:
                    continue
                err, dz = _min_error(eye, p, y, head)
                if err > on_deg:
                    continue
                pt = head.copy()
                pt[2] += dz
                if not geom.segment_clear(eye[None], pt[None], 4.0)[0]:
                    on = True
                    break
            out.append(on)
        return out
