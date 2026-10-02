# Geometry and visibility

## Collision mesh

Line of sight is computed by raycasting against the map's physics collision mesh, loaded from
`data/maps/<map>.tri` (little-endian float32 triangles, 9 floats each; a hex-text variant is also
accepted). `cs2-analyzer maps-fetch de_mirage` downloads the public Mirage mesh from
[AtomicBool/cs2-map-parser](https://github.com/AtomicBool/cs2-map-parser) (55 142 triangles, 42 366
after dropping degenerate ones).

For every other map, build the mesh from a local CS2 install with
[`tools/geometry/build_tris.py`](../tools/geometry/build_tris.py), so it matches the installed patch. It
uses Source2Viewer-CLI to print the PHYS block of `maps/<map>/world_physics.vmdl_c` and awpy's
`VphysParser` to keep the "default" collision group (world geometry and bullet-blocking props; glass and
grates marked `passbullets`, player/NPC clips, grenade clips and sky are dropped). With no map names it
builds the game's Active Duty group (`mg_active` in gamemodes.txt). Built on 27 Sep 2026:

| map | triangles | map | triangles |
|---|---|---|---|
| de_ancient | 960 693 | de_mirage | 138 208 |
| de_anubis | 811 314 | de_nuke | 203 607 |
| de_cache | 1 636 623 | de_overpass | 674 453 |
| de_dust2 | 515 155 | de_train | 1 566 104 |
| de_inferno | 2 618 082 | de_vertigo | 265 858 |

The game-built Mirage mesh agrees with the public one on 99.86% of 17 462 sampled enemy sight lines;
where they differ, the game mesh has extra waist-high cover (small props missing from the public mesh),
never the reverse. Against the game's spotting on a matchmaking demo both pass the extra-wall check
below (every spotted-but-occluded pair-tick within 32 ticks of our visible), and the game mesh has fewer
"seen but never spotted" segments (113 against 127). `de_mirage.tri` is still the public mesh because the
CS2CD calibration was built on it.

### Mesh for drawing clips

The public Mirage mesh misses many props (for example the big crates on A site), so the 3D view in
evidence clips can show through them. Clips draw with `evidence.render_maps_dir` (default
`data/maps/render/<map>.tri`) when that file exists, and fall back to the analysis mesh otherwise. Build
it from your own game, which keeps line of sight on the calibrated mesh:

    .awpy/Scripts/python tools/geometry/build_tris.py --s2v path/to/Source2Viewer-CLI.exe --out data/maps/render de_mirage

### Patch and audit

The build writes `<map>.tri.json` with the game patch (steam.inf `PatchVersion` 1.41.8.5 → demo-header
patch 14185). When a demo's patch differs from the mesh's by more than `geometry.patch_tolerance` (10),
`match.json` carries a warning: maps change between patches, and a stale mesh has missing or extra walls.
Demos from Nov 2024 to Apr 2025 (patches 14054–14074) disagreed with today's Train, Inferno and Nuke meshes
far more than current-patch demos did.

A collision mesh can also contain surfaces a player sees through in the match, such as doorways whose
closed-door collision is baked into `world_physics`. That is the dangerous error here: a visible enemy
becomes GEOMETRY_OCCLUDED, and a legitimate player tracking it looks like they use hidden information.
[`tools/geometry/audit_mesh.py`](../tools/geometry/audit_mesh.py) takes current-patch demos, finds runs of
ticks where the game's spotting says the observer saw the target but our model says occluded beyond the
32-tick spotting lag, counts every triangle crossed by those sight lines, and removes triangles that block
at least two such runs (kept in `<map>.removed.tri`). Removing geometry only makes the model more
permissive. Audit of 27 Sep 2026 (patch 14185; 31 recent matchmaking demos plus 21 pro demos from
25–27 Sep 2026):

| map | demos | runs | removed | spotted-but-occluded within lag, after |
|---|---|---|---|---|
| de_ancient | 5 | 0 | 0 | |
| de_anubis | 3 | 2 | 0 | 0.966 |
| de_cache | 4 | 0 | 0 | 1.0 (before) |
| de_dust2 | 4 | 6 | 24 | 1.0 |
| de_inferno | 4 | 2 | 36 | 0.989 |
| de_nuke | 7 | 8 | 57 (a lower-level doorway, among others) | 0.947 (was 0.899) |
| de_overpass, de_vertigo | 0 current | – | 0 | 1.0 on older demos |
| de_train | 0 current | – | 0 | **not validated** (only a Dec 2024 demo) |

The remaining disagreements cannot create hidden-information evidence on their own: the knowledge model
treats the game's `spotted` flag as radar information, so an enemy the game spotted is never UNKNOWN. What
no audit can find is an extra wall where the game did not spot either.

The mesh must match the game patch the demo was recorded on; the patch is recorded in `match.json`.

## Raycasting

`geometry/mesh.py` uses Embree via `embreex` (about 3M rays/s on one core) and falls back to a vectorized
NumPy Möller–Trumbore implementation. A segment is clear when no hit lies between the endpoints,
ignoring hits within 4 u of either end.

## Visibility states (`LOS`)

For every observer–enemy pair and tick, rays go from the observer's eye (Z + 64, crouched Z + 46,
interpolated with duck amount; verified against `fire_bullets` origins at 0.13 u median error) to four
body points of the enemy (head, chest, pelvis, knee):

| state | meaning |
|---|---|
| `DIRECT_VISIBLE` | a body point is clear of geometry and smoke |
| `VISIBLE_THROUGH_SMOKE` | clear only through a smoke's uncertain shell / thin chord |
| `SMOKE_OCCLUDED` | geometry clear, but every ray crosses a stable smoke core |
| `GEOMETRY_OCCLUDED` | every ray blocked by the mesh, with margin |
| `UNKNOWN` | occluded, but within the lateral margin (±18 u) or a 100 ms extrapolation is visible |

Field of view (`in_fov`) is tracked separately (generous ±55° yaw, ±42° pitch); "seen" = visible and
in FOV. `visibility.check(observer, target, tick)` and `cs2-analyzer inspect-visibility` explain one decision.

## Smoke model

Each smoke is two vertical ellipsoids (core 110 × 75 u, shell 175 × 125 u, all UNCALIBRATED) with bloom
(1.25 s) and fade (2 s) phases that are uncertain, HE clearing (260 u, 3 s) and short gunfire holes.
Only core chords over 24 u count as occluding.

## Validation against the game's spotting

`tools/visualization/validate_visibility.py` compares our LOS with the demo's
`approximate_spotted_by`. On the reference Mirage demo ([results](validation/visibility_validation.json)):

- Every one of the 706 pair-ticks where the game marked an enemy spotted but we said
  `GEOMETRY_OCCLUDED` is within 32 ticks (0.5 s) of a tick we mark visible (median 15 ticks). That is
  spotting lag, not a phantom wall in our mesh.
- 190 of 377 of our visible-and-in-FOV segments have no game spotting nearby. They are mostly short
  (median 17 ticks), and rendering the longest ones ([cases](validation/visibility_cases.png)) shows clear
  lines of sight. The game flag is approximate and lags; it misses brief exposures.

Missing walls in our model would make hidden-information detectors *less* sensitive, not more, because
the enemy would count as visible. Extra walls would be the dangerous direction, and none were found.
