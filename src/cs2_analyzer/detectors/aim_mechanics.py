"""Aim-mechanics detectors: acquisition (4), snaps (5), attraction (6), target switching (7).

Why they exist
--------------
Aimbots produce movements whose *shape* differs from human motor control:
very short latency, missing corrective sub-movements, no overshoot, landing
exactly on a hitbox, and unnatural consistency. Humans also produce extreme
flicks, so these detectors mainly record **distributions** (observations)
and only emit weak, low-reliability events for rare combinations. A single
flick is never evidence; the match aggregation requires repetition, and
population/player baselines (tools/calibration) are the intended comparison.

Data caveat: CS2 records view angles once per tick (64 Hz). Sub-tick input
timing is not in demos, so velocities/accelerations/jerks are finite
differences at 15.6 ms resolution and only comparable between observations
processed identically.
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp
from cs2_analyzer.detectors.common import runs
from cs2_analyzer.features.pair import angular_radius, pair_series
from cs2_analyzer.geometry.angles import angular_distance, derivatives, smooth
from cs2_analyzer.geometry.visibility import LOS
from cs2_analyzer.knowledge.model import Knowledge


def _kinematics(speed: np.ndarray, dt: float) -> tuple[np.ndarray, np.ndarray]:
    s = smooth(np.nan_to_num(speed), 3)
    acc = np.gradient(s, dt) if s.size > 1 else np.zeros_like(s)
    jerk = np.gradient(smooth(acc, 3), dt) if s.size > 1 else np.zeros_like(s)
    return acc, jerk


def _submovements(speed: np.ndarray, rel: float = 0.3) -> int:
    s = smooth(np.nan_to_num(speed), 3)
    if s.size < 3 or s.max() <= 0:
        return 0
    thr = rel * s.max()
    peaks = [i for i in range(1, len(s) - 1) if s[i] >= s[i - 1] and s[i] > s[i + 1] and s[i] >= thr]
    return len(peaks)


def _hit_after(ctx, o, e, t, window_ticks):
    hurts = ctx.demo.event("hurts")
    if not len(hurts):
        return False, False
    w = ctx.world
    m = hurts[(hurts["attacker_steam_id"] == ctx.sid(o)) & (hurts["victim_steam_id"] == ctx.sid(e))
              & (hurts["tick"] >= w.tick(t)) & (hurts["tick"] <= w.tick(t + window_ticks))]
    return bool(len(m)), bool((m["hitgroup"] == "head").any()) if len(m) else False


class AimAcquisitionDetector(Detector):
    name = "aim_acquisition"
    axis = EvidenceAxis.AIM_MECHANICS
    group = EvidenceGroup.AIM
    default_reliability = 0.3

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        dt = w.dt
        seen = ctx.vis.seen()
        events = []
        for enc in ctx.encounters:
            o, e, t0 = enc.observer, enc.target, enc.t_first_seen
            if t0 is None or not ctx.analyze_player(o):
                continue
            t1 = min(enc.t_end, t0 + w.ticks_for_ms(cfg.get("window_ms", 1000)))
            if t1 - t0 < 8 or seen[o, e, t0:t0 + 10].mean() < 0.7:
                continue
            ps = pair_series(w, o, e, t0, t1)
            err = ps.err
            if not np.isfinite(err[0]) or err[0] < cfg.get("min_initial_error_deg", 2.0):
                continue
            radius = np.maximum(angular_radius(ps.distance, cfg.get("target_radius_u", 6.0)), cfg.get("min_acquire_deg", 1.0))
            acq = np.nonzero(err <= radius)[0]
            acq_i = int(acq[0]) if acq.size else None
            speed = np.nan_to_num(ps.aim_speed)
            dec = np.r_[False, np.diff(err) < 0]
            react = np.nonzero((speed >= cfg.get("reaction_speed_deg_s", 30.0)) & dec)[0]
            react_i = int(react[0]) if react.size else None
            end = acq_i if acq_i is not None else len(err) - 1
            seg = slice(react_i or 0, end + 1)
            acc, jerk = _kinematics(speed, dt)
            # overshoot: signed error along the initial error direction, after acquisition
            u = np.array([ps.err_h[0], ps.err_v[0]])
            u = u / max(np.linalg.norm(u), 1e-9)
            proj = ps.err_h * u[0] + ps.err_v * u[1]
            after = proj[end:min(len(proj), end + w.ticks_for_ms(300))]
            overshoot = float(max(0.0, -np.nanmin(after))) if after.size else 0.0
            shots = np.nonzero(w.shot_mask[o, ps.t])[0]
            shot_after = shots[shots >= end] if acq_i is not None else np.array([])
            m = {
                "initial_error_deg": float(err[0]),
                "reaction_ms": None if react_i is None else react_i * dt * 1000,
                "acquisition_ms": None if acq_i is None else acq_i * dt * 1000,
                "peak_velocity_deg_s": float(np.nanmax(speed[seg])) if speed[seg].size else 0.0,
                "peak_acceleration_deg_s2": float(np.nanmax(np.abs(acc[seg]))) if acc[seg].size else 0.0,
                "peak_jerk_deg_s3": float(np.nanmax(np.abs(jerk[seg]))) if jerk[seg].size else 0.0,
                "corrections": max(0, _submovements(speed[seg]) - 1),
                "overshoot_deg": overshoot,
                "landing_error_deg": float(err[end]),
                "landing_to_shot_ms": float((shot_after[0] - end) * dt * 1000) if shot_after.size else None,
                "distance_u": float(ps.distance[0]),
                "weapon": w.weapon[o, t0],
            }
            ctx.observe(self.name, o, target_steam_id=ctx.sid(e), tick=w.tick(t0), **m)
            if acq_i is None or react_i is None:
                continue
            sev = (
                ramp(m["initial_error_deg"], cfg.get("big_error_lo_deg", 15.0), cfg.get("big_error_hi_deg", 35.0))
                * ramp(-m["reaction_ms"], -cfg.get("reaction_hi_ms", 110.0), -cfg.get("reaction_lo_ms", 50.0))
                * ramp(-m["acquisition_ms"], -cfg.get("acquisition_hi_ms", 200.0), -cfg.get("acquisition_lo_ms", 110.0))
                * (1.0 if m["corrections"] == 0 else 0.3)
                * ramp(-m["landing_error_deg"], -1.0, -0.3)
            )
            if sev >= cfg.get("min_event_severity", 0.3):
                events.append(self.event(
                    ctx, o, t0, t0 + acq_i, t1, sev, 1.0, e, m, {"encounter_id": enc.id},
                    f"Acquired {w.names[e]} from {m['initial_error_deg']:.0f} deg in {m['acquisition_ms']:.0f} ms after first "
                    f"visibility (movement began after {m['reaction_ms']:.0f} ms) with no corrective sub-movement and "
                    f"{m['landing_error_deg']:.2f} deg landing error. Fast acquisitions happen legitimately; only repetition matters.",
                ))
        return events


class SnapDetector(Detector):
    name = "snap"
    axis = EvidenceAxis.AIM_MECHANICS
    group = EvidenceGroup.AIM
    default_reliability = 0.35

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        dt = w.dt
        events = []
        for o in range(w.P):
            if not ctx.analyze_player(o):
                continue
            speed = np.full(w.T, np.nan)
            speed[1:] = angular_distance(w.pitch[o, 1:], w.yaw[o, 1:], w.pitch[o, :-1], w.yaw[o, :-1]) / dt
            valid = w.live & w.alive[o] & np.r_[False, w.alive[o, :-1]]
            fast = valid & (np.nan_to_num(speed) >= cfg.get("min_speed_deg_s", 300.0))
            for a, b in runs(fast):
                s = a - 1
                if s < 0 or b + 1 >= w.T:
                    continue
                angle = float(angular_distance(w.pitch[o, s], w.yaw[o, s], w.pitch[o, b], w.yaw[o, b]))
                dur = (b - s) * dt * 1000
                if angle < cfg.get("min_angle_deg", 10.0) or dur > cfg.get("max_duration_ms", 250.0):
                    continue
                # nearest visible enemy at the end of the snap
                best, best_err = None, 1e9
                for e in range(w.P):
                    if e == o or not w.is_enemy(o, e, b) or not w.alive[e, b]:
                        continue
                    if ctx.vis.los[o, e, b] not in (LOS.DIRECT_VISIBLE, LOS.VISIBLE_THROUGH_SMOKE):
                        continue
                    er = float(pair_series(w, o, e, b, b).err[0])
                    if er < best_err:
                        best, best_err = e, er
                if best is None or best_err > cfg.get("max_end_error_consider_deg", 5.0):
                    continue
                e = best
                post = min(w.T - 1, b + w.ticks_for_ms(250))
                ps = pair_series(w, o, e, s, post)
                n_snap = b - s
                d = np.array([ps.err_h[0], ps.err_v[0]])
                d = d / max(np.linalg.norm(d), 1e-9)
                proj = ps.err_h * d[0] + ps.err_v * d[1]
                overshoot = float(max(0.0, -np.nanmin(proj[n_snap:])))
                post_speed = np.nan_to_num(ps.aim_speed[n_snap + 1:])
                corrections = _submovements(post_speed) if post_speed.size and post_speed.max() > 30 else 0
                acc, jerk = _kinematics(np.nan_to_num(ps.aim_speed[: n_snap + 1]), dt)
                shots = np.nonzero(w.shot_mask[o, s:post + 1])[0]
                shot_delay = float((shots[0] - n_snap) * dt * 1000) if shots.size else None
                hit, hs = _hit_after(ctx, o, e, s + (shots[0] if shots.size else n_snap), w.ticks_for_ms(150)) if shots.size else (False, False)
                radius = float(angular_radius(ps.distance[n_snap], cfg.get("target_radius_u", 5.0)))
                m = {
                    "angle_travelled_deg": angle,
                    "duration_ms": dur,
                    "peak_velocity_deg_s": float(np.nanmax(speed[a:b + 1])),
                    "peak_acceleration_deg_s2": float(np.nanmax(np.abs(acc))) if acc.size else 0.0,
                    "peak_jerk_deg_s3": float(np.nanmax(np.abs(jerk))) if jerk.size else 0.0,
                    "target_error_before_deg": float(ps.err[0]),
                    "target_error_after_deg": float(ps.err[n_snap]),
                    "target_angular_radius_deg": radius,
                    "overshoot_deg": overshoot,
                    "correction_count": int(corrections),
                    "shot_delay_ms": shot_delay,
                    "hit": hit,
                    "headshot": hs,
                    "distance_u": float(ps.distance[n_snap]),
                    "weapon": w.weapon[o, b],
                }
                ctx.observe(self.name, o, target_steam_id=ctx.sid(e), tick=w.tick(b), **m)
                sev = (
                    ramp(angle, cfg.get("angle_lo_deg", 20.0), cfg.get("angle_hi_deg", 60.0))
                    * ramp(-dur, -cfg.get("duration_hi_ms", 150.0), -cfg.get("duration_lo_ms", 60.0))
                    * ramp(-(m["target_error_after_deg"] - radius), -1.0, 0.0)
                    * (1.0 if overshoot <= cfg.get("max_overshoot_deg", 0.4) else 0.3)
                    * (1.0 if corrections == 0 else 0.4)
                    * (1.0 if shot_delay is not None and 0 <= shot_delay <= cfg.get("max_shot_delay_ms", 50.0) else 0.3)
                )
                if sev >= cfg.get("min_event_severity", 0.3):
                    events.append(self.event(
                        ctx, o, s, b, post, sev, 1.0, e, m, {},
                        f"Snap of {angle:.0f} deg in {dur:.0f} ms ended {m['target_error_after_deg']:.2f} deg from "
                        f"{w.names[e]} (angular radius {radius:.2f}), overshoot {overshoot:.2f} deg, {corrections} "
                        f"corrections, shot after {shot_delay} ms. Individual flicks are normal; only repetition matters.",
                    ))
        return events


class AttractionDetector(Detector):
    """Detector 6 - soft aim / target attraction. Corroborating only.

    R(t) = err(t-1) - err(t): positive when a view movement reduced the error to
    the nearest enemy. Distributions are compared between visible-near,
    hidden-near and far/no-target conditions. Humans naturally move toward
    visible enemies near the crosshair, so evidence requires a population
    baseline (none by default -> observations only), except for a strong,
    large-sample bias toward *hidden, unknown* enemies which is reported with
    low reliability as information evidence.
    """

    name = "attraction"
    axis = EvidenceAxis.AIM_MECHANICS
    group = EvidenceGroup.AIM
    default_reliability = 0.2

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        dt = w.dt
        near = float(cfg.get("near_deg", 8.0))
        far = float(cfg.get("far_deg", 30.0))
        lo, hi = float(cfg.get("min_move_deg_s", 2.0)), float(cfg.get("max_move_deg_s", 120.0))
        events = []
        for o in range(w.P):
            if not ctx.analyze_player(o):
                continue
            recent_shot = np.convolve(w.shot_mask[o].astype(float), np.ones(w.ticks_for_ms(300)), mode="full")[: w.T] > 0
            base = w.live & w.alive[o] & ~recent_shot & (w.flash_remaining[o] <= 0)
            stats = {c: {"n": 0, "toward": 0, "red": 0.0, "move": 0.0, "ticks": []} for c in ("visible_near", "hidden_near", "far")}
            idx_all = np.nonzero(base)[0]
            if idx_all.size < 10:
                continue
            best_err = np.full(w.T, np.inf)
            best_prev = np.full(w.T, np.inf)
            best_cls = np.zeros(w.T, dtype=np.int8)  # 1 visible, 2 hidden-unknown
            for e in range(w.P):
                if e == o:
                    continue
                vm = w.alive[e] & w.is_enemy(o, e)
                if not vm.any():
                    continue
                ps = pair_series(w, o, e, 0, w.T - 1)
                err = np.where(vm, ps.err, np.inf)
                prev = np.r_[np.inf, err[:-1]]
                los = ctx.vis.los[o, e]
                cls = np.where(np.isin(los, (LOS.DIRECT_VISIBLE,)), 1,
                               np.where((ctx.knowledge.level[o, e] == Knowledge.UNKNOWN) & (los == LOS.GEOMETRY_OCCLUDED), 2, 0))
                better = err < best_err
                best_err = np.where(better, err, best_err)
                best_prev = np.where(better, prev, best_prev)
                best_cls = np.where(better, cls, best_cls)
            move = np.full(w.T, np.nan)
            move[1:] = angular_distance(w.pitch[o, 1:], w.yaw[o, 1:], w.pitch[o, :-1], w.yaw[o, :-1])
            spd = move / dt
            sel = base & np.isfinite(best_err) & np.isfinite(best_prev) & (spd >= lo) & (spd <= hi)
            with np.errstate(invalid="ignore"):
                R = best_prev - best_err
            for cname, cm in (
                ("visible_near", sel & (best_cls == 1) & (best_err <= near)),
                ("hidden_near", sel & (best_cls == 2) & (best_err <= near)),
                ("far", sel & (best_err > near) & (best_err <= far)),
            ):
                st = stats[cname]
                st["n"] = int(cm.sum())
                st["toward"] = int((R[cm] > 0).sum())
                st["red"] = float(np.nansum(R[cm]))
                st["move"] = float(np.nansum(move[cm]))
            res = {}
            for cname, st in stats.items():
                res[f"{cname}_n"] = st["n"]
                res[f"{cname}_p_toward"] = st["toward"] / st["n"] if st["n"] else None
                res[f"{cname}_reduction_per_degree"] = st["red"] / st["move"] if st["move"] > 0 else None
            ctx.observe(self.name, o, **res)
            hn = res["hidden_near_n"]
            p = res["hidden_near_p_toward"]
            if hn >= cfg.get("min_hidden_samples", 400) and p is not None:
                # binomial z against 0.5 with a design effect for autocorrelation
                deff = float(cfg.get("autocorrelation_design_effect", 10.0))
                z = (p - 0.5) / np.sqrt(0.25 / (hn / deff))
                sev = ramp(z, cfg.get("hidden_z_lo", 3.0), cfg.get("hidden_z_hi", 6.0)) * ramp(p, 0.6, 0.75)
                if sev >= cfg.get("min_event_severity", 0.2):
                    t = int(idx_all[len(idx_all) // 2])
                    ev = self.event(ctx, o, int(idx_all[0]), t, int(idx_all[-1]), sev, 0.8, None, {**res, "z": z},
                                    {"scope": "match"},
                                    f"Small aim corrections moved toward hidden, unknown enemies {p * 100:.0f}% of the time "
                                    f"(n={hn}, autocorrelation-adjusted z={z:.1f}). Corroborating only.")
                    ev.evidence_axis = EvidenceAxis.HIDDEN_INFORMATION.value
                    ev.evidence_group = EvidenceGroup.INFORMATION.value
                    events.append(ev)
        return events


class TargetSwitchDetector(Detector):
    name = "target_switch"
    axis = EvidenceAxis.AIM_MECHANICS
    group = EvidenceGroup.AIM
    default_reliability = 0.25

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        dt = w.dt
        seen = ctx.vis.seen()
        deaths = ctx.demo.event("deaths")
        events = []
        if not len(deaths):
            return events
        for r in deaths.itertuples():
            o = w.index_of.get(int(r.attacker_steam_id))
            v = w.index_of.get(int(r.victim_steam_id))
            tk = int(r.tick) - w.tick0
            if o is None or v is None or o == v or not (0 <= tk < w.T) or not ctx.analyze_player(o):
                continue
            if not w.alive[o, tk] or w.team[o, tk] == w.team[v, tk]:
                continue
            horizon = min(w.T - 1, tk + w.ticks_for_ms(cfg.get("horizon_ms", 1500)))
            for e2 in range(w.P):
                if e2 in (o, v) or not w.is_enemy(o, e2, tk) or not w.alive[e2, tk]:
                    continue
                vis_idx = np.nonzero(seen[o, e2, tk:horizon + 1])[0]
                if vis_idx.size == 0:
                    continue
                ts = tk + int(vis_idx[0])
                ps = pair_series(w, o, e2, ts, horizon)
                radius = np.maximum(angular_radius(ps.distance, 6.0), 1.0)
                acq = np.nonzero(ps.err <= radius)[0]
                if acq.size == 0 or ps.err[0] < cfg.get("min_initial_error_deg", 5.0):
                    continue
                k = int(acq[0])
                direct = float(angular_distance(w.pitch[o, ts], w.yaw[o, ts], w.pitch[o, ts + k], w.yaw[o, ts + k]))
                path = float(np.nansum(np.abs(ps.aim_speed[1:k + 1])) * dt) if k > 0 else 0.0
                shots = np.nonzero(w.shot_mask[o, ts + k:horizon + 1])[0]
                m = {
                    "ms_since_previous_kill": (ts - tk) * dt * 1000,
                    "initial_error_deg": float(ps.err[0]),
                    "acquisition_ms": k * dt * 1000,
                    "path_efficiency": direct / path if path > 0 else None,
                    "landing_error_deg": float(ps.err[k]),
                    "delay_to_fire_ms": float(shots[0] * dt * 1000) if shots.size else None,
                    "distance_between_targets_u": float(np.linalg.norm(w.pos[v, tk] - w.pos[e2, tk])),
                }
                ctx.observe(self.name, o, target_steam_id=ctx.sid(e2), tick=w.tick(ts), **m)
                sev = (
                    ramp(m["initial_error_deg"], cfg.get("angle_lo_deg", 20.0), cfg.get("angle_hi_deg", 50.0))
                    * ramp(-m["acquisition_ms"], -cfg.get("acq_hi_ms", 180.0), -cfg.get("acq_lo_ms", 90.0))
                    * ramp(m["path_efficiency"] or 0, 0.9, 0.98)
                    * ramp(-m["landing_error_deg"], -1.0, -0.3)
                )
                if sev >= cfg.get("min_event_severity", 0.3):
                    events.append(self.event(ctx, o, tk, ts + k, horizon, sev, 1.0, e2, m, {"previous_kill_tick": int(r.tick)},
                                             f"After killing {w.names[v]}, acquired {w.names[e2]} {m['initial_error_deg']:.0f} deg "
                                             f"away in {m['acquisition_ms']:.0f} ms with path efficiency "
                                             f"{m['path_efficiency']:.2f}. Transfers are common; only consistency matters."))
        return events
