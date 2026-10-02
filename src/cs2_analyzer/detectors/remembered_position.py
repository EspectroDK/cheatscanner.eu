"""Detector 2 - current position versus remembered position.

Why it exists
-------------
Legitimate aim at a hidden enemy is based on memory, callouts or prediction,
so it should correlate with where the enemy *was* (last seen, 0.25-2 s ago)
at least as well as with where the enemy *is*. Real-time unauthorized
information produces aim that is systematically closer to the enemy's
*current* hidden position than to any of its recent past positions.

What is measured
----------------
Over all hidden + UNKNOWN samples of a player (all enemies, whole match),
angular error to the enemy's current head position and to its positions
250/500/1000/2000 ms earlier. Only samples where the enemy actually moved
(otherwise all positions coincide) and the crosshair is roughly in that
direction are used.

Temporal autocorrelation
------------------------
Consecutive ticks are not independent. Samples are grouped into blocks
(default 1 s per enemy/hidden run); statistics use block means and a block
bootstrap confidence interval, and the evidence requires a minimum number of
blocks. Individual samples are never scored.
"""

from __future__ import annotations

import numpy as np

from cs2_analyzer.detectors.base import AnalysisContext, Detector, EvidenceAxis, EvidenceEvent, EvidenceGroup, ramp
from cs2_analyzer.detectors.common import HIDDEN_GEOMETRY, HIDDEN_SMOKE, error_to_point, hidden_mask, runs
from cs2_analyzer.features.pair import target_point

LAGS_MS = (250, 500, 1000, 2000)


class RememberedPositionDetector(Detector):
    name = "remembered_position"
    axis = EvidenceAxis.HIDDEN_INFORMATION
    group = EvidenceGroup.INFORMATION
    default_reliability = 0.55

    def analyze(self, ctx: AnalysisContext) -> list[EvidenceEvent]:
        cfg = ctx.cfg(self.name)
        w = ctx.world
        block = w.ticks_for_ms(cfg.get("block_ms", 1000))
        min_disp = float(cfg.get("min_target_displacement_u", 150.0))
        max_err = float(cfg.get("max_error_deg", 25.0))
        rng = np.random.default_rng(int(cfg.get("bootstrap_seed", 7)))
        events = []
        for o in range(w.P):
            if not ctx.analyze_player(o):
                continue
            blocks = []  # (e, a, b, mean_err_current, {lag: mean_err_lag})
            for e in range(w.P):
                if e == o:
                    continue
                mask = hidden_mask(ctx, o, e, HIDDEN_GEOMETRY + HIDDEN_SMOKE) & w.live
                for a, b in runs(mask):
                    for s in range(a, b + 1, block):
                        idx = np.arange(s, min(b, s + block - 1) + 1)
                        lag_max = w.ticks_for_ms(max(LAGS_MS))
                        idx = idx[idx - lag_max >= 0]
                        idx = idx[(w.round_of[idx - lag_max] == w.round_of[idx]) & w.alive[e, idx - lag_max]]
                        if idx.size < block // 2:
                            continue
                        cur = target_point(w, e, idx)
                        disp = np.linalg.norm(cur - target_point(w, e, idx - lag_max), axis=-1)
                        keep = disp >= min_disp
                        if keep.sum() < block // 3:
                            continue
                        idx, cur = idx[keep], cur[keep]
                        err_c = error_to_point(w, o, idx, cur)
                        lag_err = {}
                        for lag in LAGS_MS:
                            n = w.ticks_for_ms(lag)
                            lag_err[lag] = error_to_point(w, o, idx, target_point(w, e, idx - n))
                        near = np.minimum(err_c, lag_err[max(LAGS_MS)]) <= max_err
                        if near.sum() < block // 3:
                            continue
                        blocks.append((e, int(idx[0]), int(idx[-1]), float(np.mean(err_c[near])),
                                       {lag: float(np.mean(v[near])) for lag, v in lag_err.items()}))
            if not blocks:
                continue
            cur = np.array([b[3] for b in blocks])
            res = {"n_blocks": len(blocks), "mean_error_current_deg": float(cur.mean())}
            for lag in LAGS_MS:
                arr = np.array([b[4][lag] for b in blocks])
                adv = arr - cur  # positive: aim closer to CURRENT than to past position
                boots = [adv[rng.integers(0, len(adv), len(adv))].mean() for _ in range(int(cfg.get("bootstrap_n", 500)))]
                res[f"mean_error_{lag}ms_deg"] = float(arr.mean())
                res[f"advantage_{lag}ms_deg"] = float(adv.mean())
                res[f"advantage_{lag}ms_ci90"] = [float(np.percentile(boots, 5)), float(np.percentile(boots, 95))]
            ctx.observe(self.name, o, **{k: v for k, v in res.items() if not isinstance(v, list)})

            ci_low = res["advantage_2000ms_ci90"][0]
            sev = ramp(ci_low, cfg.get("ci_low_lo_deg", 1.5), cfg.get("ci_low_hi_deg", 6.0)) * ramp(
                len(blocks), cfg.get("min_blocks", 20), cfg.get("full_blocks", 60)
            ) * ramp(-res["mean_error_current_deg"], -cfg.get("max_mean_error_deg", 12.0), -4.0)
            if sev < cfg.get("min_event_severity", 0.2):
                continue
            adv_by_block = sorted(blocks, key=lambda b: b[4][2000] - b[3], reverse=True)[:5]
            top = [
                {"target_steam_id": ctx.sid(b[0]), "tick_start": w.tick(b[1]), "tick_end": w.tick(b[2]),
                 "err_current_deg": b[3], "err_2000ms_deg": b[4][2000]}
                for b in adv_by_block
            ]
            peak = adv_by_block[0]
            info_conf = float(np.mean([ctx.knowledge.info_confidence[o, b[0], b[1]] for b in blocks]))
            expl = (
                f"Across {len(blocks)} one-second blocks while enemies were hidden and unknown, the crosshair was on average "
                f"{res['advantage_2000ms_deg']:.1f} deg closer to the enemies' CURRENT positions than to where they were 2 s "
                f"earlier (90% block-bootstrap CI {ci_low:.1f}..{res['advantage_2000ms_ci90'][1]:.1f}). Memory-based aim "
                f"normally favours past positions."
            )
            events.append(self.event(ctx, o, min(b[1] for b in blocks), peak[1], max(b[2] for b in blocks), sev,
                                     info_conf, None, res, {"top_blocks": top, "scope": "match"}, expl))
        return events
