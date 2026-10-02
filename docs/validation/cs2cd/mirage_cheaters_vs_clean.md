# CS2CD: clean players vs labelled cheaters

| group | players | matches | INSUFFICIENT_DATA | NORMAL | ELEVATED | HIGH | VERY_HIGH | mean score | events/player |
|---|---|---|---|---|---|---|---|---|---|
| clean | 410 | 41 | 11.2% | 86.6% | 1.7% | 0.0% | 0.5% | 0.029 | 0.10 |
| unlabelled | 478 | 79 | 25.3% | 73.8% | 0.6% | 0.2% | 0.0% | 0.011 | 0.06 |
| cheater | 322 | 80 | 19.9% | 49.1% | 28.6% | 1.9% | 0.6% | 0.161 | 0.17 |

## Score separation (AUC, labelled cheater vs clean player)

| score | AUC | AUC same-match | clean p50 / p95 | cheater p50 / p95 |
|---|---|---|---|---|
| overall | 0.721 | 0.747 | 0 / 0.169 | 0.0478 / 0.45 |
| events | 0.523 | 0.536 | 0 / 1 | 0 / 1 |
| axis.AIM_MECHANICS | 0.492 | 0.496 | 0 / 0 | 0 / 0 |
| axis.HIDDEN_INFORMATION | 0.504 | 0.505 | 0 / 0 | 0 / 0.075 |
| axis.SHOT_TIMING | 0.500 | 0.500 | 0 / 0 | 0 / 0 |
| axis.RECOIL | 0.500 | 0.500 | 0 / 0 | 0 / 0 |
| axis.IMPOSSIBLE_MECHANICS | 0.533 | 0.540 | 0 / 0 | 0 / 0.32 |
| axis.DECISION_INFORMATION | 0.502 | 0.502 | 0 / 0 | 0 / 0 |

## Raw detector metrics, per-player medians

Players with at least 5 observations of the metric. AUC > 0.5 means labelled cheaters have higher values.

| metric | clean n / p50 / p95 | unlabelled n / p50 | cheater n / p50 / p95 | AUC | AUC same-match |
|---|---|---|---|---|---|
| Flick (snap) peak angular velocity (`snap.peak_velocity_deg_s`) | 2 / 768.1 / 868.1 | 1 / 593.9 | 2 / 2006 / 3153 | n/a | n/a |
| Flick peak angular jerk (`snap.peak_jerk_deg_s3`) | 2 / 2.489e+05 / 3.326e+05 | 1 / 2.391e+05 | 2 / 9.287e+05 / 1.604e+06 | n/a | n/a |
| Flick overshoot (`snap.overshoot_deg`) | 2 / 4.625 / 5.584 | 1 / 3.655 | 2 / 3.69 / 6.404 | n/a | n/a |
| Flick landing error (`snap.target_error_after_deg`) | 2 / 3.518 / 3.838 | 1 / 3.67 | 2 / 2.767 / 4.032 | n/a | n/a |
| Reaction time after first visibility (`aim_acquisition.reaction_ms`) | 357 / 187.5 / 329.7 | 354 / 199.2 | 178 / 128.9 / 321.5 | 0.383 | 0.369 |
| Time to acquire target (`aim_acquisition.acquisition_ms`) | 313 / 421.9 / 578.1 | 255 / 421.9 | 105 / 328.1 / 571.9 | 0.292 | 0.290 |
| Acquisition peak angular jerk (`aim_acquisition.peak_jerk_deg_s3`) | 360 / 3.857e+04 / 8.289e+04 | 394 / 3.351e+04 | 206 / 2.905e+04 / 8.234e+04 | 0.355 | 0.448 |
| Corrective sub-movements (`aim_acquisition.corrections`) | 360 / 1 / 2 | 394 / 1 | 206 / 1 / 3 | 0.466 | 0.582 |
| Hidden-tracking correlation (target moving) (`hidden_tracking.tracking_corr`) | 376 / 0.2527 / 0.3474 | 432 / 0.2402 | 291 / 0.2406 / 0.4695 | 0.462 | 0.509 |
| Fraction of hidden time aimed within 2 deg of the enemy (`hidden_tracking.frac_within_2deg`) | 388 / 0 / 0 | 454 / 0 | 308 / 0 / 0 | 0.500 | 0.499 |
| Mean aim error to hidden enemies (`hidden_tracking.mean_error_deg`) | 388 / 54.08 / 75.25 | 454 / 54.37 | 308 / 44.13 / 74.44 | 0.289 | 0.311 |
| Current-vs-2s-old position advantage (`remembered_position.advantage_2000ms_deg`) | 0 / - / - | 0 / - | 0 / - / - | n/a | n/a |
| Error 250 ms before first visibility (`previsibility.err_-250`) | 203 / 9.053 / 26.63 | 108 / 8.13 | 50 / 5.252 / 17.45 | 0.279 | 0.320 |
| Trigger time (crosshair moved onto target) (`trigger_timing.trigger_ms`) | 326 / 105.5 / 218.8 | 271 / 93.75 | 101 / 101.6 / 195.3 | 0.465 | 0.492 |
| Per-player trigger-time standard deviation (`trigger_timing_summary.std_ms`) | 0 / - / - | 0 / - | 0 / - / - | n/a | n/a |
| Spray residual / recoil amplitude (`recoil.residual_ratio`) | 287 / 0.1413 / 0.2345 | 212 / 0.1351 | 65 / 0.155 / 0.252 | 0.563 | 0.611 |
| Recoil compensation correlation (`recoil.compensation_corr`) | 287 / 0.08197 / 0.1724 | 212 / 0.09703 | 64 / 0.08045 / 0.1928 | 0.460 | 0.420 |
| P(correction toward nearby visible enemy) (`attraction.visible_near_p_toward`) | 0 / - / - | 0 / - | 0 / - / - | n/a | n/a |
| P(correction toward nearby hidden enemy) (`attraction.hidden_near_p_toward`) | 0 / - / - | 0 / - | 0 / - / - | n/a | n/a |
| Hidden-enemy aim proximity vs time-shifted null (`strategic_information.ratio`) | 0 / - / - | 0 / - | 0 / - / - | n/a | n/a |
| View-to-hit discrepancy at damage (`mechanical_impossibility.discrepancy_deg`) | 369 / 1.121 / 2.385 | 333 / 1.085 | 294 / 1.865 / 6.607 | 0.684 | 0.706 |

