"""Shot-level detectors: trigger timing (8), recoil compensation (9),
mechanical impossibility (20).

All three start at LOW reliability. Their primary output is empirical
distributions (observations) for calibration; evidence events are emitted
only for patterns that would be implausible even for elite players, and only
at match level (never from a single shot).
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp
from cs2_analyzer.detectors.common import error_to_point, lagged_corr, runs
from cs2_analyzer.features.pair import angular_radius, pair_series, target_point
from cs2_analyzer.features.weapons import weapon_class
from cs2_analyzer.geometry.angles import angle_between_vectors, pearson, view_vector, yaw_delta
from cs2_analyzer.geometry.visibility import LOS

AUTOMATIC_CLASSES = {"rifle", "smg", "mg"}


class TriggerTimingDetector(Detector):
    """Detector 8 - time from crosshair-on-target to shot.

    T_trigger = T_fire - T_intersection, where intersection is the first tick
    the crosshair enters the hittable region of a visible enemy. Excluded /
    separately classified: active sprays (shot in previous 300 ms), blind
    players, shotguns, "tight holds" (crosshair already within 3x the hittable
    radius for 500 ms: the enemy walked into a held angle) and prefires (shot
    before visibility). Tick resolution is 15.6 ms and sub-tick timing is not
    recorded, which bounds what this detector can resolve.
    """

    name = "trigger_timing"
    axis = EvidenceAxis.SHOT_TIMING
    group = EvidenceGroup.TIMING
    default_reliability = 0.2

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        dt_ms = w.dt * 1000
        max_wait = w.ticks_for_ms(cfg.get("max_wait_ms", 400))
        spray_gap = w.ticks_for_ms(cfg.get("spray_exclusion_ms", 300))
        hold = w.ticks_for_ms(cfg.get("tight_hold_ms", 500))
        events = []
        for o in range(w.P):
            if not ctx.analyze_player(o):
                continue
            samples = []
            for e in range(w.P):
                if e == o:
                    continue
                valid = w.live & w.alive[o] & w.alive[e] & w.is_enemy(o, e)
                if not valid.any():
                    continue
                visible = valid & (ctx.vis.los[o, e] == LOS.DIRECT_VISIBLE)
                idx = np.nonzero(visible)[0]
                if idx.size == 0:
                    continue
                ps = pair_series(w, o, e, int(idx[0]), int(idx[-1]))
                off = int(idx[0])
                err_head = ps.err
                chest = w.body_points(e, ps.t)["chest"]
                err_chest = error_to_point(w, o, ps.t, chest)
                r_head = angular_radius(ps.distance, cfg.get("head_radius_u", 4.5))
                r_body = angular_radius(ps.distance, cfg.get("body_radius_u", 9.0))
                hittable = visible[ps.t] & ((err_head <= r_head) | (err_chest <= r_body))
                entries = np.nonzero(hittable & ~np.r_[False, hittable[:-1]])[0]
                for k in entries:
                    t = off + int(k)
                    if not visible[max(0, t - 1)]:
                        cls = "appeared_under_crosshair"
                    elif w.shot_mask[o, max(0, t - spray_gap):t].any():
                        cls = "spray"
                    elif w.flash_remaining[o, t] > 0.5:
                        cls = "blind"
                    elif weapon_class(w.weapon[o, t]) == "shotgun":
                        cls = "shotgun"
                    else:
                        pre = slice(max(0, k - hold), k)
                        near_before = np.nanmax(err_head[pre] / np.maximum(r_head[pre], 0.1)) if k > 0 else np.inf
                        cls = "tight_hold" if near_before <= 3.0 else "crosshair_moved"
                    shots = np.nonzero(w.shot_mask[o, t:t + max_wait + 1])[0]
                    if shots.size == 0:
                        continue
                    trig = shots[0] * dt_ms
                    samples.append({"class": cls, "trigger_ms": float(trig), "tick": w.tick(t), "target": ctx.sid(e),
                                    "weapon": w.weapon[o, t]})
                    ctx.observe(self.name, o, target_steam_id=ctx.sid(e), tick=w.tick(t), trigger_class=cls,
                                trigger_ms=float(trig), weapon=w.weapon[o, t], distance_u=float(ps.distance[k]))
            main = np.array([s["trigger_ms"] for s in samples if s["class"] == "crosshair_moved"])
            if main.size == 0:
                continue
            res = {
                "n": int(main.size),
                "median_ms": float(np.median(main)),
                "std_ms": float(np.std(main)),
                "p10_ms": float(np.percentile(main, 10)),
                "p25_ms": float(np.percentile(main, 25)),
                "p75_ms": float(np.percentile(main, 75)),
                "frac_lt_50ms": float(np.mean(main < 50)),
                "frac_lt_75ms": float(np.mean(main < 75)),
                "frac_lt_100ms": float(np.mean(main < 100)),
                "tick_resolution_ms": dt_ms,
                "excluded_by_class": {c: sum(1 for s in samples if s["class"] == c) for c in
                                      ("spray", "blind", "shotgun", "tight_hold", "appeared_under_crosshair")},
            }
            ctx.observe(self.name + "_summary", o, **{k: v for k, v in res.items() if not isinstance(v, dict)})
            sev = (
                ramp(res["n"], cfg.get("min_samples", 8), cfg.get("full_samples", 25))
                * ramp(res["frac_lt_75ms"], cfg.get("frac_fast_lo", 0.5), cfg.get("frac_fast_hi", 0.85))
                * ramp(-res["std_ms"], -cfg.get("std_hi_ms", 60.0), -cfg.get("std_lo_ms", 20.0))
            )
            if sev >= cfg.get("min_event_severity", 0.2):
                fast = sorted([s for s in samples if s["class"] == "crosshair_moved"], key=lambda s: s["trigger_ms"])[:5]
                t_first = w.t(min(s["tick"] for s in samples))
                t_last = w.t(max(s["tick"] for s in samples))
                events.append(self.event(ctx, o, t_first, w.t(fast[0]["tick"]), t_last, sev, 1.0, None, res,
                                         {"scope": "match", "fastest_samples": fast},
                                         f"{res['frac_lt_75ms'] * 100:.0f}% of {res['n']} crosshair-on-target shots fired within "
                                         f"75 ms (median {res['median_ms']:.0f} ms, std {res['std_ms']:.0f} ms). Low-confidence "
                                         f"signal: tick resolution is {dt_ms:.1f} ms."))
        return events


class RecoilDetector(Detector):
    """Detector 9 - recoil compensation during sprays.

    The bullet direction is ``view + 2 * aim_punch`` (verified on real demos).
    A player compensates by moving the view opposite to ``2 * Δpunch``. Good
    recoil control is NOT suspicious; what is measured is how exact, how
    low-latency and how repeatable the compensation is, per weapon.
    """

    name = "recoil"
    axis = EvidenceAxis.RECOIL
    group = EvidenceGroup.RECOIL
    default_reliability = 0.25

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        gap = w.ticks_for_ms(cfg.get("max_shot_gap_ms", 160))
        min_shots = int(cfg.get("min_spray_shots", 5))
        events = []
        if np.isnan(w.punch_pitch).all():
            return events  # no aim punch in this demo: recoil cannot be measured
        for o in range(w.P):
            if not ctx.analyze_player(o):
                continue
            shots = w.shot_ticks.get(o, np.array([], dtype=int))
            if shots.size < min_shots:
                continue
            sprays, cur = [], [int(shots[0])]
            for t in shots[1:]:
                if t - cur[-1] <= gap and w.weapon[o, t] == w.weapon[o, cur[-1]]:
                    cur.append(int(t))
                else:
                    sprays.append(cur)
                    cur = [int(t)]
            sprays.append(cur)
            rows = []
            for sp in sprays:
                if len(sp) < min_shots or weapon_class(w.weapon[o, sp[0]]) not in AUTOMATIC_CLASSES:
                    continue
                a, b = sp[1], sp[-1]
                sl = slice(a, b + 1)
                prev = slice(a - 1, b)
                dview_p = w.pitch[o, sl] - w.pitch[o, prev]
                dview_y = yaw_delta(w.yaw[o, sl], w.yaw[o, prev])
                drec_p = 2 * (w.punch_pitch[o, sl] - w.punch_pitch[o, prev])
                drec_y = 2 * (w.punch_yaw[o, sl] - w.punch_yaw[o, prev])
                comp = np.r_[-dview_p, -dview_y]
                rec = np.r_[drec_p, drec_y]
                corr = pearson(comp, rec)
                lc, lag = lagged_corr(-dview_p, drec_p, max_lag=6, min_n=4)
                denom = float(np.nansum(rec ** 2))
                slope = float(np.nansum(comp * rec) / denom) if denom > 0 else float("nan")
                bullet_p = w.pitch[o, sl] + 2 * w.punch_pitch[o, sl]
                bullet_y = w.yaw[o, sl] + 2 * w.punch_yaw[o, sl]
                recoil_amp = float(np.nanmax(np.abs(2 * w.punch_pitch[o, sl])))
                row = {
                    "weapon": w.weapon[o, sp[0]],
                    "shots": len(sp),
                    "tick": w.tick(sp[0]),
                    "compensation_corr": corr,
                    "compensation_lag_ms": lag * w.dt * 1000,
                    "compensation_lagged_corr": lc,
                    "compensation_slope": slope,
                    "bullet_pitch_std_deg": float(np.nanstd(bullet_p)),
                    "bullet_yaw_std_deg": float(np.nanstd(yaw_delta(bullet_y, np.nanmean(bullet_y)))),
                    "recoil_amplitude_deg": recoil_amp,
                    "residual_ratio": float(np.nanstd(bullet_p) / recoil_amp) if recoil_amp > 0 else None,
                }
                rows.append(row)
                ctx.observe(self.name, o, **row)
            if not rows:
                continue
            ratios = np.array([r["residual_ratio"] for r in rows if r["residual_ratio"] is not None])
            corrs = np.array([r["compensation_corr"] for r in rows if np.isfinite(r["compensation_corr"])])
            slopes = np.array([r["compensation_slope"] for r in rows if np.isfinite(r["compensation_slope"])])
            res = {
                "sprays": len(rows),
                "median_residual_ratio": float(np.median(ratios)) if ratios.size else None,
                "median_corr": float(np.median(corrs)) if corrs.size else None,
                "slope_std": float(np.std(slopes)) if slopes.size > 1 else None,
                "median_slope": float(np.median(slopes)) if slopes.size else None,
            }
            sev = (
                ramp(len(rows), cfg.get("min_sprays", 5), cfg.get("full_sprays", 15))
                * ramp(res["median_corr"] or 0, cfg.get("corr_lo", 0.9), cfg.get("corr_hi", 0.98))
                * ramp(-(res["median_residual_ratio"] if res["median_residual_ratio"] is not None else 9), -0.15, -0.05)
                * ramp(-(res["slope_std"] if res["slope_std"] is not None else 9), -0.15, -0.04)
            )
            if sev >= cfg.get("min_event_severity", 0.2):
                best = min(rows, key=lambda r: r["residual_ratio"] if r["residual_ratio"] is not None else 9)
                t0 = w.t(min(r["tick"] for r in rows))
                t1 = w.t(max(r["tick"] for r in rows))
                events.append(self.event(ctx, o, t0, w.t(best["tick"]), t1, sev, 1.0, None, res,
                                         {"scope": "match", "sprays": rows[:20]},
                                         f"{len(rows)} sprays compensated recoil with median correlation "
                                         f"{res['median_corr']:.2f} and residual {res['median_residual_ratio']:.2f} of the "
                                         f"recoil amplitude, with slope spread {res['slope_std']:.3f} between sprays."))
        return events


class MechanicalImpossibilityDetector(Detector):
    """Collect view-to-hit discrepancies at damage events (spec section 20).

    For every damage event we compare the bullet direction at the shot
    (``view + 2 * punch``; ``fire_bullets`` angles when present) with the
    direction to the approximate hit location. The purpose of the initial
    implementation is to *characterize* legitimate discrepancies (spread,
    movement inaccuracy, lag compensation, missing sub-tick data, no hitbox
    data). No hard threshold is configured, so no events are produced until
    ``detectors.mechanical_impossibility.threshold_deg`` is set from data.
    """

    name = "mechanical_impossibility"
    axis = EvidenceAxis.IMPOSSIBLE_MECHANICS
    group = EvidenceGroup.IMPOSSIBLE
    default_reliability = 0.2

    HITGROUP_POINT = {"head": "head", "neck": "head", "chest": "chest", "stomach": "pelvis", "left_arm": "chest",
                      "right_arm": "chest", "left_leg": "knee", "right_leg": "knee", "generic": "chest"}

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        hurts = ctx.demo.event("hurts")
        bullets = ctx.demo.event("bullets")
        bmap = {}
        if len(bullets):
            for r in bullets.itertuples():
                bmap[(int(r.steam_id), int(r.tick))] = (r.angle_pitch, r.angle_yaw)
        threshold = cfg.get("threshold_deg")
        events = []
        if not len(hurts):
            return events
        for r in hurts.itertuples():
            o = w.index_of.get(int(r.attacker_steam_id))
            e = w.index_of.get(int(r.victim_steam_id))
            t = int(r.tick) - w.tick0
            if o is None or e is None or o == e or not (0 <= t < w.T) or not ctx.analyze_player(o):
                continue
            if weapon_class(r.weapon) not in {"rifle", "smg", "mg", "pistol", "sniper", "shotgun"}:
                continue
            st = w.shot_ticks.get(o, np.array([], dtype=int))
            cand = st[(st <= t) & (st >= t - w.ticks_for_ms(150))]
            if cand.size == 0:
                continue
            ts = int(cand[-1])
            src = "view+2punch"
            if (ctx.sid(o), w.tick(ts)) in bmap:
                bp, by = bmap[(ctx.sid(o), w.tick(ts))]
                src = "fire_bullets"
            else:
                bp = w.pitch[o, ts] + 2 * np.nan_to_num(w.punch_pitch[o, ts])
                by = w.yaw[o, ts] + 2 * np.nan_to_num(w.punch_yaw[o, ts])
            point = w.body_points(e, np.array([ts]))[self.HITGROUP_POINT.get(str(r.hitgroup), "chest")][0]
            d = point - w.eye[o, ts].astype(np.float64)
            disc = float(angle_between_vectors(view_vector(bp, by), d))
            dist = float(np.linalg.norm(d))
            row = {"tick": int(r.tick), "target_steam_id": ctx.sid(e), "hitgroup": str(r.hitgroup), "weapon": str(r.weapon),
                   "discrepancy_deg": disc, "distance_u": dist, "angle_source": src,
                   "attacker_speed": float(np.nan_to_num(w.speed2d[o, ts])), "shot_to_damage_ticks": t - ts}
            ctx.observe(self.name, o, **row)
            if threshold is not None and disc >= float(threshold):
                events.append(self.event(ctx, o, ts, ts, t, ramp(disc, float(threshold), float(threshold) * 2), 1.0, e, row, {},
                                         f"Damage registered {disc:.1f} deg away from the bullet direction."))
        return events
