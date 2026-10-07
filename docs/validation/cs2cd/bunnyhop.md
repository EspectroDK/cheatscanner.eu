# `bunnyhop` calibration (CS2CD)

60 CS2CD matches drawn at random (seed 7) from both splits: 30 `no_cheater_present` matches (300 clean
players) and 30 `with_cheater_present` matches (105 labelled cheaters, 195 unlabelled players), on all
maps. The detector needs only the game's airborne flag, so no map mesh is involved and the run is cheap
(`build_world` + `BunnyhopDetector`, about 10 s per match).

The detector counts, per player over the match, *rehops* (a jump whose next take-off comes within 0.5 s
of landing), *perfect rehops* (take-off within 2 ticks of landing) and *chains* of at least 3 perfect
rehops in a row (`src/cs2_analyzer/detectors/bunnyhop.py`). It fires on >= 50 perfect rehops AND a
perfect share >= 80% AND >= 20 chains.

| group | players | median perfect rehops | median perfect share | 99th pct perfect rehops | highest chains | >= 50 perfect rehops |
|---|---|---|---|---|---|---|
| clean | 300 | 1 | 6% | 48 | 14 | 1.0% |
| unlabelled (cheater matches) | 195 | - | - | - | 41 | 0.5% |
| labelled cheaters | 105 | 6 | - | - | 50 | 27.6% |

Result with the default rule: **0 of 300 clean, 1 of 195 unlabelled, 17 of 105 labelled cheaters** (in 9
of the 30 cheater matches). The unlabelled player (`with_cheater_present_234`, 190 perfect rehops at 87%,
41 chains) plays in a match with two labelled cheaters who hop the same way and is most likely an
unlabelled cheater. The firing cheaters have 105 to 408 perfect rehops at 87-98%, with longest chains of
7 to 38 hops.

Three clean-split players hop almost like a script (84, 80 and 70 perfect rehops at 90-100%, matches
`no_cheater_present_51`, `_89`, `_101`) but string together only 10-14 chains, so the chain threshold is
what keeps them out; they may be scroll-wheel experts or unlabelled cheaters (the dataset authors put the
clean split at about 97% clean). The grid below shows the chain count is the deciding guard.

| min perfect | min share | min chains | clean | unlabelled | cheater |
|---|---|---|---|---|---|
| 30 | 0.7 | 10 | 3 / 300 | 1 / 195 | 24 / 105 |
| 30 | 0.8 | 10 | 3 / 300 | 1 / 195 | 22 / 105 |
| 30 | 0.9 | 10 | 2 / 300 | 0 / 195 | 17 / 105 |
| 50 | 0.7 | 20 | 0 / 300 | 1 / 195 | 18 / 105 |
| **50** | **0.8** | **20** | **0 / 300** | **1 / 195** | **17 / 105** |
| 50 | 0.9 | 20 | 0 / 300 | 0 / 195 | 14 / 105 |
| 50 | 0.8 | 30 | 0 / 300 | 1 / 195 | 10 / 105 |
| 80 | 0.8 | 20 | 0 / 300 | 1 / 195 | 17 / 105 |
| 80 | 0.9 | 30 | 0 / 300 | 0 / 195 | 8 / 105 |

Overlap with the information detectors: of the 89 labelled cheaters in the 25 of these matches that are
also in the five-map `information_gap` / `hidden_fire` study, 12 are caught by those detectors and 7 more
by `bunnyhop`, so coverage goes from 12 to 19 of 89. The signal is independent of everything the
information and aim detectors look at.

Air speed was tried and dropped: CS2 caps air speed for everyone, so scripted and human hoppers reach the
same speeds.

Four matchmaking demos (Nuke, Dust2, Inferno, Ancient; 40 players, 2026-10-07): 0 events. The most active
hopper has 30 perfect rehops at 60% and one chain.
