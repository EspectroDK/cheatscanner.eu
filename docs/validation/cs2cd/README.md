# CS2CD calibration run (de_mirage, 2026-09-26)

Data: [CS2CD](https://huggingface.co/datasets/CS2CD/CS2CD.Counter-Strike_2_Cheat_Detection) (CC BY 4.0),
795 anonymised matches pre-parsed with demoparser2. Only de_mirage has a collision mesh here, so this
run uses all 121 Mirage matches: 41 from `no_cheater_present` and 80 from `with_cheater_present`
(322 players labelled as VAC-banned cheaters).

Label quality, as the dataset authors report it: in a 50-match sample of clean matches 97.2% of players
showed no cheating behaviour. In the cheater matches the labels mark VAC-banned *accounts*, and the ban
may not be for this match. The authors' 55.6% figure is the precision of the *not cheater* label inside
cheater matches: many unlabelled players there may be cheating too. The clean set is therefore a usable
legitimate baseline, the unlabelled same-match group is contaminated, and separations against either
cheater comparison are a lower bound.

## Reproduce

```bash
cs2-analyzer maps-fetch de_mirage
cs2-analyzer cs2cd fetch --map de_mirage --dest data/cs2cd          # ~7 GB
for s in no_cheater_present with_cheater_present; do
  cs2-analyzer analyze data/cs2cd/$s/*.parquet --no-db --output output/cs2cd --observations data/cs2cd_obs/$s
done
cs2-analyzer calibrate report --obs-dir data/cs2cd_obs/no_cheater_present --out output/cs2cd_calibration
cs2-analyzer calibrate build-baselines --obs-dir data/cs2cd_obs/no_cheater_present --no-db \
  --json-out output/cs2cd_calibration/baselines.json
cs2-analyzer cs2cd labels --dest data/cs2cd > cheaters.txt
cs2-analyzer calibrate player-evidence-model --obs-dir data/cs2cd_obs/no_cheater_present \
  --exclude docs/validation/cs2cd/anti_aim_clean_players.txt \
  --cheater-obs-dir data/cs2cd_obs/with_cheater_present --cheater-ids cheaters.txt \
  --json-out src/cs2_analyzer/data/player_evidence_cs2cd_mirage.json
python tools/calibration/cs2cd_compare.py --dataset data/cs2cd --obs data/cs2cd_obs \
  --output output/cs2cd --out output/cs2cd_compare.md \
  --exclude-clean docs/validation/cs2cd/anti_aim_clean_players.txt
```

Each match takes about a minute on one core.

## Files

- [clean_mirage_calibration_report.md](clean_mirage_calibration_report.md): raw metric distributions from the 41 clean matches (410 players)
- [clean_mirage_baselines.json](clean_mirage_baselines.json): the population baselines built from them (103 rows)
- [mirage_cheaters_vs_clean.md](mirage_cheaters_vs_clean.md): per-player comparison of clean players, unlabelled players and labelled cheaters

## Findings of the first run (placeholder thresholds)

1. **The scoring is precise but almost blind at the current placeholder thresholds.** 0 of 410 clean
   players were scored above NORMAL. Of the 322 labelled cheaters, 2 were (1 HIGH, 1 ELEVATED). The AUC of
   the overall score is 0.50. Detectors produce about 0.07 evidence events per player.
2. **Several raw metrics do separate labelled cheaters from clean players**, and they hold up against
   unlabelled players in the *same* matches (a control for rank and server). AUC is for per-player
   medians. Values below 0.5 mean cheaters have lower values.

   | metric | clean p50 | cheater p50 | AUC | AUC same-match |
   |---|---|---|---|---|
   | `aim_acquisition.acquisition_ms` | 422 ms | 328 ms | 0.29 | 0.29 |
   | `hidden_tracking.mean_error_deg` | 54.1° | 44.1° | 0.29 | 0.31 |
   | `previsibility.err_-250` | 9.1° | 5.3° | 0.28 | 0.32 |
   | `aim_acquisition.reaction_ms` | 188 ms | 129 ms | 0.38 | 0.37 |
   | `mechanical_impossibility.discrepancy_deg` | 1.12° | 1.87° | 0.68 | 0.71 |

   Hidden-tracking correlation, trigger timing and recoil compensation show little or no separation at
   the player level.
3. Some detectors produce too few observations to compare: snap, remembered position, attraction and
   strategic information. Labelled cheaters also have fewer analysed encounters (median 31, against 54
   for clean players); the cause has not been investigated yet.

4. **Per-event thresholds were left unchanged.** Replaying the event severities offline shows they already
   sit at a low false-positive level. For example, hidden tracking at `min_event_severity = 0.25` gives an
   event to 0.7% of clean players and 2.2% of labelled cheaters. Lowering the thresholds adds clean players
   about as fast as it adds cheaters (at 0.05 the figures are 25.6% and 25.2%).

These numbers are a starting point for calibration. They are not validation of any individual verdict.

## Population comparison (reported only)

Because the separation lies in typical behaviour rather than in single events, every assessment now
carries a `population_comparison` (`scoring/population.py`). For each metric, the player's match median is
placed among the per-player medians of the 41 clean matches. The bundled reference is
`data/population_reference_cs2cd_mirage.json`, which can be rebuilt with
`cs2-analyzer calibrate population-reference`. Each metric is oriented so that high means suspicious, and
the percentiles are averaged over the metrics the player has data for (at least 2). The result is printed
and stored next to the evidence score, and it **does not change the classification**.

### Choosing the metrics: pros vs cheaters

The first version averaged five metrics that were chosen on half of the cheater matches. A second clean
corpus showed that two of them measure skill: 15 pro Mirage matches from HLTV (tournament SourceTV
demos, 150 players, analysed with `--keep-demo`). Pros acquire targets faster and converge before
visibility about as strongly as labelled cheaters do, so those metrics would flag the best legitimate
players.

| per-player median | clean | pro | labelled cheater | AUC pro vs clean | AUC cheater vs clean |
|---|---|---|---|---|---|
| `aim_acquisition.acquisition_ms` | 422 ms | 344 ms | 328 ms | 0.31 | 0.29 |
| `previsibility.err_-250` | 9.1° | 6.0° | 5.3° | 0.33 | 0.28 |
| `hidden_tracking.mean_error_deg` | 54.1° | 53.6° | 44.1° | 0.46 | 0.29 |
| `mechanical_impossibility.discrepancy_deg` | 1.12° | 0.92° | 1.87° | 0.30 | 0.68 |

The comparison now uses only metrics on which pros look like ordinary clean players:

- aim error to hidden enemies
- view-to-hit discrepancy
- recoil residual

Pro demos carry no aim punch, so recoil is not measured for them. They do carry `fire_bullets`, which makes
their view-to-hit discrepancy more exact than the dataset's view + 2 × punch estimate, so the pro rate below
is somewhat optimistic.

| combined percentile | clean (LOO, n=365) | unlabelled same-match (n=335) | labelled cheaters (n=290) | pro (n=150) |
|---|---|---|---|---|
| ≥ 0.85 | 4.1% | 3.0% | 37.9% | 1.3% |
| ≥ 0.9 (configured flag) | 2.7% | 2.1% | 30.0% | 0.0% |
| ≥ 0.95 | 2.2% | 0.9% | 20.3% | 0.0% |

AUC for labelled cheaters is 0.74 against clean players and 0.76 against the unlabelled players of the
same matches. Coverage is also better, because only two metrics are needed: 290 of the 322 labelled
cheaters get a percentile. All three metrics were among those chosen on the even-numbered cheater
matches, so these whole-corpus figures are partly in-sample. Account-level ban labels, and cheaters
hiding among the unlabelled same-match players, pull in the other direction.

Only one pro was classified above NORMAL by the event-based score: ELEVATED at 0.41, from a fast
acquisition, 4 s of hidden tracking and one pre-aim. Their combined percentile was well below the flag.

## Player evidence over the match (part of the score)

Other behavioural anti-cheats judge a player over many shots rather than one event. The analyzer now
does the same (`scoring/player_evidence.py`). Every observation of five features becomes a
log-likelihood ratio, log P(x | labelled cheater) / P(x | clean), from decile bins of the clean
distribution. The features are:

- aim error to hidden enemies
- time aimed within 2° of hidden enemies
- view-to-hit discrepancy
- trigger time
- peak jerk during target acquisition (smoothed aim is unusually low in jerk)

Per player and feature the mean ratio is weighted by min(n, 30), and the weighted means are summed. The
sum is placed among clean players and becomes the `profile` family of the assessment. The clean 98th
percentile gives strength 0.25 (ELEVATED). Strength is capped at 0.45, so the profile alone never
reaches HIGH, and it never counts as independent corroboration.

Features were selected on one half of the matches and evaluated on the other. Acquisition time and
pre-visibility convergence add AUC, but pros cross the flag line with them about 2.5 times as often as
clean players, so they are not used.

Cross-fitted results: the tables and the clean reference are fitted on one half of the matches and
applied to the other half. Pros are scored with the full model. "Events only" is the event evidence of
the full run, including `view_integrity` and `input_lattice` below; before this work it was 0.0% of
clean players and 0.6% of labelled cheaters.

| | clean (n=387) | unlabelled same-match (n=456) | labelled cheaters (n=314) | pro (n=150) |
|---|---|---|---|---|
| above clean p98 | 1.6% | 0.4% | 23.6% | 0.7% |
| ELEVATED or above, events only | 0.3% | 0.4% | 7.3% | 0.7% |
| ELEVATED or above, with profile | 1.8% | 0.9% | 30.6% | 0.7% |
| HIGH or above, with profile | 0.0% | 0.2% | 3.5% | 0.0% |

AUC for labelled cheaters is 0.83 against clean players and 0.79 against same-match unlabelled players.
The pro column comes from a full re-run of the 15 pro matches: 149 NORMAL, 1 ELEVATED from events, and no pro
above the profile line besides a NORMAL one at the 97th percentile. For pros, view-to-hit discrepancy
comes from `fire_bullets`, which is more exact, so their rate is somewhat optimistic.

## Mouse-input lattice (`input_lattice` detector)

With raw mouse input every view change is a whole number of mouse counts times sensitivity × 0.022°.
The divisor has to be estimated to about 1e-6 relative, or large flicks (1000+ counts) fall off the
lattice; the first version was less precise and put about 3% of every player's moves off it. With the
least-squares refinement, legitimate players have essentially no off-lattice moves: on 31 matchmaking
and 15 pro demos the 99th percentile before shots is 0.08%. CS2CD stores angles as float32, which is
precise enough (steps of about 1.5e-5°).

All 121 matches were re-analysed with the refined detector.

- **Off-lattice aim before shots separates.** 10.4% of labelled cheaters with a valid lattice have at
  least 1% of their pre-shot moves off it, against 1.3% of clean players. Evidence needs a pre-shot rate of
  at least 2%, 3× the player's idle rate, an idle rate of at most 1% (noisy input is not evidence), at
  least 100 pre-shot moves and p ≤ 1e-6. That fires for 14 labelled cheaters, 1 clean player and 2
  unlabelled players. All 17 have calm movement on the lattice and aim before shots off it; the clean
  one is off the lattice on 23.5% of pre-shot moves and on none of its other movement. It fires for no pro
  and for none of the 31 matchmaking demos.
- **Players whose movement fits no lattice at all** (fit below 0.8: 5.9% of labelled cheaters, 1.0% of
  clean players and all 6 anti-aim players) can also be controllers, raw input off or a sensitivity change
  mid-match. That enters the player evidence only as a single per-player observation,
  `input_lattice_summary.lattice_fit`, with bins at 0.8 and 0.95 (+1.6 and +1.7; the clean p98 score is
  about 10).

## Silent aim and anti-aim (`view_integrity` detector)

Some shots show the view jumping to the target on the firing tick and returning on the next tick. This
is the signature of silent or "rage" aimbots. In 121 matches (157k gun shots), such snap-and-return shots
came from 15 labelled cheaters and from 6 players in the clean split. Those 6 players, two in each of
clean matches 65, 67 and 73, held their pitch at the 89° limit on more than half of their live ticks,
which is anti-aim. They are unbanned rage cheaters, so the clean split is not fully clean.
They are listed in `anti_aim_clean_players.txt` and left out of the clean side when fitting and
evaluating the player evidence. No other clean player produced a snap-and-return shot or a pinned pitch.
In the full re-analysis the detector's evidence fired for 13 labelled cheaters, all 6 anti-aim players,
0 of 404 other clean players and 0 of 478 unlabelled players. On the 15 pro matches it fired for no one.

## Dataset format notes

See the `parser/cs2cd_backend.py` docstring. In short: ids are `Player_1..10` per match, so they are
mapped to synthetic ids `900_000_000_000 + split × 10_000_000 + match × 100 + N` (split 1 = clean,
2 = with cheater), unique per match; `cs2-analyzer cs2cd labels` prints the labelled cheaters' ids for
`calibrate --exclude`. The dataset has no
`round_start`/`round_end`/`fire_bullets` events, so rounds come from `round_prestart`, `round_freeze_end`
and the `round_win_status` prop, and bullet direction falls back to view + 2 × aim punch. The tick files
also contain `usercmd_mouse_dx/dy` and `usercmd_input_history` (sub-tick input), which the analyzer
does not use yet.

## Per-map references (2026-09-30)

The play-pattern model and the population reference were first built from Mirage only and used on every
map. They are now built from the nine CS2CD maps that have a game-built mesh (626 matches; see
[per_map.md](per_map.md)). The llr tables are pooled over all maps, because a single map has too few
labelled cheaters for stable tables (Overpass has 12); the clean reference scores that set the ELEVATED
threshold are per map. Maps outside the dataset use the model pooled over all nine. Held-out, the clean
share reaching ELEVATED from the pattern alone went from 0.2–4.2% per map (1.6% overall) to 1.6–3.6%
(2.8%), and labelled cheaters from 26.7% to 37.4%. Fully per-map tables were worse (3.3% clean, AUC 0.84).
15 pro Mirage matches: 1 of 140 players at ELEVATED from the pattern, before and after.

Reproduce (Mirage runs with the public mesh, the other maps with meshes built from the game):

```bash
python tools/calibration/cs2cd_maps_run.py --jobs 6          # every CS2CD map with a mesh in data/maps
python tools/calibration/cs2cd_per_map.py docs/validation/cs2cd/obs/*.parquet \
  --exclude docs/validation/cs2cd/anti_aim_clean_players.txt --out docs/validation/cs2cd/per_map.md --write
```

`obs/` holds the compact observation tables (map, split, match, player, cheater label, feature, value) so
the fit can be redone without the ~40 GB of tick data. Each worker needs about 2 GB of memory.
