# `hidden_fire` calibration (CS2CD, de_mirage + de_nuke)

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
