"""Detector 3 - pre-visibility aim convergence.

Why it exists
-------------
With ESP a player can walk the crosshair onto an enemy *before* the enemy
becomes visible, converging on the enemy's real-time hidden position.

What is measured
----------------
For encounters anchored at first visibility (T=0): angular error to the
enemy at T-2000, -1500, -1000, -750, -500, -250 ms, at T=0 and at the first
shot; monotonicity of the convergence; how much of the error reduction came
from the observer moving the aim (vs the enemy walking into a held
crosshair); and whether the aim converged on the enemy's *moving* position
rather than on the static spot where it appeared.

False-positive controls
-----------------------
* **Holding / pre-aiming common angles**: pre-aim on the static appearance
  point is legitimate. ``static_spot_specificity`` compares error to the
  appearance point with error to the real-time position; converging on a
  fixed spot (specificity <= ``static_spot_lo_deg``) scores 0, and severity
  only reaches full strength at ``static_spot_hi_deg``.
* **Enemy walks into a held crosshair**: ``aim_driven_fraction`` ~0.
* **Prior knowledge**: requires the pre-window to be UNKNOWN (no sight,
  radar, teammate, sound or damage information) - otherwise skipped.
* **Population baselines** (common pre-aim spots per map cell) are not yet
  available; until they are the detector runs at reduced reliability and marks
  results ``calibrated: false``.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import spearmanr

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp
from cs2_analyzer.detectors.common import error_to_point
from cs2_analyzer.features.pair import pair_series, target_point
from cs2_analyzer.geometry.angles import angular_distance, bearing
from cs2_analyzer.knowledge.model import Knowledge

OFFSETS_MS = (-2000, -1500, -1000, -750, -500, -250, 0)


class PrevisibilityDetector(Detector):
    name = "previsibility"
    axis = EvidenceAxis.HIDDEN_INFORMATION
    group = EvidenceGroup.INFORMATION
    default_reliability = 0.4

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        kn = ctx.knowledge
        pre = w.ticks_for_ms(2000)
        events = []
        for enc in ctx.encounters:
            o, e, t0 = enc.observer, enc.target, enc.t_first_seen
            if t0 is None or not ctx.analyze_player(o):
                continue
            a = t0 - pre
            if a < 0 or w.round_of[a] != w.round_of[t0] or not w.alive[o, a:t0].all() or not w.alive[e, a:t0].all():
                continue
            lvl = kn.level[o, e, a:t0]
            unknown_frac = float(np.mean(lvl == Knowledge.UNKNOWN))
            last_second = kn.level[o, e, t0 - w.ticks_for_ms(1000):t0]
            if np.any((last_second == Knowledge.KNOWN) | (last_second == Knowledge.LIKELY_KNOWN)):
                continue
            if unknown_frac < cfg.get("min_unknown_fraction", 0.75):
                continue
            ps = pair_series(w, o, e, a, t0)
            err = ps.err
            idx = ps.t
            p0 = target_point(w, e, np.array([t0]))[0]
            err_static = error_to_point(w, o, idx, np.repeat(p0[None], len(idx), axis=0))
            # Per-tick decomposition of the error change into the part caused by the
            # observer's aim movement (target held fixed) and the part caused by the
            # target moving (aim held fixed).
            tgt = target_point(w, e, idx)
            by_t, bp_t = bearing(w.eye[o, idx].astype(np.float64), tgt)
            err_prev_aim = angular_distance(w.pitch[o, idx - 1], w.yaw[o, idx - 1], bp_t, by_t)  # aim(t-1), target(t)
            red_aim = np.nan_to_num(err_prev_aim - err)  # >0: aim movement reduced the error
            red_tgt = np.nan_to_num(np.r_[np.nan, err[:-1]] - err_prev_aim)  # >0: target moved toward crosshair
            at = {}
            for off in OFFSETS_MS:
                k = len(idx) - 1 + w.ticks_for_ms(off)
                at[off] = float(err[k]) if 0 <= k < len(err) else None
            shot_err = None
            if enc.t_first_shot is not None and enc.t_first_shot >= t0:
                shot_err = float(pair_series(w, o, e, enc.t_first_shot, enc.t_first_shot).err[0])
            samples = [at[o_] for o_ in OFFSETS_MS[:-1] if at[o_] is not None]
            mono = float(spearmanr(range(len(samples)), samples).statistic) if len(samples) >= 4 else float("nan")
            k250 = len(idx) - 1 - w.ticks_for_ms(250)
            total_red = at[-2000] - at[-250] if at[-2000] is not None and at[-250] is not None else float("nan")
            ra, rt = float(red_aim[1:k250 + 1].sum()), float(red_tgt[1:k250 + 1].sum())
            aim_frac = ra / (abs(ra) + abs(rt)) if (abs(ra) + abs(rt)) > 0.5 else float("nan")
            # specificity over the convergence phase (T-1500..T-250): positive when the aim
            # was closer to the moving hidden position than to the fixed appearance spot
            k1500 = max(0, len(idx) - 1 - w.ticks_for_ms(1500))
            spec = float(np.nanmean(err_static[k1500:k250 + 1] - err[k1500:k250 + 1]))
            m = {
                "error_at_offsets_deg": {str(k): v for k, v in at.items()},
                "error_at_first_shot_deg": shot_err,
                "monotonic_spearman": mono,
                "total_reduction_deg": total_red,
                "aim_driven_fraction": aim_frac,
                "error_reduction_by_aim_deg": ra,
                "error_reduction_by_target_motion_deg": rt,
                "static_spot_specificity_deg": spec,
                "target_angular_path_deg": float(np.nansum(np.abs(ps.d_bearing_target_yaw))),
                "unknown_fraction_prewindow": unknown_frac,
                "distance_at_t0_u": float(ps.distance[-1]),
                "calibrated": ctx.baselines is not None and ctx.baselines.has("previsibility.error_minus250"),
            }
            ctx.observe(self.name, o, target_steam_id=ctx.sid(e), tick_first_seen=w.tick(t0),
                        **{k: v for k, v in m.items() if not isinstance(v, dict)},
                        **{f"err_{k}": v for k, v in at.items()})
            sev = (
                ramp(at[-2000] or 0, cfg.get("start_error_lo_deg", 6.0), cfg.get("start_error_hi_deg", 15.0))
                * ramp(-(at[-250] if at[-250] is not None else 99), -cfg.get("end_error_hi_deg", 4.0), -cfg.get("end_error_lo_deg", 1.0))
                * ramp(-mono, 0.6, 0.95)
                * ramp(aim_frac, 0.3, 0.8)
                # converging on the fixed spot where the enemy appeared (spec <= 0) is a pre-aim, not
                # hidden information, so it contributes nothing (it used to keep 40% of the severity)
                * ramp(spec, cfg.get("static_spot_lo_deg", 0.0), cfg.get("static_spot_hi_deg", 4.0))
                * (0.5 + 0.5 * ramp(m["target_angular_path_deg"], 2.0, 10.0))
            )
            if not m["calibrated"]:
                sev *= float(cfg.get("uncalibrated_severity_factor", 0.8))
            if sev < cfg.get("min_event_severity", 0.25):
                continue
            conv = " -> ".join(f"{at[k]:.1f}" for k in OFFSETS_MS if at[k] is not None)
            expl = (
                f"Before {w.names[e]} became visible, the crosshair converged on the enemy's hidden real-time position "
                f"({conv} deg at T-2000..T0), {aim_frac * 100:.0f}% of the reduction was the player's own aim movement, "
                f"and it tracked the moving position {spec:.1f} deg better than the static spot where the enemy appeared. "
                f"No legitimate information was modelled for {unknown_frac * 100:.0f}% of the prior 2 s."
            )
            info_conf = float(np.mean(kn.info_confidence[o, e, a:t0].astype(np.float64)))
            events.append(self.event(ctx, o, a, t0, enc.t_end, sev, info_conf, e, m,
                                     {"encounter_id": enc.id, "anchor": "FIRST_SEEN"}, expl))
        return events
