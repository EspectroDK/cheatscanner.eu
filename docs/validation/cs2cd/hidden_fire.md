# `hidden_fire` calibration (CS2CD)

## Burst rule (de_mirage + de_nuke)

174 CS2CD matches with map meshes (Nuke: the game-built mesh; Mirage: the public AtomicBool mesh),
produced with `tools/calibration/hidden_fire_calibrate.py`. Groups: 714 players in clean matches (the 6
anti-aim rage cheaters in `anti_aim_clean_players.txt` left out), 600 unlabelled players in cheater
matches, 420 labelled cheaters.

The detector needs both an **excess** of precise blind bursts over the ghost-enemy expectation and a
number of **hidden hits** (see `src/cs2_analyzer/detectors/hidden_fire.py`). The default is excess >= 3 and
>= 10 hidden hits.

The one clean player it fires for (match `no_cheater_present_88`, Gold Nova I) has 16 precise blind
bursts where 2.2 are expected, and 6 of his 16 kills went through walls. He may be an unlabelled cheater,
but that is not confirmed and he is counted as clean.

Reference case (2026-10-05): a Nuke matchmaking wallhacker who damaged enemies through walls but waited for
them to become visible before the kill had 0 events before this detector; he has 6 precise blind bursts
where 2.0 are expected, and 12 hidden hits, and his match class becomes ELEVATED. Nobody else in that match
has an excess above 0.7.

| min excess | min hidden hits | clean | unlabelled | cheater | cheaters not caught by information_gap |
|---|---|---|---|---|---|
| 2 | 6 | 4 / 714 | 0 / 600 | 62 / 420 | 39 |
| 2 | 8 | 4 / 714 | 0 / 600 | 53 / 420 | 34 |
| 2 | 10 | 2 / 714 | 0 / 600 | 43 / 420 | 28 |
| 2 | 12 | 1 / 714 | 0 / 600 | 40 / 420 | 26 |
| 3 | 6 | 2 / 714 | 0 / 600 | 49 / 420 | 27 |
| 3 | 8 | 2 / 714 | 0 / 600 | 41 / 420 | 22 |
| 3 | 10 | 1 / 714 | 0 / 600 | 32 / 420 | 17 |
| 3 | 12 | 0 / 714 | 0 / 600 | 31 / 420 | 17 |
| 4 | 6 | 1 / 714 | 0 / 600 | 33 / 420 | 15 |
| 4 | 8 | 1 / 714 | 0 / 600 | 29 / 420 | 13 |
| 4 | 10 | 1 / 714 | 0 / 600 | 23 / 420 | 11 |
| 4 | 12 | 0 / 714 | 0 / 600 | 22 / 420 | 11 |

| quantile | clean excess / hidden hits | unlabelled excess / hidden hits | cheater excess / hidden hits |
|---|---|---|---|
| 0.5 | -0.17 / 0 | 0.00 / 0 | 0.30 / 3 |
| 0.9 | 0.83 / 3 | 0.76 / 1 | 4.85 / 17 |
| 0.99 | 2.81 / 9 | 2.33 / 5 | 14.52 / 36 |
| 0.997 | 3.22 / 12 | 4.24 / 7 | 20.58 / 44 |

Labelled cheaters with an event: information_gap 46, hidden_fire 32, either 63 of 420.

## Share rule (five maps)

434 CS2CD matches on de_nuke, de_mirage, de_dust2, de_inferno and de_ancient (2026-10-06), 1823 players in
clean matches, 1448 unlabelled players in cheater matches, 1022 labelled cheaters. The **hidden share** is
hits on enemies hidden behind a wall or smoke the tick before, divided by all gun hits in the match; a hidden
hit is **without information** when the best legitimate estimate of the enemy's position is >= 10 deg off.
The default is share >= 0.3, >= 30 hits and >= 2 hidden hits without information.

Two of the three clean players it fires for are on the same clean-split Ancient match (`no_cheater_present_241`,
players 5 and 9) with 656 and 1873 damage through walls and 4 and 7 excess precise bursts; the burst rule fires
for them as well. The third (`no_cheater_present_242`, player 1, Nuke) has 29 of 31 hits hidden. All three are
data artifacts of those two CS2CD matches rather than cheating or mesh errors: their "hidden" hits are AWP and
Scout hits at full damage over 1000-2600 units from standing spots that the mesh encloses within a few hundred
units in every direction, and several victims' recorded poses do not change for 60-80 s while alive. Positions in
those parquet files are stale, which a demo parsed from a real `.dem` does not produce. The detector now skips hits
while either player's pose has not changed for 2 s (`stale_pose_ms`) and hits at >= 85% of the weapon's undamaged
damage for the hit group (`full_damage_share`, a bullet that went through something always loses damage); the
numbers below were produced before those guards and are the conservative ones.

