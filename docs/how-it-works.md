# How the analysis works

This page explains, for anyone who wants to look under the hood, how Cheatscanner turns a CS2 demo into
the evidence classes shown on the site and in the app, and which measurements it takes along the way.

<!-- repo-only -->
The website shows this same file at `/#/how-it-works` (rendered by `web/src/pages/DocPage.tsx`;
everything between the repo-only markers is left out there). The deeper technical references are
[methodology.md](methodology.md), [detectors.md](detectors.md), [geometry.md](geometry.md) and the
calibration results in [validation/cs2cd](validation/cs2cd/README.md).
<!-- /repo-only -->

## The short version

- Cheatscanner reads the **demo file** of a finished match. It never touches the game, its memory or your PC.
- From the demo it rebuilds every tick of the match: where each player stood, where they looked, what they
  could see through the map's walls and smokes, and what they could plausibly *know* from sound, teammates
  and the radar.
- It then measures how each player aimed, when they fired and how they controlled recoil, relative to
  enemies they could or could not see.
- Those measurements are compared with how legitimate players behave, including professional players, so
  that skill is not mistaken for cheating.
- The result is an **evidence class**: Normal, Elevated or High (or "Not enough data"). It is a description
  of unusual behavior in analyzed matches. It is **not** a verdict, and **not** a probability that anyone
  cheats.

## Design principles

1. **Evidence, not accusations.** Every class above Normal can be traced to specific moments (round,
   tick, target), with a written explanation and a reconstruction clip.
2. **Doubt counts in the player's favor.** Whenever it is unclear whether a player could see or hear an
   enemy, the analysis assumes they could. This costs some sensitivity, but it keeps legitimate players from
   being flagged for information they really had.
3. **One moment is never enough.** A single spectacular play, however unusual, cannot raise a match above
   Normal on its own. Repetition, or a whole-match pattern, is required.
4. **Skill is not evidence.** Measurements on which top professional players look as unusual as cheaters
   are not used for scoring.
5. **Measured against real data.** The scoring was fitted and checked on a public dataset of labelled
   matches and on professional matches (see "How well it works" below).

## Step 1: reading the demo

Only Premier and Competitive matches are analyzed, since the scoring is calibrated on 5 against 5 matches.
Wingman and other modes, and demos with fewer than 8 players, are skipped right after reading the demo.

