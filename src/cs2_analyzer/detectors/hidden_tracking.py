"""Detector 1 - hidden-player tracking (and Detector 10 - smoke tracking).

Why it exists
-------------
A player with unauthorized real-time information (ESP/wallhack) tends to keep
the crosshair on an enemy they cannot see and to *follow that enemy's
movement*. This is the most direct behavioral trace of hidden information.

What is measured
----------------
For windows where the enemy is occluded (by geometry, or by smoke for the
smoke variant) and the knowledge model says the observer has NO legitimate
information (UNKNOWN), we compare the observer's per-tick view change with
the change of the enemy's bearing *caused by the enemy's own movement*
(``features/pair.py``). The observer-induced part is excluded.

False-positive controls
-----------------------
* **Observer moving, enemy stationary**: target-induced bearing change is ~0,
  so there is nothing to correlate with; windows with little target motion
  get zero severity.
* **Memory / last-known position**: error to the last known position is
  computed; aiming at where the enemy was last seen is legitimate and lowers
  severity.
* **Coincidental sweeps**: correlation must be with *this* enemy - the
  specificity term compares against other hidden enemies at the same time -
  and direction reversals of the enemy must be matched by the aim.
* **Uncertain knowledge**: any KNOWN/LIKELY/POSSIBLY sample (sight, radar,
  teammates, sound, damage, round start, planted-bomb area) is excluded, and
  UNKNOWN visibility (occlusion-edge margin) never counts as hidden.
* One window is never proof: the event carries severity; aggregation requires
  repetition across incidents.

Thresholds are UNCALIBRATED placeholders (config ``detectors.hidden_tracking``).
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp
from cs2_analyzer.detectors.common import (
    HIDDEN_GEOMETRY,
    HIDDEN_SMOKE,
    error_to_point,
    hidden_mask,
    last_known_positions,
    runs,
    tracking_metrics,
    windows,
)
from cs2_analyzer.features.pair import pair_series


class HiddenTrackingDetector(Detector):
    name = "hidden_tracking"
    axis = EvidenceAxis.HIDDEN_INFORMATION
    group = EvidenceGroup.INFORMATION
    default_reliability = 0.7
    los_codes = HIDDEN_GEOMETRY
    label = "Hidden tracking"

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        min_len = w.ticks_for_ms(cfg.get("min_window_ms", 750))
        max_len = w.ticks_for_ms(cfg.get("max_window_ms", 4000))
        step = w.ticks_for_ms(cfg.get("window_step_ms", 2000))
        events: list[EvidenceEvent] = []
        for o in range(w.P):
            if not ctx.analyze_player(o):
                continue
            for e in range(w.P):
                if o == e:
                    continue
                mask = hidden_mask(ctx, o, e, self.los_codes) & w.live
                if not mask.any():
                    continue
                for a, b in runs(mask):
                    if b - a + 1 < min_len:
                        continue
                    best = None
                    for wa, wb in windows(a, b, max_len, step):
                        res = self._window(ctx, cfg, o, e, wa, wb)
                        ctx.observe(self.name, o, target_steam_id=ctx.sid(e), round=int(w.round_of[wa]),
                                    tick_start=w.tick(wa), tick_end=w.tick(wb), **res["metrics"],
                                    severity=res["severity"])
                        if best is None or res["severity"] > best["severity"]:
                            best = res
                    if best and best["severity"] >= cfg.get("min_event_severity", 0.25):
                        events.append(self._make_event(ctx, o, e, best))
        return events

    def _window(self, ctx, cfg, o, e, a, b) -> dict:
        w = ctx.world
        dt = w.dt
        ps = pair_series(w, o, e, a, b)
        m = tracking_metrics(
            ps, dt,
            smooth_n=int(cfg.get("smooth_ticks", 5)),
            max_lag=w.ticks_for_ms(cfg.get("max_reaction_lag_ms", 250)),
            min_target_speed_deg_s=float(cfg.get("min_target_angular_speed_deg_s", 4.0)),
        )
        # last-known-position control
        lk, has_lk = last_known_positions(ctx, o, e, ps.t)
        err_lk = error_to_point(w, o, ps.t, lk)
        m["mean_error_to_last_known_deg"] = float(np.nanmean(err_lk)) if has_lk.any() else None
        m["last_known_available"] = bool(has_lk.any())
        m["target_moved_from_last_known_u"] = (
            float(np.nanmean(np.linalg.norm(lk - w.eye[e, ps.t].astype(np.float64), axis=-1))) if has_lk.any() else None
        )
        # Cheap pre-gate: the expensive controls below only matter for windows
        # that could produce evidence at all.
        plausible = (
            m["moving_ticks"] >= w.ticks_for_ms(cfg.get("min_moving_ms", 300))
            and np.isfinite(m["tracking_corr"])
            and m["mean_error_deg"] <= cfg.get("mean_error_hi_deg", 12.0)
        )
        # specificity vs other hidden enemies in the same window
        others = []
        for e2 in (range(w.P) if plausible else []):
            if e2 in (o, e):
                continue
            hm = hidden_mask(ctx, o, e2, self.los_codes)[a : b + 1]
            if hm.mean() < 0.8:
                continue
            ps2 = pair_series(w, o, e2, a, b)
            m2 = tracking_metrics(ps2, dt, max_lag=w.ticks_for_ms(cfg.get("max_reaction_lag_ms", 250)))
            if np.isfinite(m2["tracking_corr"]) and m2["target_angular_path_deg"] >= 2:
                others.append(m2["tracking_corr"])
        m["other_hidden_enemies_compared"] = len(others)
        m["max_corr_other_hidden_enemy"] = max(others) if others else None
        spec = (m["tracking_corr"] - max(others)) if others and np.isfinite(m["tracking_corr"]) else None
        m["specificity"] = spec
        kn = ctx.knowledge
        info_conf = float(np.mean(kn.info_confidence[o, e, a : b + 1].astype(np.float64)))
        last_info = kn.ms_since(kn.last_info, o, e, a)
        m["ms_since_last_information_at_start"] = last_info
        m["ms_since_last_seen_at_start"] = kn.ms_since(kn.last_seen, o, e, a)
        m["teammate_saw_target_ticks"] = int(kn.team_seen_now[o, e, a : b + 1].sum())

        # severity components (UNCALIBRATED)
        corr_s = ramp(m["tracking_corr"], cfg.get("corr_lo", 0.55), cfg.get("corr_hi", 0.9))
        path_s = ramp(m["target_angular_path_deg"], cfg.get("target_path_lo_deg", 3.0), cfg.get("target_path_hi_deg", 12.0))
        err_s = ramp(-m["mean_error_deg"], -cfg.get("mean_error_hi_deg", 12.0), -cfg.get("mean_error_lo_deg", 3.0))
        gain_ok = 1.0 if (np.isfinite(m["tracking_gain"]) and 0.4 <= m["tracking_gain"] <= 1.8) else 0.5
        rev_f = 0.5 + 0.5 * ramp(m["matched_reversals"], 0, 2)
        spec_f = 0.75 if spec is None else 0.5 + 0.5 * ramp(spec, 0.0, 0.4)
        if m["mean_error_to_last_known_deg"] is not None and (m["target_moved_from_last_known_u"] or 0) > 100:
            lk_f = 0.6 + 0.4 * ramp(m["mean_error_to_last_known_deg"] - m["mean_error_deg"], 0.0, 6.0)
        else:
            lk_f = 0.8
        severity = corr_s * path_s * err_s * gain_ok * rev_f * spec_f * lk_f if plausible else 0.0
        m["severity_components"] = {"correlation": corr_s, "target_motion": path_s, "error": err_s, "gain": gain_ok,
                                    "reversals": rev_f, "specificity": spec_f, "last_known": lk_f}
        peak = a + int(np.nanargmin(ps.err)) if np.isfinite(ps.err).any() else a
        return {"a": a, "b": b, "peak": peak, "metrics": m, "severity": float(severity), "info_conf": info_conf}

    def _make_event(self, ctx, o, e, r) -> EvidenceEvent:
        m = r["metrics"]
        since = m["ms_since_last_information_at_start"]
        since_txt = "at any point this round" if since is None else f"for {since / 1000:.1f}s"
        expl = (
            f"{self.label}: enemy {ctx.world.names[e]} was occluded ({'smoke' if self.los_codes == HIDDEN_SMOKE else 'geometry'}) "
            f"and not known by sight, teammates, radar, sound or damage {since_txt}. Over {m['duration_ms'] / 1000:.1f}s the enemy's own "
            f"movement shifted its bearing by {m['target_angular_path_deg']:.1f} deg and the crosshair followed with "
            f"correlation {m['tracking_corr']:.2f} (lag {m['tracking_lag_ms']:.0f} ms), mean error {m['mean_error_deg']:.1f} deg, "
            f"{m['matched_reversals']}/{m['target_reversals']} direction reversals matched."
        )
        return self.event(
            ctx, o, r["a"], r["peak"], r["b"], r["severity"], r["info_conf"], e, m,
            {"occlusion": "smoke" if self.los_codes == HIDDEN_SMOKE else "geometry", "knowledge": "UNKNOWN"},
            expl,
        )


class SmokeTrackingDetector(HiddenTrackingDetector):
    """Detector 10: same measurement restricted to smoke-occluded windows.

    A kill through smoke is NOT evidence by itself; only sustained, specific
    tracking of a moving enemy behind a stable smoke core with no other
    information source is. Smoke edges, bloom/fade and HE-cleared smokes are
    modelled as uncertain and never count as hidden.
    """

    name = "smoke_tracking"
    los_codes = HIDDEN_SMOKE
    label = "Smoke tracking"
    default_reliability = 0.55
