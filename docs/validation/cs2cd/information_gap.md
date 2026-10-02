# Following hidden enemies beyond what could be known (`information_gap`), CS2CD calibration

Data: the 174 CS2CD matches on maps with a mesh in the cloud runner (de_mirage 121, de_nuke 53):
714 clean players (6 anti-aim rage cheaters from `anti_aim_clean_players.txt` left out), 600 unlabelled
players in cheater matches and 420 labelled cheaters. Mirage uses the public mesh, Nuke a game-built one.

Per player and match: **tracked** is the hidden enemies' own bearing change (degrees) during which the
crosshair stayed within 3° of the enemy's head (and within half the gap), at moments when the best
legitimate estimate of the enemy's position was at least 6° off; **share** is tracked divided by all such
movement. Event rule shipped: tracked ≥ 300° **and** share ≥ 3%.

Reproduce with `tools/calibration/information_gap_calibrate.py` (see its docstring).

| min tracked deg | min share | clean | unlabelled | cheater |
|---|---|---|---|---|
| 200 | 0.02 | 12 / 714 | 4 / 600 | 99 / 420 |
| 200 | 0.03 | 5 / 714 | 0 / 600 | 85 / 420 |
| 200 | 0.04 | 2 / 714 | 0 / 600 | 63 / 420 |
| 200 | 0.05 | 1 / 714 | 0 / 600 | 36 / 420 |
| 250 | 0.02 | 4 / 714 | 1 / 600 | 72 / 420 |
| 250 | 0.03 | 2 / 714 | 0 / 600 | 64 / 420 |
| 250 | 0.04 | 1 / 714 | 0 / 600 | 53 / 420 |
| 250 | 0.05 | 1 / 714 | 0 / 600 | 31 / 420 |
| 300 | 0.02 | 0 / 714 | 0 / 600 | 51 / 420 |
| 300 | 0.03 | 0 / 714 | 0 / 600 | 46 / 420 |
| 300 | 0.04 | 0 / 714 | 0 / 600 | 40 / 420 |
| 300 | 0.05 | 0 / 714 | 0 / 600 | 26 / 420 |
| 350 | 0.02 | 0 / 714 | 0 / 600 | 34 / 420 |
| 350 | 0.03 | 0 / 714 | 0 / 600 | 30 / 420 |
| 350 | 0.04 | 0 / 714 | 0 / 600 | 28 / 420 |
| 350 | 0.05 | 0 / 714 | 0 / 600 | 21 / 420 |
| 400 | 0.02 | 0 / 714 | 0 / 600 | 23 / 420 |
| 400 | 0.03 | 0 / 714 | 0 / 600 | 22 / 420 |
| 400 | 0.04 | 0 / 714 | 0 / 600 | 20 / 420 |
| 400 | 0.05 | 0 / 714 | 0 / 600 | 16 / 420 |

| quantile | clean tracked / share | unlabelled tracked / share | cheater tracked / share |
|---|---|---|---|
| 0.5 | 81 / 0.010 | 51 / 0.010 | 101 / 0.025 |
| 0.9 | 154 / 0.019 | 113 / 0.020 | 322 / 0.059 |
| 0.99 | 271 / 0.048 | 189 / 0.033 | 594 / 0.099 |
| 0.997 | 295 / 0.065 | 227 / 0.045 | 674 / 0.116 |
Per map at the shipped rule: Nuke 23 of 98 labelled cheaters, Mirage 23 of 322; 0 clean and 0 unlabelled
on both. The highest clean player reaches 283° at a share of 3% (and 1.8% among clean players above 300°).

Variants measured on the same matches (same rule): a sound margin of 4° instead of 6° roughly halves what
cheaters reach (their 99th percentile of tracked falls from 594° to 301°) while clean players fall less,
so 6° is kept; 10° gives the same numbers as 6° because sound-only moments already pass the 6° gap. A 2°
on-target radius separates less than 3°.

First shots (the first shot of a burst at an enemy hidden just before it, on target although the
estimate was ≥6° off): clean 99th percentile 8 shots and a share of 35%, labelled cheaters 28 and 75%.
One clean Nuke player reaches 20 of 45, so first shots only add severity when the tracking rule has
already fired.

Limitation: smokes in this data rarely leave a stable core between two players for long (bloom, fade,
shell edges and bullet holes all count as uncertain), so almost all calibrated movement is behind walls.
