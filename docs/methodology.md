# Methodology

This analyzer produces **behavioral evidence scores**, not verdicts and not probabilities of cheating.
The design goal is that every non-NORMAL score can be defended to a skeptical reviewer by pointing at
specific ticks, the information the player could legitimately have had, and a measured anomaly.

## Status: partly calibrated

The player-level scoring (population comparison, player evidence profile, `input_lattice` and
`view_integrity`) was fitted and checked on the public CS2CD dataset and 15 pro matches; results and the
steps to reproduce them are in [validation/cs2cd](validation/cs2cd/README.md). Per-event thresholds
were checked there too and left at their conservative defaults. Every threshold in
`default_config.toml` still marked `UNCALIBRATED` is a placeholder chosen by reasoning, not from data.
Treat every score as a pointer for manual review, never as a verdict.

## Layers

1. **Observations** – every measured instance (each snap, each trigger time, each hidden window,
   each spray...) is written to `data/observations/<match>/<detector>.parquet` whether or not it looks
   unusual. These feed calibration, population baselines and player fingerprints.
2. **Evidence events** – emitted by a detector only when its criteria are met.
   `confidence = severity × reliability × information_confidence`, where reliability is a per-detector
   prior (hidden tracking 0.7 … strategic information 0.1) and information confidence comes from the
   knowledge model (how sure we are the player had *no* legitimate information).
3. **Match assessment** – events are grouped into **incidents** (same axis, same evidence group, same
   target, same round, within 3 s), so one moment seen by several detectors counts once. Per axis,
   incident strengths combine by noisy-OR, but an axis with fewer than 2 incidents is capped at 0.24,
   just below ELEVATED: a single spectacular play can never raise a match above NORMAL on its own
   (on 194 pro players across 6 non-Mirage maps, the old 0.35 cap let two single hidden-tracking
   incidents reach ELEVATED; on CS2CD the change cost 1 of 258 labelled cheaters). Axes form two families
   (information: hidden/decision; mechanics: aim/timing/recoil/impossible); the family score is the
   strongest axis plus 0.25 × the rest, and a small corroboration bonus applies only when *both*
   families are independently elevated. Too few encounters (<15) or rounds (<5) gives
   `INSUFFICIENT_DATA`.
4. **Player history** – match scores combine with a shrunk mean (prior of 3 neutral matches, so one
   match cannot dominate) plus a consistency term that rewards repeated elevated matches. Player
   fingerprints (aim/timing/recoil distributions) are stored per match, and `behavior_shift` flags
   robust z-score changes once 3 previous matches exist.
   The per-match player evidence (the `profile` family) is taken out of each match score and combined
   separately (`profile_history`). Each match's clean percentile becomes a z-score, clipped to
   [-2, 3] so one extreme match cannot dominate. Matches are weighted by age (half-life 180 days), and
   the weighted mean is tested against its variance under a within-player correlation ρ = 0.5:
   Var = (1 − ρ)/n_eff + ρ. Many mildly unusual matches therefore never add up to ELEVATED; only
   consistently high ones do. ρ is UNCALIBRATED: 10 pros with two matches each gave 0.64. The
   per-match breakdown is stored, and a latest match 2 z above the player's earlier ones is reported as
   a sudden change.

Labels: `NORMAL < 0.25 ≤ ELEVATED < 0.5 ≤ HIGH < 0.75 ≤ VERY_HIGH` (UNCALIBRATED).

## Treating ambiguity as legitimate

- Visibility is `UNKNOWN` (never "hidden") when the ray is near an edge, the enemy is within a
  shoulder-width margin, or a 100 ms extrapolation would make them visible (peeker's advantage,
  interpolation). Without a map mesh everything is `UNKNOWN` and hidden-information detectors cannot fire.
- Smokes have a solid core and an uncertain shell; bloom, fade, HE-cleared and gunfire-holed smokes are
  uncertain, never hiding.
- The knowledge model is deliberately generous: own sight with memory, teammate sight/radar with a
  callout persistence, damage, gunfire/footstep/jump/reload sound radii, round-start spawns, planted
  bomb site. Only `UNKNOWN` samples feed hidden-information detectors.
- Detectors on mechanics (snap, acquisition, trigger, recoil) start at low reliability and mainly
  collect distributions; they emit events only for combinations implausible even for elite players.

## Traceability

Every event has tick start/peak/end, round, target, the visibility and knowledge context, the raw
metrics, a natural-language explanation and a stable id. With `--debug` or `--generate-evidence`,
flagged players get per-event plots (top-down map, collision-mesh POV reconstruction, error/bearing
timelines, LOS and knowledge strips) and 10 s MP4 reviewer clips. Clips are **reconstructions** from demo
data and the collision mesh; the enemy outline is a reviewer annotation, not what the player saw.

## Calibration workflow

```bash
cs2-analyzer analyze demos/*.dem --keep-demo      # build up data/observations/
cs2-analyzer calibrate report --out output/calibration [--exclude banned_ids.txt]
cs2-analyzer calibrate build-baselines [--json-out baselines.json]
```

The report gives quantiles and histograms per metric (flick velocity and jerk, reaction time, hidden
tracking correlation, share of hidden time aimed within 2° of an enemy, trigger-time spread, recoil
residuals, ...). Set thresholds from those distributions (e.g. beyond the 99.9th percentile of
legitimate players, stratified by weapon class, range and map), never from a few suspicious clips.
Baselines are stored in `baseline_stats` for detectors to compare against. Known limitation: no
labelled cheater data exists in this repo, so sensitivity cannot yet be measured.

## Known limitations

- Demos record view angles once per tick (64 Hz); sub-tick input timing is not available, so reaction
  and trigger times have 15.6 ms resolution and are biased by server-side timing.
- No hitbox data: hit points are approximated from body points at eye-height fractions.
- `approximate_spotted_by` / `spotted` are approximate and lag; they are used as teammate/radar
  information (generously), not as ground truth for visibility.
- Voice communication is unobservable; it is modelled as persistence of teammate sight.
- Only maps with a `.tri` collision mesh get visibility; see [geometry.md](geometry.md).
- Strategic-information (radar-style) analysis is a single, weak metric; routes, rotations and utility
  decisions are future work.

## Retention and privacy

Raw demos are deleted after successful analysis unless `--keep-demo` (or `retention.keep_demos`).
Failed analyses move the demo to the retained directory if configured, otherwise delete it. Stored
data is SteamIDs, names as seen in the demo, compact features and evidence. The API binds to
127.0.0.1 in Compose and has no authentication: do not expose it publicly.
