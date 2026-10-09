"""Backend for the CS2CD dataset (pre-parsed demos on Hugging Face).

`CS2CD <https://huggingface.co/datasets/CS2CD/CS2CD.Counter-Strike_2_Cheat_Detection>`_
publishes 795 anonymised matches already run through demoparser2. Each match is
two files next to each other::

    <split>/<n>.parquet   per-player per-tick props (every tick, 10 players)
    <split>/<n>.json      game events by name, plus ``CSstats_info`` (map, rank,
                          matchmaking type) and, in ``with_cheater_present``,
                          ``cheaters`` (anonymised ids of VAC-banned players)

Differences from a raw demo that matter here (checked on real files, see
``docs/parser-notes.md``):

* SteamIDs are replaced by ``Player_1`` .. ``Player_10`` *per match*, so the
  same label in two matches is not the same person. They are mapped to
  synthetic int64 ids that are unique per match (:func:`cs2cd_steam_id`), which
  keeps player history and calibration exclusions from merging strangers.
* There are no ``round_start``/``round_end``/``fire_bullets`` events. Rounds
  start at ``round_prestart`` and end on the tick where the
  ``round_win_status`` prop becomes non-zero; it and ``round_win_reason`` give
  the winner and reason. Without
  ``fire_bullets`` the bullet direction falls back to view + 2 x aim punch.
* ``m_iClip1`` is exported as ``active_weapon_ammo``.
* The dataset has no ``.dem`` header, so server name and patch are unknown; the
  map comes from ``CSstats_info``.
"""

from __future__ import annotations

import csv
import json
import re
from importlib import resources
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from cs2_analyzer.parser.base import MatchMeta, ParsedDemo
from cs2_analyzer.parser.demoparser2_backend import DemoParser2Backend, _sha256
from cs2_analyzer.parser.normalize import EYE_HEIGHT_CROUCH, EYE_HEIGHT_STANDING, normalize_ticks

HF_REPO = "CS2CD/CS2CD.Counter-Strike_2_Cheat_Detection"
HF_RESOLVE = f"https://huggingface.co/datasets/{HF_REPO}/resolve/main"
SPLITS = {"no_cheater_present": 1, "with_cheater_present": 2}

# Parquet columns the analysis needs (the files carry ~220).
TICK_COLUMNS = [
    "tick", "steamid", "game_time", "X", "Y", "Z", "pitch", "yaw", "health", "team_num", "is_alive",
    "active_weapon_name", "active_weapon_ammo", "aim_punch_angle", "flash_duration", "flash_max_alpha",
    "is_scoped", "duck_amount", "ducking", "is_walking", "is_airborne", "shots_fired", "buttons", "FIRE",
    "spotted", "approximate_spotted_by", "round_win_status", "round_win_reason",
]
# Read when the file has them (detectors/mouse_view.py).
OPTIONAL_TICK_COLUMNS = ["usercmd_mouse_dx", "usercmd_mouse_dy"]

_PLAYER_RE = re.compile(r"Player_(\d+)$")
_ID_BASE = 900_000_000_000


def cs2cd_steam_id(split: str, match: int, label: str | int) -> int:
    """Synthetic int64 id for an anonymised ``Player_N`` of one dataset match.

    ``900_000_000_000 + split_code * 10_000_000 + match * 100 + N``; 0 for
    missing ids (bots have none). Deterministic, so labels can be mapped
    without parsing ticks.
    """
    if isinstance(label, str):
        m = _PLAYER_RE.match(label.strip())
        if not m:
            return 0
        label = int(m.group(1))
    return _ID_BASE + SPLITS.get(split, 9) * 10_000_000 + int(match) * 100 + int(label)


def split_and_match(path: Path) -> tuple[str, int | None]:
    """``.../with_cheater_present/12.parquet`` -> (``with_cheater_present``, 12)."""
    split = path.parent.name if path.parent.name in SPLITS else "unknown"
    try:
        return split, int(path.stem)
    except ValueError:
        return split, None


