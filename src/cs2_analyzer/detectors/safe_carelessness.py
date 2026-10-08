"""Detector 27 - knife or bomb out exactly when it is safe, put away when a hidden enemy is near.

Why it exists
-------------
Spectators of wallhackers notice that they *know* when they are safe: they may
still check a corner for show, but they run through parts of the map nobody
has cleared with the knife or the bomb in hand, and have the gun out when an
enemy really is behind the next wall. A clean player cannot tell those two
situations apart, so how often they hold the knife does not depend on whether a
hidden enemy is actually close.

What is measured
----------------
Every ``step_ms`` while the player is alive in a live round, sees no enemy and
has no legitimate information about any enemy within ``known_u`` (best estimate
of the enemy's position, ``knowledge/estimates.py``, within ``known_gap_deg``):
they cannot know whether the ground around them is safe. At each such moment:

* **careless**: the knife or the bomb is the active weapon;
* **threat**: an enemy they cannot see or know about is within ``radius_u``.

``real`` = careless share in safe moments minus careless share in threat
moments. Habits tied to place and time (the knife comes out on the way to a
site, the gun on arrival) are removed with **ghost enemies**: the enemy team at
the same time of up to ``ghost_rounds`` other rounds with the same side, seen
from the player's real position (hidden behind walls from their eye, head and
chest). Each moment gets the share of those rounds with a hidden ghost enemy
within ``radius_u`` as its ghost threat weight, and ``expected`` = the same
difference weighted by it. ``excess = real - expected``; ``z`` divides it by its
standard error, counting ``deflate`` neighbouring samples as one observation.

False-positive controls
-----------------------
* Only moments with no enemy in view and none known within ``known_u`` count,
  so a player who heard or saw someone and drew their gun is not a sign of
  anything; every legitimate source is generous (the closest estimate wins).
* The ghost comparison removes spots and times where this player always has
  the knife out or the gun up, per player and per map.
* The player needs ``min_threat_moments`` threat moments, ``min_safe_moments``
  safe ones and enough ghost threat and ghost safety to compare; most players
  never have that many moments near a hidden enemy, and they are left alone.
* Both the size of the difference and its z-score must be high.

Calibration (434 CS2CD matches on five maps with meshes, 2026-10-08, 800 u):
AUC 0.71 for labelled cheaters vs clean players; inside each quarter of overall
knife use it stays 0.66 to 0.75, and unlabelled players in cheater matches look
like clean ones (AUC 0.51). The rule (excess >= 0.4 and z >= 2.5) fires for 2 of
854 clean players who qualified, 1 of 231 unlabelled and 11 of 141 labelled
cheaters, 8 of them caught by neither ``information_gap`` nor ``hidden_fire``
(docs/validation/cs2cd/safe_carelessness.md). Running loudly in the same
situation did not separate (AUC 0.54 to 0.65) and is not used. Raw
observations: ``safe_carelessness`` (one row per player).
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp
from cs2_analyzer.geometry.visibility import LOS
from cs2_analyzer.knowledge.estimates import EstimateParams, InformationSources, information_gap

HIDDEN = (int(LOS.GEOMETRY_OCCLUDED), int(LOS.SMOKE_OCCLUDED))
CARELESS_WEAPONS = ("knife", "c4")


def _round_starts(w) -> dict[int, int]:
    starts: dict[int, int] = {}
    if w.live[0]:
        starts[int(w.round_of[0])] = 0
    for t in np.nonzero(w.live[1:] & ~w.live[:-1])[0] + 1:
        starts.setdefault(int(w.round_of[t]), int(t))
    return starts


class SafeCarelessnessDetector(Detector):
    name = "safe_carelessness"
    axis = EvidenceAxis.HIDDEN_INFORMATION
    group = EvidenceGroup.INFORMATION
    default_reliability = 0.5
    label = "Knife out only when it is safe"

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        geom = getattr(ctx.vis, "geometry", None)
        if geom is None or not geom.available or getattr(w, "weapon_class", None) is None:
            return []
        self._cfg = cfg
        self._params = EstimateParams.from_config(ctx.cfg("information_gap"))
        self._sources = InformationSources(ctx.knowledge)
        self._starts = _round_starts(w)
        rounds = sorted(self._starts)
        self._ends = {r: (self._starts[rounds[i + 1]] if i + 1 < len(rounds) else w.T) for i, r in enumerate(rounds)}
        self._rounds = rounds
        self._sides: dict[tuple[int, int], int] = {}
        min_threat = int(cfg.get("min_threat_moments", 20))
        min_safe = int(cfg.get("min_safe_moments", 50))
        min_ghost_threat = float(cfg.get("min_ghost_threat", 5.0))
        min_ghost_safe = float(cfg.get("min_ghost_safe", 20.0))
        deflate = float(cfg.get("deflate", 4.0))
        min_excess = float(cfg.get("min_excess", 0.4))
        full_excess = float(cfg.get("full_excess", 0.7))
        min_z = float(cfg.get("min_z", 2.5))
        full_z = float(cfg.get("full_z", 4.0))
        radius = float(cfg.get("radius_u", 800.0))
        events = []
        for o in range(w.P):
            if not ctx.analyze_player(o):
                continue
            m = self._moments(ctx, o, geom, radius)
            if m is None:
                continue
            ts, careless, threat, ghost = m
            v = careless.astype(float)
            n_t, n_s = int(threat.sum()), int((~threat).sum())
            g_t, g_s = float(ghost.sum()), float((1 - ghost).sum())
            obs = {"moments": len(ts), "threat_moments": n_t, "safe_moments": n_s,
                   "careless_share": float(v.mean()) if len(v) else 0.0}
            if n_t < min_threat or n_s < min_safe or g_t < min_ghost_threat or g_s < min_ghost_safe:
                ctx.observe(self.name, o, **obs)
                continue
            safe_rate, threat_rate = float(v[~threat].mean()), float(v[threat].mean())
            ghost_safe_rate = float((v * (1 - ghost)).sum() / g_s)
            ghost_threat_rate = float((v * ghost).sum() / g_t)
            real = safe_rate - threat_rate
            expected = ghost_safe_rate - ghost_threat_rate
            excess = real - expected
            p = float(v.mean())
            se = float(np.sqrt(max(p * (1 - p), 1e-3) * (1 / n_t + 1 / n_s) * deflate))
            z = excess / se
            obs.update(safe_rate=safe_rate, threat_rate=threat_rate, ghost_safe_rate=ghost_safe_rate,
                       ghost_threat_rate=ghost_threat_rate, excess=excess, z=z)
            ctx.observe(self.name, o, **obs)
            if excess < min_excess or z < min_z:
                continue
            sev = min(1.0, 0.35 + 0.35 * ramp(excess, min_excess, full_excess) + 0.3 * ramp(z, min_z, full_z))
            safe_ticks = ts[careless & ~threat]
            near_ticks = ts[threat]
            marks = safe_ticks if len(safe_ticks) else ts
            metrics = {"safe_moments": n_s, "threat_moments": n_t, "safe_rate": round(safe_rate, 3),
                       "threat_rate": round(threat_rate, 3), "ghost_safe_rate": round(ghost_safe_rate, 3),
                       "ghost_threat_rate": round(ghost_threat_rate, 3), "excess": round(excess, 3), "z": round(z, 2)}
            events.append(self.event(
                ctx, o, int(marks[0]), int(marks[len(marks) // 2]), int(marks[-1]), sev, 1.0, None, metrics,
                {"scope": "match", "radius_u": radius,
                 "knife_out_safe": [{"tick": w.tick(int(t))} for t in safe_ticks[:20]],
                 "enemy_near": [{"tick": w.tick(int(t))} for t in near_ticks[:20]]},
                f"{self.label}: with no enemy in view or known, this player had the knife or bomb out "
                f"{safe_rate:.0%} of the time when no hidden enemy was within {radius:.0f} u, but only "
                f"{threat_rate:.0%} of the time when one actually was ({n_t} such moments), something they could not "
                f"have known legitimately. Where and when enemies usually are in their other rounds explains "
                f"{expected * 100:+.0f} points of that {real * 100:+.0f}. Of 854 clean players with enough such "
                f"moments, two reached {min_excess * 100:.0f} points beyond that and a z-score of {min_z:g}."))
        return events

    # ------------------------------------------------------------------ moments

    def _side(self, w, o: int, r: int) -> int:
        k = (o, r)
        if k not in self._sides:
            s = self._starts[r]
            a = w.team[o, s:min(self._ends[r], s + 64)].astype(int)
            self._sides[k] = int(np.bincount(a, minlength=4).argmax()) if a.size else 0
        return self._sides[k]

    def _ghost_rounds(self, w, o: int, r: int) -> list[int]:
        side = self._side(w, o, r)
        cands = [q for q in self._rounds if q != r and self._side(w, o, q) == side]
        return sorted(cands, key=lambda q: abs(q - r))[: int(self._cfg.get("ghost_rounds", 6))]

    def _moments(self, ctx, o: int, geom, radius: float):
        """Sample ticks where o cannot know whether they are safe, with careless / threat / ghost threat weight."""
        cfg, w, los = self._cfg, ctx.world, ctx.vis.los
        step = max(1, w.ticks_for_ms(cfg.get("step_ms", 500)))
        skip = w.ticks_for_ms(cfg.get("skip_round_start_ms", 2000))
        known_gap = float(cfg.get("known_gap_deg", 15.0))
        known_u = float(cfg.get("known_u", 2500.0))
        ts_l, rr_l = [], []
        for r in self._rounds:
            if not self._ghost_rounds(w, o, r):
                continue
            tt = np.arange(self._starts[r] + skip, self._ends[r] - step, step)
            tt = tt[w.live[tt] & w.alive[o, tt]]
            ts_l.append(tt)
            rr_l.append(np.full(len(tt), r))
        if not ts_l:
            return None
        ts, rr = np.concatenate(ts_l).astype(int), np.concatenate(rr_l).astype(int)
        if not len(ts):
            return None
        sides = np.array([self._side(w, o, int(r)) for r in rr])
        ok = np.ones(len(ts), bool)
        n_enemy = np.zeros(len(ts), int)
        dmin = np.full(len(ts), np.inf)
        eye = w.eye[o, ts].astype(np.float64)
        for e in range(w.P):
            if e == o:
                continue
            en = w.alive[e, ts] & np.isin(w.team[e, ts], (2, 3)) & (w.team[e, ts] != sides)
            if not en.any():
                continue
            n_enemy += en
            seen = en & ~np.isin(los[o, e, ts].astype(int), HIDDEN)
            gap, _ = information_gap(w, self._sources.last(o, e), o, e, ts, self._params)
            dist = np.linalg.norm(w.eye[e, ts].astype(np.float64) - eye, axis=1)
            known = en & (gap < known_gap) & (dist <= known_u)
            ok &= ~seen & ~known
            dmin = np.where(en & ~seen & ~known, np.minimum(dmin, dist), dmin)
        ok &= n_enemy > 0
        if not ok.any():
            return None
        ts, rr, sides, dmin, eye = ts[ok], rr[ok], sides[ok], dmin[ok], eye[ok]
        # ghost threat weight: share of ghost rounds with a hidden enemy within radius of the real eye
        n_g = np.zeros(len(ts), int)
        qi, ji, ei = [], [], []
        for i, (t, r, side) in enumerate(zip(ts, rr, sides)):
            tau = int(t) - self._starts[int(r)]
            for q in self._ghost_rounds(w, o, int(r)):
                j = self._starts[q] + tau
                if j >= self._ends[q] or not w.live[j]:
                    continue
                n_g[i] += 1
                for e in range(w.P):
                    if e != o and w.alive[e, j] and w.team[e, j] in (2, 3) and w.team[e, j] != side:
                        qi.append(i)
                        ji.append(j)
                        ei.append(e)
        near_g = np.zeros(len(ts), int)
        if qi:
            qi_a, ji_a, ei_a = np.array(qi), np.array(ji), np.array(ei)
            pts = w.eye[ei_a, ji_a].astype(np.float64)
            src = eye[qi_a]
            close = np.linalg.norm(pts - src, axis=1) <= radius
            if close.any():
                low = pts[close].copy()
                low[:, 2] -= 18.0
                hid = ~geom.segment_clear(src[close], pts[close], 4.0) & ~geom.segment_clear(src[close], low, 4.0)
                pairs = {(int(i), int(j)) for i, j in zip(qi_a[close][hid], ji_a[close][hid])}
                for i, _ in pairs:
                    near_g[i] += 1
        ghost = np.where(n_g > 0, near_g / np.maximum(n_g, 1), 0.0)
        careless = np.isin(w.weapon_class[o, ts], CARELESS_WEAPONS)
        threat = dmin <= radius
        return ts, careless, threat, ghost
