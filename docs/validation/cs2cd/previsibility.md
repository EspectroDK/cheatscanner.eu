# previsibility: static-spot pre-aims (PR for issue #35)

Before the change, an encounter where the crosshair converged on the fixed spot
where the enemy then appeared (static-spot specificity <= 0) kept 40% of its
severity, enough for an event. After it, such encounters give no event.

Severity before and after was recomputed from the same per-encounter
observations (all metrics the severity uses are stored), with default config.

## CS2CD, 174 de_nuke + de_mirage matches with map meshes

| group | players | with an event before | after | events before | after | players with >= 2 events before | after |
|---|---|---|---|---|---|---|---|
| clean | 714 | 34 (4.8%) | 16 (2.2%) | 35 | 16 | 1 | 0 |
| unlabelled (cheater matches) | 600 | 20 (3.3%) | 10 (1.7%) | 21 | 10 | 1 | 0 |
| labelled cheaters | 420 | 11 (2.6%) | 4 (1.0%) | 12 | 4 | 1 | 0 |

Clean players exclude `anti_aim_clean_players.txt`.

## Reference demos (stored observations)

| set | player-matches | with an event before | after |
|---|---|---|---|
| 15 pro Mirage matches | 149 | 8 | 2 |
| 21 matchmaking matches | 208 | 15 | 6 |

## Reading

The detector fires more often for clean players than for labelled cheaters,
both before and after the change, so on its own it does not separate them
(consistent with the earlier finding that pre-visibility measures skill and
pros show it as strongly as cheaters). The change halves its events in every
group and removes all repeat events (>= 2 in a match).
