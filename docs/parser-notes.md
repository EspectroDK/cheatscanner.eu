# Parser notes (demoparser2 0.42, verified on a real Premier demo)

All facts below were checked against the reference demo (de_mirage, Premier, Valve Helsinki server,
patch 13984; `src/parser/test_demo.dem` in [LaihoE/demoparser](https://github.com/LaihoE/demoparser)).

- **Tickrate** 64. Ticks are the server tick numbers; the world uses dense indices `t = tick - tick0`.
- **SteamIDs** arrive as strings in events and uint64 in tick data. Converting through `pd.to_numeric`
  goes via float64 and silently corrupts 17-digit IDs (e.g. …430 becomes …432), which breaks joins
  between hurts, deaths and players. The backend converts each value through Python `int`.
- **Velocity** props (`velocity_X/Y/Z`) are NaN in Valve matchmaking demos; velocity is derived from
  positions by central difference (values over 1500 u/s treated as teleports/NaN).
- **Eye position** is not recorded; eye Z = Z + 64 − 18 × duck_amount (median error 0.13 u, p90 5.1 u
  against `fire_bullets` origins).
- **View angles**: `pitch`/`yaw` are the view; positive pitch looks down (Source convention).
  `fire_bullets` angles = view + 2 × `aim_punch_angle` (median error ≈0.1°), used by the recoil and
  mechanical-impossibility detectors. From CS2 patch ~14180 `aim_punch_angle` comes back empty; the
  same angle is in `CCSPlayer_AimPunchServices.m_predictableBaseAngle` (same relation, median error
  ≈0.1–0.15°) and is read from there when the old prop is missing.
- **Smokes**: `smokegrenade_detonate` / `smokegrenade_expired` share an `entityid`, but entity ids are
  reused within a match, so each detonation is paired with the first expiry of that entity after it.
- `buttons` is no longer networked in recent demos (only `m_nToggleButtonDownMask`); it is recorded as
  missing and no detector depends on it.
- **Mode**: `rank_update.rank_type_id == 11` means Premier (12 competitive, 7 wingman); with no rank updates the mode is `None`.
- **Match id**: from a Valve filename `match730_<id>_...` when present, else `sha256-<24 hex>` of the file.
- `approximate_spotted_by` (bitmask) and `spotted` exist and are used as radar/teammate information.
- `player_footstep` events are sparse; footstep sound is also derived from movement speed ≥ 135 u/s.
- Not available in demos: sub-tick input timing, hitboxes, client-side visibility, voice.

When a property is missing in a demo the analyzer records `UNKNOWN` / `None` instead of inventing it,
and the eye-height and bullet-angle checks are written to `match.json` (`match.data_checks`) so a parser
change is visible immediately.
