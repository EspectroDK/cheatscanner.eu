"""Contextual information detectors: flash behavior (11) and strategic
information anomalies (12).

Both are deliberately weak. A flash kill is not a cheat; strategic choices are
dominated by game sense, comms, spawns and common strategies. They exist to
collect corroborating context and large-sample statistics.
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp
from cs2_analyzer.detectors.common import runs, tracking_metrics
from cs2_analyzer.features.pair import pair_series
from cs2_analyzer.geometry.angles import angular_distance, bearing
from cs2_analyzer.geometry.visibility import LOS
from cs2_analyzer.knowledge.model import Knowledge


class FlashDetector(Detector):
    """Detector 11 - behavior while heavily flashed.

    Records kills/hits/shots while blind and measures whether the crosshair
    tracked a moving enemy the player could not see because of the flash.
    Sound is a legitimate cue while blind, so windows with recent enemy noise
    are excluded from the tracking evidence.
    """

    name = "flash"
    axis = EvidenceAxis.HIDDEN_INFORMATION
    group = EvidenceGroup.INFORMATION
    default_reliability = 0.2

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        kn = ctx.knowledge
        blind_s = float(cfg.get("min_remaining_blind_s", 1.5))
        min_len = w.ticks_for_ms(cfg.get("min_window_ms", 500))
        events = []
        deaths = ctx.demo.event("deaths")
        for o in range(w.P):
            if not ctx.analyze_player(o):
                continue
            blind = (w.flash_remaining[o] >= blind_s) & w.alive[o] & w.live
            n_kills = 0
            if len(deaths):
                for r in deaths[deaths["attacker_steam_id"] == ctx.sid(o)].itertuples():
                    t = int(r.tick) - w.tick0
                    if 0 <= t < w.T and blind[t]:
                        n_kills += 1
                        ctx.observe(self.name, o, kind="kill_while_blind", tick=int(r.tick), weapon=str(r.weapon),
                                    target_steam_id=int(r.victim_steam_id))
            for a, b in runs(blind):
                if b - a + 1 < min_len:
                    continue
                for e in range(w.P):
                    if e == o or not w.is_enemy(o, e, a) or not w.alive[e, a:b + 1].all():
                        continue
                    snd = kn.last_sound[o, e, a:b + 1]
                    if (snd > a - w.ticks_for_ms(1000)).any():  # heard recently: legitimate cue
                        continue
                    ps = pair_series(w, o, e, a, b)
                    m = tracking_metrics(ps, w.dt)
                    m["blind_remaining_s_at_start"] = float(w.flash_remaining[o, a])
                    ctx.observe(self.name, o, kind="tracking_while_blind", target_steam_id=ctx.sid(e), tick=w.tick(a),
                                **{k: v for k, v in m.items() if not isinstance(v, dict)})
                    sev = (ramp(m["tracking_corr"], 0.6, 0.9) * ramp(m["target_angular_path_deg"], 3, 12)
                           * ramp(-m["mean_error_deg"], -10, -3))
                    if sev >= cfg.get("min_event_severity", 0.3):
                        events.append(self.event(ctx, o, a, a + int(np.nanargmin(ps.err)), b, sev, 0.8, e, m, {"blind": True},
                                                 f"While fully flashed ({m['blind_remaining_s_at_start']:.1f}s remaining) and "
                                                 f"with no recent sound from {w.names[e]}, the crosshair followed the enemy's "
                                                 f"movement (corr {m['tracking_corr']:.2f}). Corroborating only."))
            ctx.observe(self.name + "_summary", o, kills_while_blind=n_kills, blind_ticks=int(blind.sum()))
        return events


class StrategicInformationDetector(Detector):
    """Detector 12 - broad UNAUTHORIZED_INFORMATION (radar-style) indicators.

    MVP metric: how often the crosshair points near hidden, UNKNOWN enemies
    compared to a null model where the same enemy trajectories are shifted in
    time (the player's view is kept). Aiming toward where enemies happen to
    be *right now* more than toward where they typically are at other moments
    suggests real-time information. Strongly confounded by common angles and
    spawn tendencies, so it needs large samples and carries very low weight.
    Route/rotation/utility analyses are future work (docs/detectors.md).
    """

    name = "strategic_information"
    axis = EvidenceAxis.DECISION_INFORMATION
    group = EvidenceGroup.STRATEGIC
    default_reliability = 0.1

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        near = float(cfg.get("near_deg", 5.0))
        shifts = [w.ticks_for_ms(s) for s in cfg.get("null_shifts_ms", [-12000, -8000, -5000, 5000, 8000, 12000])]
        events = []
        for o in range(w.P):
            if not ctx.analyze_player(o):
                continue
            actual, null, n = 0, [], 0
            for e in range(w.P):
                if e == o:
                    continue
                mask = (ctx.knowledge.level[o, e] == Knowledge.UNKNOWN) & (ctx.vis.los[o, e] == LOS.GEOMETRY_OCCLUDED) & w.live
                idx = np.nonzero(mask)[0]
                if idx.size == 0:
                    continue
                eye = w.eye[o, idx].astype(np.float64)
                by, bp = bearing(eye, w.eye[e, idx].astype(np.float64))
                err = angular_distance(w.pitch[o, idx], w.yaw[o, idx], bp, by)
                actual += int((err <= near).sum())
                n += idx.size
                for s in shifts:
                    j = idx + s
                    ok = (j >= 0) & (j < w.T)
                    ok[ok] &= (w.round_of[j[ok]] == w.round_of[idx[ok]]) & w.alive[e, j[ok]]
                    if not ok.any():
                        continue
                    by2, bp2 = bearing(eye[ok], w.eye[e, j[ok]].astype(np.float64))
                    err2 = angular_distance(w.pitch[o, idx[ok]], w.yaw[o, idx[ok]], bp2, by2)
                    null.append(((err2 <= near).sum(), ok.sum()))
            if n == 0:
                continue
            rate = actual / n
            null_rate = sum(a for a, _ in null) / max(1, sum(b for _, b in null))
            res = {"hidden_unknown_samples": n, "rate_near_actual": rate, "rate_near_time_shifted": null_rate,
                   "ratio": rate / null_rate if null_rate > 0 else None}
            ctx.observe(self.name, o, **res)
            if res["ratio"] is None:
                continue
            sev = ramp(res["ratio"], cfg.get("ratio_lo", 2.5), cfg.get("ratio_hi", 5.0)) * ramp(
                n, cfg.get("min_samples", 3000), cfg.get("full_samples", 10000))
            if sev >= cfg.get("min_event_severity", 0.2):
                t_all = np.nonzero(w.live & w.alive[o])[0]
                events.append(self.event(ctx, o, int(t_all[0]), int(t_all[len(t_all) // 2]), int(t_all[-1]), sev, 0.7, None,
                                         res, {"scope": "match"},
                                         f"Crosshair was within {near:.0f} deg of hidden, unknown enemies {res['ratio']:.1f}x as "
                                         f"often as expected from the same enemies' positions at other times."))
        return events
