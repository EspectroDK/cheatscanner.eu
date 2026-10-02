"""Detector 24 - following hidden enemies more closely than any legitimate information allows.

Why it exists
-------------
``hidden_tracking`` and ``smoke_tracking`` only use moments in which the
knowledge model finds *no* legitimate information. In real smoke and wall
fights there nearly always is some (a footstep, a sighting a few seconds ago,
a teammate's view), so those moments are thrown out and a player who follows
enemies through smoke and walls all match long can score zero. But a footstep
or an old sighting only says roughly where the enemy is. It cannot let anyone
keep the crosshair on a moving enemy's head, through his direction changes,
again and again.

What is measured
----------------
For every tick an enemy is hidden behind a wall or a stable smoke core, the
best legitimate estimate of his position is built from sight, teammates and
radar, sound and damage, each with a margin of error
(``knowledge/estimates.py``). The ``gap`` is the angle between that estimate
and the enemy's true head. Two things are then counted per player over the
whole match, only where the gap is at least ``min_gap_deg``:

* **tracking**: the enemy's own movement (in degrees of bearing, the part
  caused by the observer's movement excluded) while the crosshair stays on
  him, within ``on_target_deg`` and well inside the gap, compared with all
  such movement of hidden enemies;
* **first shots**: the first shot of each burst fired at an enemy who was
  hidden just before it, and how many of them were on his head although the
  legitimate information was vague.

False-positive controls
-----------------------
* **Generous information**: every source is an estimate, and the closest one
  to the truth is picked; footstep sound is treated as continuous while an
  enemy runs (knowledge model) with a floor of only ``sound_floor_deg``.
* **Holding an angle** is not tracking: only the enemy's own bearing change
  counts, so an enemy walking into a crosshair that stays still adds at most a
  few degrees.
* **Only information before the tick counts**, so a peek or a bullet hole that
  shows the enemy does not explain itself, and uncertain visibility (smoke
  shells, bloom, fade, bullet holes, occlusion edges) never counts as hidden.
* **Repetition**: the event is only raised from the whole match, when both the
  amount and the share of tracked movement are far above clean players, never
  from one moment. First shots only add to the severity, they never raise the
  event on their own (one clean CS2CD player wallbangs well enough to pass a
  first-shot rule).

Calibration (CS2CD, 174 de_mirage and de_nuke matches with map meshes): at
``min_tracked_deg = 300`` and ``min_tracked_share = 0.03`` the event fires for
0 of 714 clean players, 0 of 600 unlabelled players in cheater matches and 46
of 420 labelled cheaters (23 of 98 on Nuke, 23 of 322 on Mirage). The highest
clean player reaches 283 deg at a share of 3%, or 1.8% among those above 300
deg. CS2CD smokes rarely leave a stable core between players for long, so the
calibration covers wall fights far more than smoke fights.

Raw observations: ``information_gap`` (one row per hidden stretch) and
``information_gap_shots`` (one row per first shot at a hidden enemy).
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp
from cs2_analyzer.detectors.common import runs
from cs2_analyzer.features.pair import pair_series
from cs2_analyzer.geometry.angles import angular_distance, bearing, smooth
from cs2_analyzer.geometry.visibility import LOS
from cs2_analyzer.knowledge.estimates import SOURCE_NAMES, EstimateParams, InformationSources, information_gap

HIDDEN = (int(LOS.GEOMETRY_OCCLUDED), int(LOS.SMOKE_OCCLUDED))


class InformationGapDetector(Detector):
    name = "information_gap"
    axis = EvidenceAxis.HIDDEN_INFORMATION
    group = EvidenceGroup.INFORMATION
    default_reliability = 0.6
    label = "Tracking beyond available information"

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        params = EstimateParams.from_config(cfg)
        sources = InformationSources(ctx.knowledge)
        min_run = w.ticks_for_ms(cfg.get("min_run_ms", 500))
        min_gap = float(cfg.get("min_gap_deg", 6.0))
        on_deg = float(cfg.get("on_target_deg", 3.0))
        on_frac = float(cfg.get("on_target_gap_fraction", 0.5))
        max_shot_err = float(cfg.get("max_shot_error_deg", 20.0))
        burst_gap = w.ticks_for_ms(cfg.get("burst_gap_ms", 1000))
        smooth_n = int(cfg.get("smooth_ticks", 5))
        los = ctx.vis.los
        events: list[EvidenceEvent] = []
        for o in range(w.P):
            if not ctx.analyze_player(o):
                continue
            tot = {"opp": 0.0, "tracked": 0.0, "smoke_opp": 0.0, "smoke_tracked": 0.0, "shots": 0, "shots_on": 0,
                   "smoke_shots": 0, "smoke_shots_on": 0}
            moments, shot_moments = [], []
            lasts = {}
            for e in range(w.P):
                if e == o:
                    continue
                hidden = np.isin(los[o, e], HIDDEN) & w.alive[o] & w.alive[e] & w.live & w.is_enemy(o, e)
                if not hidden.any():
                    continue
                lasts[e] = last = sources.last(o, e)
                for a, b in runs(hidden):
                    if b - a + 1 < min_run:
                        continue
                    idx = np.arange(a, b + 1)
                    gap, src = information_gap(w, last, o, e, idx, params)
                    ps = pair_series(w, o, e, a, b)
                    move = np.abs(smooth(ps.d_bearing_target_yaw, smooth_n))
                    move[~np.isfinite(move)] = 0.0
                    beyond = gap >= min_gap
                    on = beyond & (ps.err <= np.minimum(on_deg, on_frac * gap))
                    opp, tracked = float(move[beyond].sum()), float(move[on].sum())
                    smoke = float(np.mean(los[o, e, a : b + 1] == LOS.SMOKE_OCCLUDED)) >= 0.5
                    streak = _max_streak(move, on)
                    fin = np.isfinite(gap[beyond])
                    ctx.observe(self.name, o, target_steam_id=ctx.sid(e), round=int(w.round_of[a]),
                                tick_start=w.tick(a), tick_end=w.tick(b), occlusion="smoke" if smoke else "wall",
                                duration_ms=(b - a + 1) * 1000.0 / w.tickrate,
                                beyond_ms=float(beyond.sum()) * 1000.0 / w.tickrate,
                                on_target_ms=float(on.sum()) * 1000.0 / w.tickrate,
                                opportunity_deg=opp, tracked_deg=tracked, max_tracked_streak_deg=streak,
                                median_gap_deg=float(np.median(gap[beyond][fin])) if fin.any() else None,
                                median_error_deg=float(np.nanmedian(ps.err[beyond])) if beyond.any() else None,
                                main_source=_main_source(src[beyond]),
                                mean_distance_u=float(np.nanmean(ps.distance)))
                    tot["opp"] += opp
                    tot["tracked"] += tracked
                    if smoke:
                        tot["smoke_opp"] += opp
                        tot["smoke_tracked"] += tracked
                    if tracked > 0:
                        moments.append((tracked, e, a, b, smoke))
            # first shots of bursts at enemies hidden just before the shot
            st = w.shot_ticks.get(o, np.array([], dtype=int))
            if st.size:
                first = st[np.r_[True, np.diff(st) > burst_gap]]
                for s in first:
                    if s < 1 or not w.live[s]:
                        continue
                    for e, last in lasts.items():
                        if not (w.alive[e, s] and los[o, e, s - 1] in HIDDEN):
                            continue
                        by, bp = bearing(w.eye[o, s].astype(np.float64), w.eye[e, s].astype(np.float64))
                        err = float(angular_distance(w.pitch[o, s], w.yaw[o, s], bp, by))
                        if not np.isfinite(err) or err > max_shot_err:
                            continue
                        gap, src = information_gap(w, last, o, e, np.array([s]), params)
                        g = float(gap[0])
                        smoke = int(los[o, e, s - 1]) == LOS.SMOKE_OCCLUDED
                        is_on = g >= min_gap and err <= min(on_deg, on_frac * g)
                        ctx.observe("information_gap_shots", o, target_steam_id=ctx.sid(e), round=int(w.round_of[s]),
                                    tick=w.tick(s), occlusion="smoke" if smoke else "wall", error_deg=err,
                                    gap_deg=g if np.isfinite(g) else None, beyond=bool(g >= min_gap), on_target=bool(is_on),
                                    source=SOURCE_NAMES[src[0]] if src[0] >= 0 else "none",
                                    distance_u=float(np.linalg.norm(w.eye[e, s] - w.eye[o, s])))
                        if is_on:
                            shot_moments.append((e, s))
                        if g >= min_gap:
                            tot["shots"] += 1
                            tot["shots_on"] += int(is_on)
                            if smoke:
                                tot["smoke_shots"] += 1
                                tot["smoke_shots_on"] += int(is_on)
            ev = self._match_event(ctx, cfg, o, tot, moments, shot_moments)
            if ev is not None:
                events.append(ev)
        return events

    def _match_event(self, ctx, cfg, o, tot, moments, shot_moments) -> EvidenceEvent | None:
        w = ctx.world
        ratio = tot["tracked"] / tot["opp"] if tot["opp"] > 0 else 0.0
        shot_share = tot["shots_on"] / tot["shots"] if tot["shots"] else 0.0
        metrics = {
            "tracked_deg": tot["tracked"], "opportunity_deg": tot["opp"], "tracked_share": ratio,
            "smoke_tracked_deg": tot["smoke_tracked"], "smoke_opportunity_deg": tot["smoke_opp"],
            "first_shots_beyond_information": tot["shots"], "first_shots_on_target": tot["shots_on"],
            "first_shot_on_target_share": shot_share,
            "smoke_first_shots": tot["smoke_shots"], "smoke_first_shots_on_target": tot["smoke_shots_on"],
        }
        min_tracked = float(cfg.get("min_tracked_deg", 300.0))
        min_share = float(cfg.get("min_tracked_share", 0.03))
        fires = tot["tracked"] >= min_tracked and ratio >= min_share
        shots_back = tot["shots_on"] >= cfg.get("min_shots_on_target", 12) and shot_share >= cfg.get("min_shot_share", 0.3)
        metrics["first_shots_support"] = bool(shots_back)
        if not fires:
            return None
        sev = (0.4 + 0.3 * ramp(tot["tracked"], min_tracked, 2 * min_tracked)
               + 0.3 * ramp(ratio, min_share, float(cfg.get("full_tracked_share", 0.08)))
               + float(cfg.get("first_shot_bonus", 0.1)) * shots_back)
        sev = min(1.0, sev)
        moments.sort(key=lambda m: -m[0])
        top = [{"target_steam_id": ctx.sid(e), "round": int(w.round_of[a]), "tick_start": w.tick(a), "tick_end": w.tick(b),
                "tracked_deg": round(t, 1), "occlusion": "smoke" if sm else "wall"} for t, e, a, b, sm in moments[:10]]
        _, e0, a0, b0, _ = moments[0]
        expl = (
            f"{self.label}: while enemies were hidden behind walls or smoke and the best legitimate information "
            f"(own sight, teammates, radar, sound, damage, each with its margin of error) was at least "
            f"{cfg.get('min_gap_deg', 6.0):.0f} deg off, the crosshair stayed on the enemy's head through "
            f"{tot['tracked']:.0f} deg of the enemies' own movement, {ratio * 100:.1f}% of all such movement. "
            f"Clean players typically manage about 1%, and none of 714 reached both {min_tracked:.0f} deg and "
            f"{min_share * 100:.0f}%. {tot['shots_on']} of {tot['shots']} first shots at such enemies were on target."
        )
        return self.event(ctx, o, a0, (a0 + b0) // 2, b0, sev, 1.0, e0, metrics,
                          {"scope": "match", "top_moments": top,
                           "on_target_first_shots": [{"target_steam_id": ctx.sid(e), "tick": w.tick(t)}
                                                     for e, t in shot_moments[:20]]}, expl)


def _max_streak(move: np.ndarray, on: np.ndarray) -> float:
    best = cur = 0.0
    for m, k in zip(move, on):
        cur = cur + m if k else 0.0
        best = max(best, cur)
    return float(best)


def _main_source(src: np.ndarray) -> str:
    if not src.size:
        return "none"
    vals, counts = np.unique(src, return_counts=True)
    k = int(vals[np.argmax(counts)])
    return SOURCE_NAMES[k] if k >= 0 else "none"