| min share | min hits | min hidden hits without information | clean | unlabelled | cheater | cheaters not caught by information_gap or the burst rule |
|---|---|---|---|---|---|---|
| 0.25 | 20 | 0 | 15 / 1823 | 5 / 1448 | 214 / 1022 | 128 |
| 0.25 | 20 | 2 | 9 / 1823 | 1 / 1448 | 175 / 1022 | 101 |
| 0.25 | 20 | 4 | 6 / 1823 | 1 / 1448 | 114 / 1022 | 64 |
| 0.25 | 30 | 0 | 9 / 1823 | 2 / 1448 | 102 / 1022 | 55 |
| 0.25 | 30 | 2 | 4 / 1823 | 0 / 1448 | 91 / 1022 | 47 |
| 0.25 | 30 | 4 | 4 / 1823 | 0 / 1448 | 67 / 1022 | 33 |
| 0.25 | 40 | 0 | 2 / 1823 | 2 / 1448 | 58 / 1022 | 24 |
| 0.25 | 40 | 2 | 1 / 1823 | 0 / 1448 | 55 / 1022 | 22 |
| 0.25 | 40 | 4 | 1 / 1823 | 0 / 1448 | 45 / 1022 | 20 |
| 0.3 | 20 | 0 | 10 / 1823 | 1 / 1448 | 184 / 1022 | 105 |
| 0.3 | 20 | 2 | 8 / 1823 | 1 / 1448 | 158 / 1022 | 89 |
| 0.3 | 20 | 4 | 5 / 1823 | 1 / 1448 | 107 / 1022 | 59 |
| 0.3 | 30 | 0 | 5 / 1823 | 0 / 1448 | 87 / 1022 | 44 |
| 0.3 | 30 | 2 | 3 / 1823 | 0 / 1448 | 80 / 1022 | 40 |
| 0.3 | 30 | 4 | 3 / 1823 | 0 / 1448 | 61 / 1022 | 29 |
| 0.3 | 40 | 0 | 1 / 1823 | 0 / 1448 | 48 / 1022 | 18 |
| 0.3 | 40 | 2 | 1 / 1823 | 0 / 1448 | 46 / 1022 | 17 |
| 0.3 | 40 | 4 | 1 / 1823 | 0 / 1448 | 39 / 1022 | 16 |
| 0.35 | 20 | 0 | 8 / 1823 | 0 / 1448 | 154 / 1022 | 80 |
| 0.35 | 20 | 2 | 7 / 1823 | 0 / 1448 | 135 / 1022 | 70 |
| 0.35 | 20 | 4 | 5 / 1823 | 0 / 1448 | 98 / 1022 | 51 |
| 0.35 | 30 | 0 | 4 / 1823 | 0 / 1448 | 72 / 1022 | 32 |
| 0.35 | 30 | 2 | 3 / 1823 | 0 / 1448 | 69 / 1022 | 32 |
| 0.35 | 30 | 4 | 3 / 1823 | 0 / 1448 | 56 / 1022 | 25 |
| 0.35 | 40 | 0 | 1 / 1823 | 0 / 1448 | 43 / 1022 | 15 |
| 0.35 | 40 | 2 | 1 / 1823 | 0 / 1448 | 42 / 1022 | 15 |
| 0.35 | 40 | 4 | 1 / 1823 | 0 / 1448 | 36 / 1022 | 14 |
| 0.4 | 20 | 0 | 8 / 1823 | 0 / 1448 | 132 / 1022 | 63 |
| 0.4 | 20 | 2 | 7 / 1823 | 0 / 1448 | 117 / 1022 | 57 |
| 0.4 | 20 | 4 | 5 / 1823 | 0 / 1448 | 86 / 1022 | 41 |
| 0.4 | 30 | 0 | 4 / 1823 | 0 / 1448 | 63 / 1022 | 26 |
| 0.4 | 30 | 2 | 3 / 1823 | 0 / 1448 | 60 / 1022 | 26 |
| 0.4 | 30 | 4 | 3 / 1823 | 0 / 1448 | 50 / 1022 | 20 |
| 0.4 | 40 | 0 | 1 / 1823 | 0 / 1448 | 36 / 1022 | 11 |
| 0.4 | 40 | 2 | 1 / 1823 | 0 / 1448 | 35 / 1022 | 11 |
| 0.4 | 40 | 4 | 1 / 1823 | 0 / 1448 | 31 / 1022 | 10 |

| quantile | clean share | unlabelled share | cheater share |
|---|---|---|---|
| 0.5 | 0.02 | 0.02 | 0.12 |
| 0.9 | 0.08 | 0.08 | 0.54 |
| 0.99 | 0.22 | 0.21 | 0.80 |
| 0.995 | 0.27 | 0.23 | 0.83 |

| map | clean players (>=30 hits) | clean 99.5th percentile share | cheaters | cheaters firing (0.3 / 30 / 2) |
|---|---|---|---|---|
| de_ancient | 211 | 0.68 | 45 | 2 |
| de_dust2 | 326 | 0.27 | 409 | 32 |
| de_inferno | 282 | 0.23 | 148 | 7 |
| de_mirage | 243 | 0.19 | 322 | 25 |
| de_nuke | 250 | 0.11 | 98 | 14 |

Labelled cheaters with an event: information_gap 87, burst rule 66, share rule 80, any 168 of 1022. Clean players with an event: information_gap 4, burst rule 4, share rule 3.

Other candidate metrics tested on the same run and dropped (AUC cheaters vs clean, fires at the 0.5% clean threshold):

| metric | AUC | clean / unlabelled / cheaters fired |
|---|---|---|
| movement toward hidden uninformed enemies vs ghost enemies | 0.67 | 9 / 7 / 30 |
| grenade detonations near hidden uninformed enemies vs ghosts | 0.65 | 9 / 3 / 9 |
| turning toward hidden enemies approaching from outside the view vs ghosts | 0.58 | 9 / 3 / 5 |
| surprise penalty: hit rate on informed vs uninformed first sights | 0.53 | 8 / 2 / 15 |
| surprise penalty: aim error and reaction on informed vs uninformed first sights | 0.45 to 0.48 | below chance |
| crosshair on the enemy 250 ms before first sight vs ghosts | 0.33 to 0.39 | below chance |