def load_index() -> list[dict]:
    """The bundled dataset index (split, match, map, rank, cheater labels, file size)."""
    with resources.files("cs2_analyzer").joinpath("data/cs2cd_index.csv").open() as fh:
        return list(csv.DictReader(fh))


def match_labels(json_path: Path) -> dict:
    """Match-level metadata and the labelled cheaters' synthetic ids for one match."""
    d = json.loads(Path(json_path).read_text())
    split, num = split_and_match(Path(json_path))
    info = (d.get("CSstats_info") or [{}])[0]
    cheaters = [c.get("steamid") for c in d.get("cheaters", [])]
    return {
        "split": split, "match": num, "csstats": info, "cheater_labels": cheaters,
        "cheater_steam_ids": [cs2cd_steam_id(split, num, c) for c in cheaters] if num is not None else [],
    }


class _JsonEvents:
    """Presents the dataset's JSON events through the demoparser2 calls the
    demoparser2 backend uses, with ``Player_N`` ids already mapped to ints."""

    def __init__(self, data: dict, to_sid):
        self._data = data
        self._to_sid = to_sid

    def list_game_events(self) -> list[str]:
        return [k for k, v in self._data.items() if isinstance(v, list) and v and isinstance(v[0], dict)]

    def parse_event(self, name: str) -> pd.DataFrame:
        df = pd.DataFrame(self._data.get(name, []))
        for c in df.columns:
            if c.endswith("steamid"):
                df[c] = pd.Series([self._to_sid(v) for v in df[c]], index=df.index, dtype="int64")
        return df


