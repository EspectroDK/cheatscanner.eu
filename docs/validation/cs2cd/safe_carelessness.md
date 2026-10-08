# `safe_carelessness` calibration (CS2CD)

434 CS2CD matches on the five maps with meshes (de_nuke, de_mirage, de_dust2, de_inferno, de_ancient):
1,818 clean players, 1,419 unlabelled players in cheater matches and 1,013 labelled cheaters (6 rage
cheaters excluded as in the other notes). Run on 2026-10-08 with the detector itself
(`build_analysis(..., detector_names=["safe_carelessness"])`), median 28 s per match for the whole
pipeline; the detector adds about a second.

## What is measured

Every 0.5 s (from 2 s after freeze end) while the player is alive, sees no enemy and has no legitimate
estimate within 15 deg of any enemy within 2,500 u, the moment is kept. At each kept moment: is the knife
or bomb the active weapon, and is a hidden, unknown enemy within 800 u (*threat*)? The ghost threat
weight of the moment is the share of up to 6 other rounds with the same side in which an enemy at the
same round time stood within 800 u of the player's real eye, hidden behind geometry (head and chest).

* `real` = knife share in safe moments - knife share in threat moments
* `expected` = the same difference with the moments weighted by ghost threat and ghost safety
* `excess` = real - expected; `z` = excess / its standard error (4 neighbouring samples count as one)

A player qualifies with at least 20 threat moments, 50 safe moments, a summed ghost threat weight of 5
and a ghost safety weight of 20. The event fires on **excess >= 0.4 AND z >= 2.5**.

## Result

| group | players | qualified | fired |
|---|---|---|---|
| clean | 1,818 | 854 | 2 |
| unlabelled (cheater matches) | 1,419 | 231 | 1 |
| labelled cheaters | 1,013 | 141 | 11 |

AUC (qualified cheaters vs qualified clean players): 0.71 on excess, 0.70 on z. The 11 cheaters are in 11
different matches on four maps (Dust2 4, Mirage 3, Nuke 2, Inferno 2). 8 of them are caught by neither
`information_gap` nor `hidden_fire`; together the three detectors now cover 176 of the 1,013 labelled
cheaters (168 before).

The three non-cheaters it fires for hold the knife 35-84% of the time when it is safe and never with a
hidden enemy near, over 20-39 threat moments:

| match | map | group | threat moments | excess | z |
|---|---|---|---|---|---|
| no_cheater_present_43 | de_mirage | clean | 39 | 0.46 | 2.98 |
| no_cheater_present_444 | de_nuke | clean | 20 | 0.50 | 2.61 |
| with_cheater_present_87 | de_inferno | unlabelled | 21 | 0.63 | 2.86 |

## Checks

* **Not a knife habit.** Within each quarter of overall knife use the AUC on excess stays 0.66-0.75.
  Cheaters hold the knife or bomb in 25% of the moments where enemies usually are (ghost threat), clean
  players in 11%; with a real hidden enemy near, cheaters drop to 10%.
* **Not a match artefact.** Unlabelled players in cheater matches score like clean players (AUC 0.51).
* **Per map** (AUC on excess): Ancient 0.77, Mirage 0.74, Inferno 0.72, Dust2 0.63, Nuke 0.62.
* **Radius.** 1,200 u lets more players qualify (449 cheaters) but separates less (AUC 0.66); 1,600 u
  0.62. 800 u is the default.
* **Running loudly** (not walking, faster than 150 u/s) in the same moments does not separate (AUC
  0.54-0.65; many clean players run when it happens to be safe) and is not used.
* **Coverage limit.** Most players never have 20 moments with a hidden, unknown enemy within 800 u: 141
  of 1,013 cheaters qualify. A per-player rate that adds up across matches would lift that.

## Stricter lines

| rule | clean | unlabelled | cheaters | new |
|---|---|---|---|---|
| excess >= 0.4 and z >= 2.5 (default) | 2/854 | 1/231 | 11/141 | 8 |
| excess >= 0.4 and z >= 3.0 | 0/854 | 0/231 | 5/141 | 3 |
| excess >= 0.45 and z >= 3.0 | 0/854 | 0/231 | 4/141 | 3 |