## Combined clean-population percentile

Mean oriented percentile over `hidden_tracking.mean_error_deg`, `mechanical_impossibility.discrepancy_deg`, `recoil.residual_ratio` (players with at least 2 of them, 5+ observations each). Clean players are scored leave-one-match-out.

AUC labelled cheater vs clean: 0.739; vs same-match unlabelled: 0.755

| cutoff | clean (n=365) | unlabelled (n=335) | cheater (n=290) | pro (n=150) |
|---|---|---|---|---|
| >= 0.8 | 6.0% | 5.7% | 43.4% | 1.3% |
| >= 0.85 | 4.1% | 3.0% | 37.9% | 1.3% |
| >= 0.9 | 2.7% | 2.1% | 30.0% | 0.0% |
| >= 0.95 | 2.2% | 0.9% | 20.3% | 0.0% |

## Player evidence over the match (cross-fitted)

Sum over `hidden_tracking.mean_error_deg`, `hidden_tracking.frac_within_2deg`, `mechanical_impossibility.discrepancy_deg`, `trigger_timing.trigger_ms`, `aim_acquisition.peak_jerk_deg_s3` of the mean log-likelihood ratio times min(n, 30), plus the ratio of the per-player `input_lattice_summary.lattice_fit`. Players excluded from the clean side: 6.

AUC labelled cheater vs clean: 0.828; vs same-match unlabelled: 0.794

| | clean (n=387) | unlabelled (n=456) | cheater (n=314) | pro (n=140) |
|---|---|---|---|---|
| above clean p95 | 3.9% | 2.2% | 44.9% | 0.7% |
| above clean p98 | 1.6% | 0.4% | 23.6% | 0.7% |
| above clean p99.5 | 0.5% | 0.0% | 4.1% | 0.0% |
| ELEVATED or above, events only | 0.3% | 0.4% | 7.3% | n/a |
| ELEVATED or above, with profile | 1.8% | 0.9% | 30.6% | n/a |
| HIGH or above, with profile | 0.0% | 0.2% | 3.5% | n/a |
