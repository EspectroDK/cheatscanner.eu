# Calibration report

Empirical distributions of raw detector observations. Use these (from matches believed to be legitimate) to set thresholds; do not tune from individual suspicious examples.

Matches in corpus: **41**

## Flick (snap) peak angular velocity  (`snap.peak_velocity_deg_s`)
n=277, players=169, mean=706.4

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 330.9 | 360.4 | 391.9 | 452.2 | 571.7 | 784.5 | 1125 | 1320 | 2503 | 5812 |

Median by weapon class: grenade=648.4, knife=553.9, mg=670.6, pistol=555.6, rifle=549.5, shotgun=681.3, smg=512.6, sniper=693


## Flick peak angular jerk  (`snap.peak_jerk_deg_s3`)
n=277, players=169, mean=2.2e+05

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 2.647e+04 | 5.113e+04 | 6.745e+04 | 8.804e+04 | 1.557e+05 | 2.631e+05 | 3.902e+05 | 4.697e+05 | 1.146e+06 | 2.976e+06 |

Median by weapon class: grenade=1.702e+05, knife=2.108e+05, mg=8.066e+04, pistol=1.45e+05, rifle=1.491e+05, shotgun=1.558e+05, smg=1.621e+05, sniper=1.748e+05


## Flick overshoot  (`snap.overshoot_deg`)
n=277, players=169, mean=7.966

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 0.6611 | 2.978 | 5.855 | 9.968 | 15.06 | 21.4 | 40.36 | 86.64 |

Median by weapon class: grenade=7.519, knife=6.453, mg=2.978, pistol=6.185, rifle=5.855, shotgun=5.672, smg=7.183, sniper=4.127


## Flick landing error  (`snap.target_error_after_deg`)
n=277, players=169, mean=3.18

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0.3868 | 0.9802 | 1.34 | 2.105 | 3.41 | 4.298 | 4.626 | 4.766 | 4.968 | 4.992 |

Median by weapon class: grenade=1.996, knife=3.169, mg=2.892, pistol=3.309, rifle=3.334, shotgun=4.471, smg=3.697, sniper=3.835


## Reaction time after first visibility  (`aim_acquisition.reaction_ms`)
n=10184, players=393, mean=245.1

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 15.62 | 15.62 | 15.62 | 15.62 | 187.5 | 375 | 604.7 | 765.6 | 937.5 | 1000 |

Median by weapon class: c4=46.88, grenade=281.2, knife=93.75, mg=195.3, pistol=187.5, rifle=187.5, shotgun=156.2, smg=109.4, sniper=234.4, taser=54.69


## Time to acquire target  (`aim_acquisition.acquisition_ms`)
n=4146, players=393, mean=446.3

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 46.88 | 78.12 | 125 | 265.6 | 421.9 | 609.4 | 796.9 | 890.6 | 984.4 | 1000 |

Median by weapon class: c4=nan, grenade=500, knife=593.8, mg=242.2, pistol=421.9, rifle=421.9, shotgun=390.6, smg=406.2, sniper=437.5, taser=468.8


## Acquisition peak angular jerk  (`aim_acquisition.peak_jerk_deg_s3`)
n=12409, players=393, mean=7.821e+04

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 2153 | 7157 | 1.743e+04 | 3.935e+04 | 9.131e+04 | 1.957e+05 | 2.956e+05 | 5.381e+05 | 9.321e+05 |

Median by weapon class: c4=7.042e+04, grenade=3.109e+04, knife=7.472e+04, mg=2.65e+04, pistol=3.84e+04, rifle=4.04e+04, shotgun=4.682e+04, smg=4.541e+04, sniper=3.031e+04, taser=4.642e+04


## Corrective sub-movements  (`aim_acquisition.corrections`)
n=12409, players=393, mean=1.749

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 1 | 3 | 5 | 6 | 9 | 13 |

Median by weapon class: c4=1, grenade=2, knife=1, mg=2, pistol=1, rifle=1, shotgun=1, smg=1, sniper=1, taser=1.5


## Hidden-tracking correlation (target moving)  (`hidden_tracking.tracking_corr`)
n=64712, players=387, mean=0.2466

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| -0.658 | -0.4036 | -0.271 | -0.0352 | 0.246 | 0.5321 | 0.8037 | 0.915 | 0.985 | 0.9976 |


