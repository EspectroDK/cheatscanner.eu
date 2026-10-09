# Detectors

All detectors share one interface (`detectors/base.py`): they read the reconstructed world, visibility,
knowledge model and encounters, record **observations** for every instance they measure, and emit
**evidence events** only when their (configurable, mostly UNCALIBRATED) criteria are met. Each module
docstring explains why the detector exists and which false-positive situations it handles; this page
is the overview. Config lives under `[detectors.<name>]`; `enabled = false` disables one, and
`--detectors a,b` runs a subset.

| # | name | axis / group | reliability | what it looks for | main false-positive controls |
|---|---|---|---|---|---|
| 1 | `hidden_tracking` | HIDDEN_INFORMATION / information | 0.7 | crosshair follows the bearing change *caused by the enemy's own movement* while the enemy is geometry-occluded and UNKNOWN | only target-induced motion counts (observer strafing excluded); enemy must move ≥300 ms; specificity vs other hidden enemies; direction reversals must be matched; last-known-position aim lowers severity |
| 2 | `remembered_position` | HIDDEN_INFORMATION / information | 0.55 | aim systematically closer to an enemy's *current* hidden position than to where it was 0.25–2 s ago | match-level only; 1 s blocks + block bootstrap CI; minimum 20 blocks; enemy must have moved ≥150 u |
| 3 | `previsibility` | HIDDEN_INFORMATION / information | 0.4 | aim converging on an enemy's real-time position before first visibility | pre-window must be UNKNOWN; `aim_driven_fraction` (enemy walking into a held crosshair ≈ 0); static-spot specificity (pre-aiming the appearance point ≈ 0); reduced until per-map pre-aim baselines exist |
| 4 | `aim_acquisition` | AIM_MECHANICS / aim | 0.3 | very fast reaction + acquisition of a large initial error with no corrections | server-side visibility lags the client; wide lower bounds; weak events only |
| 5 | `snap` | AIM_MECHANICS / aim | 0.35 | large, short flicks that land on a hitbox with ~0 overshoot and fire instantly | a flick alone is never evidence; needs repetition; distributions for baselines |
| 6 | `attraction` | AIM_MECHANICS / aim | 0.2 | aim corrections biased toward nearby enemies (soft aim) | movement toward *visible* enemies is normal (observation only without baselines); hidden bias needs ≥400 samples and a design-effect-corrected z |
| 7 | `target_switch` | AIM_MECHANICS / aim | 0.25 | fast large switches to a second enemy after a kill | weak; repetition required |
| 8 | `trigger_timing` | SHOT_TIMING / timing | 0.2 | shots consistently fired within one or two ticks of the crosshair touching an enemy, with low variance | sprays, blind, shotguns, tight holds (enemy walked into a held angle) and prefires excluded; ≥8 samples; tick resolution 15.6 ms |
| 9 | `recoil` | RECOIL / recoil | 0.25 | compensation that is too exact and too repeatable across sprays (view vs 2 × Δpunch) | good recoil control is normal; ≥5 sprays of ≥5 shots; per weapon |
| 10 | `smoke_tracking` | HIDDEN_INFORMATION / information | 0.55 | #1 restricted to stable smoke cores | bloom/fade, edges and HE-cleared smokes are uncertain, and so are sight lines within 48 u of a bullet tunnel for 1 s after a shot through the core (the rest of the smoke stays opaque); a smoke kill alone is not evidence |
| 11 | `flash` | HIDDEN_INFORMATION / information | 0.2 | tracking a moving enemy while heavily blind (≥1.5 s remaining) | sound windows excluded |
| 12 | `strategic_information` | DECISION_INFORMATION / strategic | 0.1 | crosshair near hidden UNKNOWN enemies more often than a time-shifted null of the same trajectories | needs ≥3000 samples; very low weight; confounded by common angles |
| 20 | `mechanical_impossibility` | IMPOSSIBLE_MECHANICS / impossible | 0.2 | bullet direction vs direction to the hit body point at damage | **observations only**: no event until `threshold_deg` is set from data |
| 21 | `view_integrity` | IMPOSSIBLE_MECHANICS / impossible | 0.8 | the view jumps to a target on the firing tick and is back on the next tick (silent aim); pitch held at the ±89° limit while moving (anti-aim) | ≥2 snap-returns per match; the jump must be 5× the movement on the surrounding ticks, so fast human flicks do not qualify; pinned pitch only counts on moving, live ticks (AFK players excluded); no clean CS2CD player without anti-aim showed either |
| 22 | `input_lattice` | IMPOSSIBLE_MECHANICS / impossible | 0.6 | view changes that are not whole mouse counts (sensitivity × 0.022°) in the 500 ms before shots, against the player's own other movement; also records each player's sensitivity as a fingerprint | players whose idle movement fits no lattice (controller, acceleration, sensitivity changed) never produce evidence; scoped, dead, freeze-time and teleport ticks excluded; needs ≥100 pre-shot moves, a pre-shot rate ≥2% and ≥3× the idle rate, idle rate ≤1% (median over rounds, so a cheat switched on for a few rounds is not mistaken for noisy input; the ratio and test use the whole-match idle rate) and p ≤ 1e-6. Across all 795 CS2CD matches the per-round median adds 5 labelled cheaters, 1 clean and 3 unlabelled players to the 81 / 36 / 20 the whole-match rate gave. Legitimate players' 99th percentile before shots is 0.08%. On CS2CD Mirage the rule fires for 14 labelled cheaters and 1 clean player. On 31 Valve matchmaking demos (9 maps) and 15 pro Mirage demos, 456 of 465 player slots fit a lattice (median fit 1.0; the rest are coaches, one very-low-sensitivity pro and three likely controller/changed-sensitivity players) and no player met the event criteria (one matchmaking player at 9.4% before shots is excluded by the idle rule) |
| 23 | `smoke_kills` | HIDDEN_INFORMATION / information | 0.5 | many kills the game itself marks as through smoke (`player_death.thrusmoke`), as a share of the player's kills | match-scope; needs ≥10 smoke kills that are ≥35% of kills. On 795 CS2CD matches the clean 99th percentile is 4; the rule fires for 2 of 4,598 clean players (one an anti-aim rage cheater) and 3 of 1,040 labelled cheaters |
| 24 | `information_gap` | HIDDEN_INFORMATION / information | 0.6 | over the match, how much of hidden enemies' own movement (walls, stable smoke cores) the crosshair follows within 3° while the best legitimate estimate of their position (own sight + extrapolation, teammates/radar, sound, damage, each with a margin of error: `knowledge/estimates.py`) is ≥6° off; also first shots of bursts at such enemies | match-scope; only target-induced bearing change counts (held angles add ~nothing); only information from before each tick; the best estimate is chosen knowing the true position; needs ≥300° tracked AND ≥3% of the opportunity; first shots only add severity. On 174 CS2CD Mirage/Nuke matches: 0 of 714 clean, 0 of 600 unlabelled, 46 of 420 labelled cheaters (clean median share 1%, highest clean 283° at 3%) |
| 25 | `hidden_fire` | HIDDEN_INFORMATION / information | 0.6 | over the match: bursts fired with no enemy in view whose first shots land within 1.5° of a hidden enemy while the best legitimate estimate of his position is ≥5° off, minus the same count against ghost enemies (the enemy team at the same time of up to 6 other rounds in which the player had the same side); plus hits on enemies hidden behind a wall or smoke and their share of all the player's gun hits | match-scope; the ghost comparison removes habitual wallbang and pre-fire spots per player; fires on excess ≥3 AND ≥10 hidden hits (174 CS2CD Mirage/Nuke matches: 1 of 714 clean, possibly an unlabelled cheater, 0 of 600 unlabelled, 32 of 420 labelled cheaters, 17 of them not caught by `information_gap`), or on a hidden share ≥30% over ≥30 hits with ≥2 hidden hits without information (434 CS2CD matches on 5 maps: 3 of 1823 clean, two of them most likely unlabelled cheaters, 0 of 1448 unlabelled, 80 of 1022 cheaters, 40 of them new; docs/validation/cs2cd/hidden_fire.md) |
| 26 | `bunnyhop` | IMPOSSIBLE_MECHANICS / impossible | 0.7 | over the match, from the game's own airborne flag: jumps that take off again within 2 ticks of landing (*perfect rehops*), their share of all quick rehops (next take-off within 0.5 s), and chains of ≥3 perfect rehops in a row | match-scope; fires only on ≥50 perfect rehops AND ≥80% share AND ≥20 chains, so a human scroll-wheel hopper who times most hops a few ticks late never qualifies; air speed is not used (CS2 caps it for everyone); numbers in docs/validation/cs2cd/bunnyhop.md |
| 27 | `safe_carelessness` | HIDDEN_INFORMATION / information | 0.5 | every 0.5 s while no enemy is visible or known (legitimate estimate within 15° and 2,500 u): is the knife or bomb out, and is a hidden, unknown enemy within 800 u? Knife share when safe minus knife share when an enemy is near, minus the same difference against ghost enemies (the enemy team at the same time of up to 6 other rounds with the same side, hidden from the player's real eye) | match-scope; moments with any enemy in view or known are left out; the ghost comparison removes place- and time-bound knife habits per player; needs ≥20 threat and ≥50 safe moments; fires on excess ≥0.4 AND z ≥2.5 (neighbouring samples deflated 4×). 434 CS2CD matches on 5 maps: 2 of 854 qualifying clean players, 1 of 231 unlabelled, 11 of 141 labelled cheaters, 8 of them caught by neither `information_gap` nor `hidden_fire`; loud running in the same moments did not separate and is not used (docs/validation/cs2cd/safe_carelessness.md) |
| 28 | `mouse_view` | IMPOSSIBLE_MECHANICS / impossible | 0.6 | user commands whose mouse fields (`usercmd_mouse_dx/dy`) hold the absolute view angle (dx = −yaw / 0.022, dy = pitch / 0.022, within 1.5) instead of mouse movement, on ≥80% of a player's ticks; also records how well the recorded counts explain the view turn (`agreement`, about 0.94 for clean players) | needs ≥2000 live, alive, unscoped ticks with mouse data; ticks with tiny counts never match; demos without user commands produce nothing. On all 795 CS2CD matches it fires for 0 of 4,723 clean players (the highest clean share is 0.45, one player from mid-match on), 6 of 1,815 unlabelled and 26 of 1,293 labelled cheaters (25 not caught by `view_integrity` or `input_lattice`); no player in 8 Valve matchmaking demos shows a single such tick. Where the values come from is not known (docs/validation/cs2cd/mouse_view.md) |

## Evidence groups

Detectors in the same group measure the same underlying behaviour (e.g. hidden tracking, previsibility
and smoke tracking all measure use of hidden information). Scoring clusters events into incidents
per axis, group, target and round, so one moment reported by three detectors is one incident; see
[methodology.md](methodology.md).

## Per-player evidence

Besides single events, `scoring/player_evidence.py` scores each player over *all* their observations in a
match: every observation of five features (hidden-aim error, time aimed within 2° of hidden enemies,
view-to-hit discrepancy, trigger time, acquisition jerk) becomes a log-likelihood ratio from binned CS2CD
rates, and the per-feature means, weighted by min(n, 30), are summed. The score is placed among clean
players and enters the assessment as the `profile` family, capped below HIGH. See
[validation/cs2cd](validation/cs2cd/README.md).

## Adding a detector

Subclass `Detector`, set `name`, `axis`, `group`, `default_reliability`, implement `analyze(ctx)` returning a list of events, call
`ctx.observe(name, player_index, **values)` for every instance and build evidence with `self.event(...)`, register it in
`detectors/__init__.py`, add a config section, and add a synthetic scenario to
`tests/test_detectors_synthetic.py` covering both a positive case and its main false-positive case.

## Future work

Strategic-information analysis of routes, rotations and utility; per-map pre-aim baselines for #3;
per-player baselines for mechanics; learned models on the ML-ready encounter channels
(`--export-parquet`) once labelled data exists.