The demo is parsed with the open-source [demoparser2](https://github.com/LaihoE/demoparser) library. The
analysis uses:

- per tick (64 per second): position, view angles (where the player looks), health, weapon, ducking,
  flash blindness, whether the player is scoped, and the game's own "spotted" flags used by the radar;
- events: shots, damage, kills, grenades and smokes, footsteps, jumps, reloads, bomb plant and defuse,
  round start and end;
- the scoreboard and match details (map, date, score, Premier rating).

It does **not** read text chat or voice. Some things are simply not in a demo: sub-tick input timing,
hitboxes, and what the player's own screen showed. The analysis works around that and never invents data
that is missing; a missing value is recorded as unknown.

## Step 2: rebuilding what each player could see

Line of sight is computed by casting rays from each player's eyes to each enemy's body against the map's
**collision mesh**, the 3D geometry the game uses for bullets. Meshes are built from the game's own map
files, so walls, crates and props are where they are in the game (Mirage uses a public mesh that agrees
with the game's on 99.9% of sampled sight lines). Every map in the current Active Duty
pool is supported.

Each player-enemy pair at each tick is labelled **visible**, **hidden**, or **unknown**. "Unknown" is used
generously: when a ray passes close to an edge, when an enemy is within about a shoulder's width of
becoming visible, or when a tiny extrapolation of movement (peeker's advantage, network interpolation)
would make them visible. Only clearly hidden moments can ever count as hidden-information evidence.

Smokes are modelled with a solid core and an uncertain shell. Smokes that are still blooming, fading,
cleared by an HE grenade or holed by gunfire count as uncertain, never as blocking sight.

## Step 3: what could the player legitimately know?

Seeing an enemy is not the only way to know where they are. A **knowledge model** estimates, for every
player, enemy and tick, whether the player could plausibly know the enemy's position from:

- their own sight, now or recently (with memory);
- teammates' sight and the in-game radar;
- voice callouts (modelled as teammate sight staying useful for several seconds);
- damage given or taken;
- sound: gunfire (shorter range for silenced weapons), footsteps, jumps and landings, reloads, bomb plant
  and defuse;
- the situation: spawn areas at round start, and the site where the bomb is planted.

Every source is modelled with generous ranges and long memory. Only moments where none of these sources
applies are treated as "the player had no legitimate information". This model exists mainly to prevent
false alarms.

Knowing *that* an enemy is around is not the same as knowing *exactly where* he is. A footstep tells you
roughly where someone was; a sighting three seconds ago tells you where he was three seconds ago. So each
source also gives an **estimate of the enemy's position with a margin of error**: the last sighting
(optionally carried forward along the enemy's movement for a moment), where a sound was made plus a margin
because hearing gives a direction rather than a point, and coarser margins for radar, callouts and damage.
For hidden enemies the analyzer measures how far the *best* of these estimates is from the enemy's real
position. It picks the best one knowing the real position, which favours the player.

## Step 4: measurements (what is tracked)

The match is split into **encounters** (a player and an enemy over a stretch of time) and into individual
shots, flicks and sprays. A set of detectors measures every instance, whether or not it looks unusual. All
these measurements are stored as numbers, because they are what the population comparisons and the
player's play pattern are built from. A detector raises an **evidence event** only when a measurement is
far outside what legitimate players do.

### Use of information the player should not have

| Measurement | What it looks at | Protections against false alarms |
|---|---|---|
| Tracking hidden enemies | Whether the crosshair follows an enemy that is moving behind a wall, specifically the movement *caused by the enemy* (not by the player's own strafing). | Only clearly hidden and unknown moments count; the enemy must move for a while; direction changes must be matched; aiming at the last known position counts much less. |
| Tracking through smoke | The same, restricted to the stable core of a smoke. | Edges, blooming, fading and damaged smokes are excluded; a kill through smoke alone is not evidence. |
| Tracking while flashed | Following a moving enemy while heavily blinded. | Only long, strong flashes; moments with audible sound are excluded. |
| Aim on the current hidden position | Whether aim stays closer to where a hidden enemy *is now* than to where they were a moment ago. | Match-level statistics with confidence intervals; the enemy must have moved. |
| Pre-aim before visibility | Whether aim converges on an enemy's real position before they become visible. | Holding an angle an enemy walks into does not count; pre-aiming a common spot does not count. Kept at low weight because good players pre-aim well. |
| Aim near hidden enemies | How often the crosshair rests near hidden enemies, compared with the same player's movement shifted in time (a "what would chance give" baseline). | Needs a lot of data; very low weight, because common angles explain much of it. |
| Following hidden enemies beyond what could be known | Over the whole match: how much of hidden enemies' own movement (behind walls or the stable core of a smoke) the crosshair stays on, at moments when the best legitimate estimate of their position is clearly off. Also how many first shots at such enemies land on target. A wallhack lets a player follow an enemy, including his direction changes, again and again; sound and old sightings do not. | Only the enemy's own movement counts, so holding an angle an enemy walks into adds almost nothing. Estimates are generous and only information from before each moment counts. It takes both a large amount and a large share of tracked movement over the match; clean players typically keep the crosshair on about 1% of it, and no clean player in the calibration data reached the bar. First shots only add weight. |
| Kills through smoke | How many of a player's kills the game itself records as going through a smoke. A wallhack shows enemies inside and behind smokes. | One lucky spray is common: it takes many such kills that are also a large share of the player's kills; clean players rarely have more than a handful in a match. |

### Aim and shooting mechanics

| Measurement | What it looks at | Protections against false alarms |
|---|---|---|
| Target acquisition | Reaction time and how directly the crosshair travels onto a newly visible enemy, including the smoothness (jerk) of that movement. Aim-assist software tends to move unusually smoothly. | The server sees visibility slightly later than the player's screen, so bounds are wide; weak weight. |
| Flicks | Large, fast flicks that land on the target with no overshoot and fire instantly. | A flick alone is never evidence; repetition is needed. |
| Aim attraction | Whether small aim corrections drift toward nearby enemies (soft aim). | Moving toward visible enemies is normal; only hidden-enemy bias with lots of samples counts. |
| Target switching | Very fast, large switches to a second enemy right after a kill. | Weak weight; repetition needed. |
| Trigger timing | Whether shots are fired consistently within a tick or two of the crosshair touching an enemy, with unusually low variation (trigger bots). | Sprays, blindness, shotguns, held angles and prefires are excluded; needs several samples. |
| Recoil control | Whether spray compensation is too exact and too repeatable compared with the actual recoil pattern. | Good recoil control is normal; needs several long sprays per weapon. |
| View vs bullet direction | The difference between where the view points and where the bullet actually went and hit. | Recorded as a measurement for the play pattern; does not create events on its own. |

### Signs no human input produces

| Measurement | What it looks at | Protections against false alarms |
|---|---|---|
| Silent aim | The view jumps to a target on exactly the firing tick and is back on the next tick. | The jump must be far larger than the movement around it, so fast human flicks do not qualify; must happen repeatedly in a match. |
| Anti-aim | The view pitch held at the game's up/down limit while moving and alive. | AFK and dead players excluded. |
| Mouse-input lattice | With a mouse, every view change is a whole number of mouse "counts" times the player's sensitivity. The detector learns each player's step size from their ordinary movement, then checks whether aim just before shots leaves that grid, which happens when software moves the view. | Players whose ordinary movement fits no grid (controllers, mouse acceleration, sensitivity changed mid-match) never get evidence from this; the off-grid rate before shots must be several times the player's own rate elsewhere and statistically extreme. |

Some measurements separate cheaters from legitimate players but were **left out of scoring** because
professional players show them just as strongly: how fast a target is acquired, and how early aim
converges before an enemy becomes visible. They are still recorded, but they measure skill as much as
cheating.

## Step 5: from events to a match score

Evidence events are scored by how extreme the measurement is, how reliable that detector is known to be,
and how sure the knowledge model is that the player had no legitimate information. Then:

1. **Incidents, not events.** Events about the same target in the same moment (within a few seconds) and
   of the same kind are merged into one incident. One moment seen by three detectors counts once.
2. **Repetition.** Incidents are grouped by type (hidden information, aim, timing, recoil, impossible
   input, decision-making). A type with only one incident is capped just below Elevated, so one play can
   never lift a match on its own.
3. **Families, not averages.** Types are grouped into two families that tend to go together: information
   (seeing what you should not) and mechanics (aim, timing, recoil, impossible input). Within a family the
   strongest type dominates. When both families are independently unusual, a small corroboration bonus is
   added.
4. **The play pattern over the whole match.** Single events rarely separate cheaters from legitimate
   players; a player's *typical* behavior over a whole match does. So every measurement of five features
   (aim error to hidden enemies, time aimed right at hidden enemies, view-vs-bullet difference, trigger
   time, and acquisition smoothness), plus how well the player's movement fits a mouse-input grid, is
   compared with how often that value occurs for labelled cheaters versus clean players. These comparisons
   add up to a pattern score, which is placed among clean players **on the same map**: each of the nine
   maps in the dataset (Ancient, Anubis, Dust2, Inferno, Mirage, Nuke, Overpass, Train, Vertigo) has its
   own clean reference of 290 to 430 players, and any other map uses one reference pooled over all nine.
   Around the top 2% of clean players on each map reaches Elevated. The pattern alone is capped below High and never counts as independent
   corroboration, because it re-uses the same measurements. This is why a match can be Elevated with no
   single evidence event; the site then shows a "pattern" mark and explains it in "Why this class".
5. **Enough data.** A player with too few encounters or rounds in a match gets "Not enough data".

## Step 6: across matches

A player's overall class combines all their analyzed matches:

- Match scores are averaged as if every player also had a few extra, perfectly normal matches, so one or
  two unusual matches cannot give a high overall class, and many mildly unusual matches do not add up to
  one either. Repeatedly strong matches do add up.
- The play pattern is combined separately and needs at least two matches. Newer matches count more than
  old ones (weight halves every six months), a single extreme match is capped, and the calculation assumes
  that a player's habits repeat from match to match, so only *consistently* unusual matches lift the class.
- A sudden jump of the latest match above the player's earlier ones is noted separately.

## Classes

| Class | Meaning |
|---|---|
| Normal | No unusual behavior beyond what legitimate players show. |
| Elevated | Some unusual behavior worth a look. Good players and luck produce this too. |
| High | Repeated, strong unusual behavior. Review the evidence before drawing conclusions. |
| Not enough data | Too little play in analyzed matches to say anything. |

The score behind a class is an evidence score from 0 to 1, not a percentage and not a probability.

## Evidence you can check

Each evidence event stores the round, the exact ticks, the target, the visibility and knowledge context,
the raw measurements and a written explanation. For the strongest events the site shows a short
**reconstruction clip**: a top-down map and a 3D view drawn from the recorded positions and view angles
on the map's geometry. Clips are reconstructions, not game footage, and the enemy outline in them is a
reviewer's aid, not what the player saw.

## How well it works

The scoring was fitted and checked on the public
[CS2CD dataset](https://huggingface.co/datasets/CS2CD/CS2CD.Counter-Strike_2_Cheat_Detection)
(first on 121 Mirage matches: 41 without cheaters, 80 with players whose accounts were later VAC-banned)
and on 15 professional tournament matches. Figures were measured on matches the model was not fitted on.
The first table is from Mirage.

| Share reaching Elevated or above | Clean players | Labelled cheaters | Pros |
|---|---|---|---|
| From evidence events only | 0.3% | 7.3% | 0.7% |
| Events plus play pattern | 1.8% | 30.6% | 0.7% |
| High or above | 0.0% | 3.5% | 0.0% |

In plain words: most cheaters are **not** caught from one match, and roughly 1 in 50 clean players gets an
Elevated match. That is exactly why Elevated is not a verdict, why one match counts for little, and why the
class across matches matters more. The silent-aim and anti-aim checks fired for no clean player except six
in the "clean" set who were visibly rage-cheating (and were removed from the clean baseline), and for no
professional player.

The play pattern has since been refitted on all nine dataset maps that have a game-built map model (626
matches: 323 without cheaters and 303 with a total of 1,244 labelled cheaters), with a clean reference per map.
Measured on held-out matches, the share whose play pattern alone reaches Elevated:

| Play pattern alone | Clean players | Labelled cheaters |
|---|---|---|
| Before (Mirage reference used on every map) | 1.6%, from 0.2% to 4.2% depending on the map | 26.7% |
| Now (a reference per map) | 2.8%, from 1.6% to 3.6% depending on the map | 37.4% |

The clean share is now about the same on every map, which is the point: before, the same score meant
something different on Anubis than on Dust2. It lands slightly above 2% in this test because each test
reference was built from only half of a map's clean players; the shipped references use all of them. Of
the 140 pro players, 1 reaches Elevated from the pattern with either reference.

The mouse-input check was also run on 31 real matchmaking demos from nine maps and the 15 pro matches:
456 of 465 player slots fitted a mouse grid, and no player there met its evidence criteria.

The check for following hidden enemies beyond what could be known was calibrated on the 174 CS2CD Mirage
and Nuke matches: it flags no clean player (714) and no unlabelled player in cheater matches (600), and
flags 46 of 420 labelled cheaters (23 of 98 on Nuke, 23 of 322 on Mirage). Smokes in that data rarely
hide players behind a stable core for long, so the check is better calibrated for walls than for smokes.

Detector thresholds are described here as ranges and percentiles rather than exact values, so that the
detection is not trivial to tune a cheat against. Every threshold lives in the source code's
configuration.

## Limitations

- Demos record view angles 64 times a second; anything faster is invisible, so reaction and trigger times
  have a resolution of about 16 ms.
- There are no hitboxes in a demo; hit points are estimated from body positions.
- Voice is not observable; it is modelled as teammates' sight staying useful for a while.
- The play-pattern references come from one dataset, and the dataset's own tick data lacks a few details
  real demos have (for example the exact bullet direction). Maps outside its nine use the pooled reference.
- The dataset's matches are most likely from an older game version than the map models, so small map changes since
  then can blur the aim-to-hidden-enemy measurements.
- Closet cheating that only uses information occasionally, or that imitates human aim well, is hard to
  see in any single match.
- Decision-making analysis (routes, rotations, utility timing that uses hidden information) is still
  basic.

## What Cheatscanner never does

- It never reads or changes CS2's memory and never injects anything into the game.
- It never reads text chat or voice.
- It keeps no copies of demos: they are deleted after analysis. See the Privacy page for what is stored.