## Fraction of hidden time aimed within 2 deg of the enemy  (`hidden_tracking.frac_within_2deg`)
n=145549, players=393, mean=0.006002

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0.01562 | 0.1641 | 0.6072 |


## Mean aim error to hidden enemies  (`hidden_tracking.mean_error_deg`)
n=145549, players=393, mean=61.92

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 5.523 | 12.14 | 18.05 | 31.72 | 54.29 | 86.82 | 118.6 | 135.6 | 158.9 | 172.9 |


## Current-vs-2s-old position advantage  (`remembered_position.advantage_2000ms_deg`)
n=382, players=382, mean=0.1282

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| -3.432 | -1.744 | -1.142 | -0.5453 | 0.1396 | 0.7607 | 1.387 | 1.745 | 4.048 | 8.585 |


## Error 250 ms before first visibility  (`previsibility.err_-250`)
n=1907, players=340, mean=18.19

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0.6349 | 1.34 | 1.977 | 3.627 | 8.962 | 22.94 | 48.11 | 64.87 | 112.6 | 144.5 |


## Trigger time (crosshair moved onto target)  (`trigger_timing.trigger_ms`)
n=4849, players=368, mean=137.3

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0 | 15.62 | 46.88 | 109.4 | 218.8 | 312.5 | 359.4 | 406.2 | 406.2 |

Median by weapon class: mg=109.4, pistol=93.75, rifle=93.75, smg=93.75, sniper=125


## Per-player trigger-time standard deviation  (`trigger_timing_summary.std_ms`)
n=368, players=368, mean=98.36

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 10.55 | 53.96 | 86.98 | 105.9 | 119.6 | 131.2 | 137.3 | 145.8 | 154.2 |


## Spray residual / recoil amplitude  (`recoil.residual_ratio`)
n=4299, players=346, mean=0.1914

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0.05227 | 0.06569 | 0.07793 | 0.1 | 0.1417 | 0.2113 | 0.3169 | 0.4414 | 1.068 | 2.225 |

Median by weapon class: mg=0.1065, rifle=0.1365, smg=0.1712


## Recoil compensation correlation  (`recoil.compensation_corr`)
n=4297, players=346, mean=0.09128

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| -0.2099 | -0.1129 | -0.06578 | 0.01181 | 0.08873 | 0.173 | 0.2516 | 0.2997 | 0.4006 | 0.492 |

Median by weapon class: mg=0.1402, rifle=0.09203, smg=0.07164


## P(correction toward nearby visible enemy)  (`attraction.visible_near_p_toward`)
n=396, players=410, mean=0.6028

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0.3103 | 0.506 | 0.5357 | 0.5664 | 0.6043 | 0.6393 | 0.6735 | 0.7257 | 0.9041 | 1 |


## P(correction toward nearby hidden enemy)  (`attraction.hidden_near_p_toward`)
n=383, players=410, mean=0.5376

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0.3685 | 0.4534 | 0.4841 | 0.505 | 0.5331 | 0.5637 | 0.5911 | 0.6265 | 0.7928 | 1 |


## Hidden-enemy aim proximity vs time-shifted null  (`strategic_information.ratio`)
n=385, players=401, mean=0.8918

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0 | 0.5214 | 0.6256 | 0.7508 | 0.8991 | 1.014 | 1.178 | 1.283 | 1.57 | 1.728 |


## View-to-hit discrepancy at damage  (`mechanical_impossibility.discrepancy_deg`)
n=15445, players=393, mean=2.07

| p1 | p5 | p10 | p25 | p50 | p75 | p90 | p95 | p99 | p99.9 |
|---|---|---|---|---|---|---|---|---|---|
| 0.1022 | 0.23 | 0.3401 | 0.6072 | 1.121 | 2.145 | 4.009 | 5.946 | 16.57 | 41.23 |

Median by weapon class: mg=1.061, pistol=1.234, rifle=1.002, shotgun=2.156, smg=1.43, sniper=0.9339


## How often do players aim within 2 deg of a hidden, unknown opponent?
0.59% of hidden-and-unknown window time (566824 s analysed; windows overlap).
