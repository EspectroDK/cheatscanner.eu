# `mouse_view` calibration (CS2CD)

All 795 CS2CD matches, run with
`tools/calibration/mouse_view_calibrate.py` (`mouse_view`, `view_integrity` and `input_lattice`, no map
mesh needed, about 10 s per match). Players with at least 2,000 live, alive, unscoped ticks with mouse
data: 4,723 clean, 1,815 unlabelled (cheater matches) and 1,293 labelled cheaters. The six anti-aim players
in `anti_aim_clean_players.txt` are left out of the clean side (none of them shows the pattern).

## What the detector looks for

Every user command carries `usercmd_mouse_dx/dy`, the mouse counts of that tick. Over a match the
recorded counts follow the turn of the view: |correlation| of the yaw change with `dx` has a median of
0.943 for clean players and 0.92 on 8 Valve matchmaking demos. Single ticks do not match exactly (only
10-35% to within a count), because the demo keeps one command per tick.

Some players' commands carry something else in those fields: the view angle itself, `dx = -yaw / 0.022`
and `dy = pitch / 0.022` to within about one count (measured offset median 0.5-0.6), tick after tick. This
is not mouse movement. Values reach ±8,180 (±180°) and stay constant while the view is still. The
detector fires when this holds on at least 80% of a player's ticks.

An earlier version scored a weak relation between counts and view over the whole match (study in the
project files, issue #39). Every player with a near-zero relation turned out to have the absolute-angle
fields. With those ticks set aside, no clean, unlabelled or cheater player with normal mouse data is left
below 0.2, so the relation itself is only recorded (`agreement`), not scored.

## Result

| min share of ticks with angle-valued mouse fields | clean | unlabelled | cheater | cheaters not caught by `view_integrity` or `input_lattice` |
|---|---|---|---|---|
| 0.001 | 1 | 6 | 26 | 25 |
| 0.5 | 0 | 6 | 26 | 25 |
| **0.8 (default)** | **0** | **6** | **26** | **25** |

The distribution is two-sided: the 26 cheaters (23 matches) have the pattern on 84-100% of their ticks
(median 100%), everyone else on none. The one clean player with it (`no_cheater_present_98`, Player_5)
has normal mouse fields until tick 46,502 and angle-valued ones after that (45% of the match). Five of the
six unlabelled players are at 100% and one at 97%; two of the six play in a match where labelled
cheaters show the same pattern (`with_cheater_present_140`, `_141`), and all six are in cheater matches.

**Where it comes from is not known.** A mouse cannot produce it, and it is almost only found in cheater
matches, but the clean player above shows it for half a match. Possible sources include a non-mouse input
path or software writing the command. That is why it is one IMPOSSIBLE_MECHANICS event with reliability
0.6 (like `input_lattice`), not a stronger one.

None of the 80 players in Michael's 8 Valve matchmaking demos (patch 14186-14189) has a single tick with
angle-valued fields. Tournament (SourceTV) demos have not been checked; demos without user commands are
skipped.

## Class impact (full analyzer, before/after)

All 29 matches with a player at or above 20% were analysed with the full pipeline twice, with
`mouse_view` disabled and enabled (`before_after.py` in the project study). Players the detector does not
fire for are unchanged by construction.

| | before -> after | players |
|---|---|---|
| labelled cheaters | NORMAL -> ELEVATED | 3 |
| | NORMAL -> HIGH | 1 |
| | ELEVATED -> HIGH | 6 |
| | HIGH -> VERY_HIGH | 8 |
| | VERY_HIGH, unchanged | 2 |
| | INSUFFICIENT_DATA, unchanged | 7 |
| unlabelled | NORMAL -> ELEVATED | 1 |
| | ELEVATED -> HIGH | 1 |
| | INSUFFICIENT_DATA, unchanged | 4 |
| clean | no change | all |

Matchmaking demos: no change (no event). Pro demos: not re-run.

Reproduce:

```bash
python tools/calibration/mouse_view_calibrate.py \
  --exclude docs/validation/cs2cd/anti_aim_clean_players.txt --work output/mouse_view --jobs 4
```