class CS2CDBackend:
    name = "cs2cd"
    version = "1"

    def parse(self, path: Path, match_id: str | None = None) -> ParsedDemo:
        path = Path(path)
        if path.suffix != ".parquet":
            path = path.with_suffix(".parquet")
        json_path = path.with_suffix(".json")
        split, num = split_and_match(path)
        num_key = num if num is not None else 0

        def to_sid(v) -> int:
            return cs2cd_steam_id(split, num_key, v) if isinstance(v, str) else 0

        data = json.loads(json_path.read_text())
        events_src = _JsonEvents(data, to_sid)
        available = set(events_src.list_game_events())

        present = set(pq.read_schema(path).names)
        raw = pd.read_parquet(path, columns=TICK_COLUMNS + [c for c in OPTIONAL_TICK_COLUMNS if c in present])
        raw["steamid"] = pd.Series([to_sid(v) for v in raw["steamid"]], index=raw.index, dtype="int64")
        raw = raw[raw["steamid"] != 0]
        raw["m_iClip1"] = raw["active_weapon_ammo"]
        raw["approximate_spotted_by"] = raw["approximate_spotted_by"].map(
            lambda v: [s for s in (to_sid(x) for x in v) if s] if isinstance(v, (list, np.ndarray)) else []
        )

        tickrate = DemoParser2Backend._tickrate(raw)
        rounds = self._rounds(events_src, available, raw)
        ticks = normalize_ticks(raw, rounds, tickrate)
        dp2 = DemoParser2Backend()
        events = dp2._events(events_src, available)
        ranks = DemoParser2Backend._ranks(events_src, available)

        first = ticks[ticks["team"].isin([2, 3])].sort_values("tick").drop_duplicates("steam_id")
        players = pd.DataFrame({
            "steam_id": first["steam_id"].astype("int64"),
            "name": [f"Player_{int(s) % 100}" for s in first["steam_id"]],
            "start_team": first["team"].astype(int),
        }).sort_values("steam_id").reset_index(drop=True)

        info = (data.get("CSstats_info") or [{}])[0]
        cheaters = [c.get("steamid") for c in data.get("cheaters", [])]
        mode, mode_source = DemoParser2Backend._mode(ranks, {})
        if mode is None and info.get("match_making_type"):
            mode = {"Premier Matchmaking": "premier", "Official Matchmaking": "competitive"}.get(
                info["match_making_type"], info["match_making_type"])
            mode_source = "CSstats_info.match_making_type"
        sha = _sha256(path)
        meta = MatchMeta(
            match_id=match_id or (f"cs2cd-{split}-{num}" if num is not None else f"cs2cd-sha256-{sha[:24]}"),
            source="user" if match_id else "cs2cd_dataset",
            map_name=info.get("map", "unknown"),
            mode=mode,
            mode_source=mode_source,
            played_at=None,
            server_name=info.get("server"),
            patch_version=None,
            tickrate=tickrate,
            demo_sha256=sha,
            first_tick=int(ticks["tick"].min()) if len(ticks) else 0,
            last_tick=int(ticks["tick"].max()) if len(ticks) else 0,
            parser_name=self.name,
            parser_version=self.version,
            extra={
                "dataset": HF_REPO,
                "split": split,
                "dataset_match": num,
                "csstats": info,
                "cheater_labels": cheaters,
                "cheater_steam_ids": [to_sid(c) for c in cheaters],
                "velocity_source": "derived_from_positions",
                "eye_position_source": f"origin_z + {EYE_HEIGHT_STANDING} - "
                f"{EYE_HEIGHT_STANDING - EYE_HEIGHT_CROUCH} * duck_amount",
                "eye_position_check": {"available": False, "reason": "dataset has no fire_bullets events"},
                "round_source": "round_prestart/round_freeze_end events + round_win_status prop",
            },
        )
        return ParsedDemo(meta=meta, players=players, rounds=rounds, ticks=ticks, events=events, ranks=ranks)

    @staticmethod
    def _rounds(events_src: _JsonEvents, available: set[str], raw: pd.DataFrame) -> pd.DataFrame:
        """Rounds from ``round_prestart``/``round_freeze_end`` and the tick props.

        A round ends on the first tick where ``round_win_status`` becomes non-zero
        (2 = T, 3 = CT; ``total_rounds_played`` increments on the same tick except
        after the final round); ``round_win_reason`` at that tick is the reason.
        """
        def ticks_of(name):
            return sorted(events_src.parse_event(name)["tick"].astype(int).unique()) if name in available else []

        start_ticks = ticks_of("round_prestart")
        freeze_ticks = ticks_of("round_freeze_end")
        g = raw[["tick", "round_win_status", "round_win_reason"]].drop_duplicates("tick").sort_values("tick")
        status = g["round_win_status"].fillna(0).to_numpy()
        decided = (status != 0) & (np.r_[0, status[:-1]] == 0)
        ends = g[decided].to_dict("records")
        if not start_ticks and freeze_ticks:
            start_ticks = [max(0, t - 1) for t in freeze_ticks]
        rows = []
        for i, st in enumerate(start_ticks):
            nxt = start_ticks[i + 1] if i + 1 < len(start_ticks) else 1 << 62
            fe = next((t for t in freeze_ticks if st <= t < nxt), None)
            en = next((r for r in ends if st <= r["tick"] <= nxt), None)
            rows.append({
                "round_number": i + 1,
                "start_tick": int(st),
                "freeze_end_tick": int(fe) if fe is not None else None,
                "end_tick": int(en["tick"]) if en else None,
                "winner": int(en["round_win_status"]) if en and en["round_win_status"] in (2, 3) else None,
                "reason": int(en["round_win_reason"]) if en else None,
            })
        df = pd.DataFrame(rows, columns=["round_number", "start_tick", "freeze_end_tick", "end_tick", "winner", "reason"])
        df["live"] = df["freeze_end_tick"].notna() & df["end_tick"].notna()
        # Only rounds that were played count (warmup / restart segments have no
        # end), so round numbers match the scoreboard.
        df = df[df["live"]].reset_index(drop=True)
        df["round_number"] = range(1, len(df) + 1)
        return df
