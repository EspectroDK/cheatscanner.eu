"""Parser abstraction.

The rest of the system only depends on :class:`ParsedDemo`, never on a concrete
parser library, so ``demoparser2`` can later be swapped for another parser
(or a Rust module) by implementing :class:`DemoParserBackend`.

Normalized conventions (enforced by every backend):

* ``steam_id`` is a 64-bit integer (``int64``) everywhere.
* Angles are in degrees. ``view_pitch`` positive = looking *down* (Source).
  ``view_yaw`` is normalized to [-180, 180).
* Positions are Hammer units. ``position_*`` is the player origin (feet);
  ``eye_*`` is the estimated eye position (see ``docs/parser-notes.md``).
* ``tick`` is the demo tick; ``game_time`` seconds.
* Event tables share the columns ``tick`` and one or more ``*_steam_id``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import pandas as pd


@dataclass
class MatchMeta:
    match_id: str
    source: str
    map_name: str
    mode: str | None
    mode_source: str | None
    played_at: str | None
    server_name: str | None
    patch_version: str | None
    tickrate: float
    demo_sha256: str
    first_tick: int
    last_tick: int
    parser_name: str
    parser_version: str
    extra: dict = field(default_factory=dict)


@dataclass
class ParsedDemo:
    """Everything downstream analysis needs from a demo.

    ``ticks`` is the normalized per-player per-tick table (see
    ``parser/normalize.py`` for the column list). ``events`` maps a normalized
    event name to a DataFrame:

    ``shots``        tick, steam_id, weapon
    ``bullets``      tick, steam_id, weapon_id, origin_*, angle_pitch, angle_yaw, recoil_index, inaccuracy
    ``hurts``        tick, attacker_steam_id, victim_steam_id, weapon, hitgroup, dmg_health, dmg_armor, health
    ``deaths``       tick, attacker_steam_id, victim_steam_id, assister_steam_id, weapon, headshot,
                     penetrated, thrusmoke, attackerblind, noscope, hitgroup
    ``smokes``       entity_id, start_tick, end_tick, x, y, z, thrower_steam_id
    ``he_grenades``  tick, x, y, z, steam_id
    ``flashes``      tick, x, y, z, steam_id
    ``blinds``       tick, victim_steam_id, attacker_steam_id, blind_duration
    ``footsteps``    tick, steam_id           (as recorded; known to be incomplete)
    ``jumps``        tick, steam_id
    ``reloads``      tick, steam_id
    ``bomb``         tick, steam_id, action (beginplant/planted/begindefuse/defused/exploded/dropped/pickup), site
    """

    meta: MatchMeta
    players: pd.DataFrame
    rounds: pd.DataFrame
    ticks: pd.DataFrame
    events: dict[str, pd.DataFrame]
    ranks: pd.DataFrame | None = None

    def event(self, name: str) -> pd.DataFrame:
        return self.events.get(name, pd.DataFrame())


class DemoParserBackend(Protocol):
    name: str

    @property
    def version(self) -> str: ...

    def parse(self, path: Path, match_id: str | None = None) -> ParsedDemo: ...
