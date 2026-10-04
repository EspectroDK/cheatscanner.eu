"""``demoparser2`` implementation of :class:`DemoParserBackend`.

Field names were taken from inspecting real parser output of a Valve Premier
demo (demoparser2 0.42), not assumed. Findings are recorded in
``docs/parser-notes.md``. Notable ones:

* ``velocity_X/Y/Z`` are NaN in Valve MM demos, so velocity is *derived* from
  positions (marked ``velocity_source = "derived"`` in metadata).
* There is no eye-position prop. ``fire_bullets.origin_z - Z`` shows the eye
  sits 64u above the origin standing and ~46u crouched, so the eye height is
  ``64 - 18 * duck_amount``. The residual against ``fire_bullets`` is logged
  in metadata so every run documents its own error.
* ``fire_bullets.angles == view + 2 * aim_punch_angle`` (median error ~0.1 deg),
  i.e. the bullet direction includes recoil scaled by 2 while ``pitch/yaw``
  is the player's (crosshair) view. Since CS2 patch ~14180 demoparser2's
  ``aim_punch_angle`` comes back empty; the same angle is networked as
  ``CCSPlayer_AimPunchServices.m_predictableBaseAngle`` (same relation,
  median error ~0.1 deg), which is read as a fallback.
* ``player_footstep`` events are sparse in SourceTV demos; the knowledge
  model therefore also derives possible footstep noise from movement.
"""

from __future__ import annotations

import hashlib
import re
from importlib import metadata
from pathlib import Path

import numpy as np
import pandas as pd

from cs2_analyzer.parser.base import MatchMeta, ParsedDemo
from cs2_analyzer.parser.normalize import (
    AIM_PUNCH_SERVICES_PROP,
    EYE_HEIGHT_CROUCH,
    EYE_HEIGHT_STANDING,
    normalize_ticks,
    punch_angles,
)
from cs2_analyzer.memory import release_memory

TICK_PROPS = [
    "X", "Y", "Z", "pitch", "yaw", "health", "team_num", "is_alive",
    "active_weapon_name", "m_iClip1", "aim_punch_angle", "flash_duration",
    "flash_max_alpha", "is_scoped", "duck_amount", "ducking", "is_walking",
    "is_airborne", "shots_fired", "buttons", "FIRE", "spotted",
    "approximate_spotted_by", "game_time", "team_rounds_total",
    # newer demos: aim punch moved here (unknown fields are skipped on older demos)
    AIM_PUNCH_SERVICES_PROP,
]
# ``ducking`` itself is not used, but demoparser2 0.42 only fills ``FIRE`` when it is
# parsed in the same call.

# Ticks are parsed in slices of this many ticks: demoparser2 builds every requested
# value of a call before handing it over, so one call for the whole match briefly
# needs several times the memory of the finished table. Each slice is made compact
# (see ``_compact``) before the next one is parsed.
TICK_CHUNK = 32768

# object columns (bool with missing values) turned into what normalize_ticks makes of them
_BOOL_PROPS = ("FIRE", "is_scoped", "is_walking", "spotted")

RANK_TYPES = {11: "premier", 12: "competitive", 7: "wingman", 10: "danger_zone"}

_MATCH_FILE_RE = re.compile(r"match730_(\d+)_(\d+)_(\d+)")


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _to_sid(v) -> int:
    try:
        if v is None or (isinstance(v, float) and v != v):
            return 0
        return int(str(v).split(".")[0]) if not isinstance(v, (int, np.integer)) else int(v)
    except (TypeError, ValueError):
        return 0


def _sid(series: pd.Series) -> pd.Series:
    """Steam IDs arrive as str in events and uint64 in ticks; normalize to int64.

    Conversion is done per element in Python ints: going through float64 (as
    ``pd.to_numeric`` does when values are missing) silently corrupts 17-digit
    IDs.
    """
    if series.dtype.kind in "iu":
        return series.astype("int64")
    return pd.Series([_to_sid(v) for v in series], index=series.index, dtype="int64")


def _event(parser, name: str, available: set[str]) -> pd.DataFrame:
    if name not in available:
        return pd.DataFrame()
    df = parser.parse_event(name)
    return df if df is not None else pd.DataFrame()


def smoke_expiries(det: pd.DataFrame, exp: pd.DataFrame) -> list[int]:
    """Expiry tick for each smoke detonation, -1 when none is found.

    Entity ids are reused during a match, so each detonation is paired with the
    first expiry of the same entity at or after it, not with that entity's first
    expiry in the demo (which dropped some smokes and kept others all match).
    """
    by_ent = {e: np.sort(g["tick"].to_numpy()) for e, g in exp.groupby("entityid")} if len(exp) else {}
    out = []
    for ent, start in zip(det["entityid"], det["tick"]):
        ticks = by_ent.get(ent)
        i = int(np.searchsorted(ticks, start)) if ticks is not None else 0
        out.append(int(ticks[i]) if ticks is not None and i < len(ticks) else -1)
    return out


def _compact(raw: pd.DataFrame) -> pd.DataFrame:
    """One slice of ``parse_ticks`` output with its bulky object columns made compact.

    Rows without a steam id are dropped, nullable bools become what ``normalize_ticks``
    makes of them, aim punch lists become ``aim_punch_pitch``/``aim_punch_yaw``, and
    columns nothing reads (``name``, ``ducking``) go.
    """
    sid = _sid(raw["steamid"])
    keep = (sid != 0).to_numpy()
    out = pd.DataFrame({"tick": raw["tick"][keep], "steamid": sid[keep]})
    for c in raw.columns:
        if c in ("tick", "steamid", "name", "ducking", "aim_punch_angle", AIM_PUNCH_SERVICES_PROP):
            continue
        col = raw[c][keep]
        out[c] = col.fillna(False).astype(bool) if c in _BOOL_PROPS else col
    if "aim_punch_angle" in raw or AIM_PUNCH_SERVICES_PROP in raw:
        out["aim_punch_pitch"], out["aim_punch_yaw"] = punch_angles(raw[keep], [])
    return out.reset_index(drop=True)


# every game event the backend reads (``_events``, ``_ranks``, ``_rounds``)
GAME_EVENTS = [
    "weapon_fire", "fire_bullets", "player_hurt", "player_death", "smokegrenade_detonate", "smokegrenade_expired",
    "hegrenade_detonate", "flashbang_detonate", "player_blind", "player_footstep", "player_jump", "weapon_reload",
    *(f"bomb_{a}" for a in ("beginplant", "planted", "begindefuse", "defused", "exploded", "dropped", "pickup")),
    "rank_update", "round_start", "round_prestart", "round_freeze_end", "round_end",
]


class _Prefetched:
    """Answers ``parse_event``/``parse_player_info`` from results fetched up front.

    All events come from one ``parse_events`` call (one pass over the demo instead of one
    per event), and the parser (it holds the whole demo file) can be released early.
    """

    def __init__(self, parser, available: set[str]):
        wanted = [e for e in GAME_EVENTS if e in available]
        self.events = {name: df for name, df in parser.parse_events(wanted)} if wanted else {}
        self._player_info = parser.parse_player_info()

    def parse_event(self, name: str) -> pd.DataFrame:
        if name not in GAME_EVENTS:
            raise KeyError(f"{name} is not in GAME_EVENTS")
        df = self.events.get(name)
        return df if df is not None else pd.DataFrame()  # the demo has none of it

    def parse_player_info(self) -> pd.DataFrame:
        return self._player_info


class DemoParser2Backend:
    name = "demoparser2"

    @property
    def version(self) -> str:
        try:
            return metadata.version("demoparser2")
        except metadata.PackageNotFoundError:  # pragma: no cover
            return "unknown"

    def parse(self, path: Path, match_id: str | None = None) -> ParsedDemo:
        from demoparser2 import DemoParser  # imported lazily: optional at test time

        path = Path(path)
        parser = DemoParser(str(path))
        header = parser.parse_header()
        available = set(parser.list_game_events())

        # Events first (one pass), while little else is in memory; the parser (it holds
        # the whole demo file) is let go before the tick table is normalized.
        cached = _Prefetched(parser, available)
        events = self._events(cached, available)
        ranks = self._ranks(cached, available)
        last_event_tick = max([int(df["tick"].max()) for df in cached.events.values() if len(df) and "tick" in df]
                              + [0])
        raw_ticks = self._parse_ticks(parser, last_event_tick)
        del parser
        release_memory()

        tickrate = self._tickrate(raw_ticks)
        rounds = self._rounds(cached, available, raw_ticks)
        ticks = normalize_ticks(raw_ticks, rounds, tickrate)
        del raw_ticks
        release_memory()

        players = self._players(cached, ticks)

        sha = _sha256(path)
        mid, source = self._match_id(path, match_id, sha)
        mode, mode_source = self._mode(ranks, header)
        eye_check = self._eye_height_check(events.get("bullets"), ticks)

        meta = MatchMeta(
            match_id=mid,
            source=source,
            map_name=header.get("map_name", "unknown"),
            mode=mode,
            mode_source=mode_source,
            played_at=None,  # not present in the demo header; supplied by caller if known
            server_name=header.get("server_name"),
            patch_version=header.get("patch_version"),
            tickrate=tickrate,
            demo_sha256=sha,
            first_tick=int(ticks["tick"].min()) if len(ticks) else 0,
            last_tick=int(ticks["tick"].max()) if len(ticks) else 0,
            parser_name=self.name,
            parser_version=self.version,
            extra={
                "header": {k: v for k, v in header.items() if k != "demo_file_stamp"},
                "velocity_source": "derived_from_positions",
                "eye_position_source": f"origin_z + {EYE_HEIGHT_STANDING} - "
                f"{EYE_HEIGHT_STANDING - EYE_HEIGHT_CROUCH} * duck_amount",
                "eye_position_check": eye_check,
                "missing_props": ticks.attrs.get("missing_props", []),
            },
        )
        return ParsedDemo(meta=meta, players=players, rounds=rounds, ticks=ticks, events=events, ranks=ranks)

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _parse_ticks(parser, last_event_tick: int = 0, chunk: int = TICK_CHUNK) -> pd.DataFrame:
        """``parse_ticks(TICK_PROPS)`` for the whole demo, ``chunk`` ticks per call.

        Players without a steam id (bots, GOTV) are dropped. Slices are read until the demo
        ends (a slice after ``last_event_tick`` that stops short of its last tick).
        """
        parts = []
        start = 0
        while True:
            g = parser.parse_ticks(TICK_PROPS, ticks=list(range(start, start + chunk)))
            start += chunk
            # the demo ended inside this slice: no rows, or none in its last tick
            ended = start > last_event_tick and (not len(g) or int(g["tick"].max()) < start - 1)
            if len(g):
                parts.append(_compact(g))
            del g
            release_memory()
            if ended:
                break
        if not parts:  # nothing in the expected tick range: one plain call decides
            parts.append(_compact(parser.parse_ticks(TICK_PROPS)))
        raw = pd.concat(parts, ignore_index=True)
        del parts
        return raw

    @staticmethod
    def _tickrate(raw: pd.DataFrame) -> float:
        g = raw[["tick", "game_time"]].drop_duplicates("tick").sort_values("tick")
        dt = np.diff(g["game_time"].to_numpy())
        dtick = np.diff(g["tick"].to_numpy())
        ok = (dtick > 0) & (dt > 0)
        if not ok.any():
            return 64.0
        rate = float(np.median(dtick[ok] / dt[ok]))
        return float(round(rate))

    @staticmethod
    def _score_round_ends(raw_ticks: pd.DataFrame | None) -> list[dict]:
        """Round ends from team score increments, for demos without ``round_end``.

        Tournament SourceTV demos carry neither ``round_start`` nor
        ``round_end``. A round ends on the tick where exactly one team's
        ``team_rounds_total`` grows by one; the halftime side swap (scores
        exchanged between teams) is not a round end.
        """
        if raw_ticks is None or "team_rounds_total" not in raw_ticks.columns:
            return []
        t = raw_ticks[raw_ticks["team_num"].isin([2, 3])]
        score = t.groupby(["tick", "team_num"])["team_rounds_total"].max().unstack().sort_index().ffill()
        if not {2, 3} <= set(score.columns):
            return []
        d = score.diff()
        rows = []
        for tick, r in d.iterrows():
            if r[2] == 1 and r[3] == 0:
                rows.append({"tick": int(tick), "winner": 2, "reason": None})
            elif r[3] == 1 and r[2] == 0:
                rows.append({"tick": int(tick), "winner": 3, "reason": None})
        return rows

    @staticmethod
    def _rounds(parser, available: set[str], raw_ticks: pd.DataFrame | None = None) -> pd.DataFrame:
        starts = _event(parser, "round_start", available)
        if not len(starts):
            starts = _event(parser, "round_prestart", available)
        freeze = _event(parser, "round_freeze_end", available)
        ends = _event(parser, "round_end", available)
        start_ticks = sorted(starts["tick"].tolist()) if len(starts) else []
        freeze_ticks = sorted(freeze["tick"].tolist()) if len(freeze) else []
        end_rows = ends.sort_values("tick").to_dict("records") if len(ends) else []
        if not end_rows:
            end_rows = DemoParser2Backend._score_round_ends(raw_ticks)
        if not start_ticks and freeze_ticks:
            start_ticks = [max(0, t - 1) for t in freeze_ticks]
        rows = []
        for i, st in enumerate(start_ticks):
            nxt = start_ticks[i + 1] if i + 1 < len(start_ticks) else 1 << 62
            fe = next((t for t in freeze_ticks if st <= t < nxt), None)
            en = next((r for r in end_rows if st <= r["tick"] <= nxt), None)
            rows.append(
                {
                    "round_number": i + 1,
                    "start_tick": int(st),
                    "freeze_end_tick": int(fe) if fe is not None else None,
                    "end_tick": int(en["tick"]) if en else None,
                    "winner": int(en["winner"]) if en and pd.notna(en.get("winner")) else None,
                    "reason": int(en["reason"]) if en and pd.notna(en.get("reason")) else None,
                }
            )
        df = pd.DataFrame(rows, columns=["round_number", "start_tick", "freeze_end_tick", "end_tick", "winner", "reason"])
        # A round is "live" from freeze end to round end; rounds without a
        # freeze end (e.g. a surrender vote) have no live window.
        df["live"] = df["freeze_end_tick"].notna() & df["end_tick"].notna()
        return df

    def _events(self, parser, available: set[str]) -> dict[str, pd.DataFrame]:
        ev: dict[str, pd.DataFrame] = {}

        wf = _event(parser, "weapon_fire", available)
        if len(wf):
            ev["shots"] = pd.DataFrame(
                {"tick": wf["tick"].astype("int64"), "steam_id": _sid(wf["user_steamid"]),
                 "weapon": wf["weapon"].astype(str)}
            )

        fb = _event(parser, "fire_bullets", available)
        if len(fb):
            ev["bullets"] = pd.DataFrame(
                {
                    "tick": fb["tick"].astype("int64"),
                    "steam_id": _sid(fb["user_steamid"]),
                    "weapon_id": fb.get("item_def_index"),
                    "origin_x": fb["origin_x"], "origin_y": fb["origin_y"], "origin_z": fb["origin_z"],
                    "ent_origin_z": fb.get("ent_origin_z"),
                    "angle_pitch": fb["angles_x"], "angle_yaw": fb["angles_y"],
                    "recoil_index": fb.get("recoil_index"), "inaccuracy": fb.get("inaccuracy"),
                }
            )

        hurt = _event(parser, "player_hurt", available)
        if len(hurt):
            ev["hurts"] = pd.DataFrame(
                {
                    "tick": hurt["tick"].astype("int64"),
                    "attacker_steam_id": _sid(hurt["attacker_steamid"]),
                    "victim_steam_id": _sid(hurt["user_steamid"]),
                    "weapon": hurt["weapon"].astype(str),
                    "hitgroup": hurt["hitgroup"].astype(str),
                    "dmg_health": hurt["dmg_health"], "dmg_armor": hurt["dmg_armor"], "health": hurt["health"],
                }
            )

        death = _event(parser, "player_death", available)
        if len(death):
            d = pd.DataFrame(
                {
                    "tick": death["tick"].astype("int64"),
                    "attacker_steam_id": _sid(death["attacker_steamid"]),
                    "victim_steam_id": _sid(death["user_steamid"]),
                    "assister_steam_id": _sid(death["assister_steamid"]) if "assister_steamid" in death else 0,
                    "weapon": death["weapon"].astype(str),
                }
            )
            for col in ["headshot", "penetrated", "thrusmoke", "attackerblind", "noscope"]:
                d[col] = death[col].astype(bool) if col in death else False
            d["hitgroup"] = death["hitgroup"].astype(str) if "hitgroup" in death else None
            ev["deaths"] = d

        det = _event(parser, "smokegrenade_detonate", available)
        exp = _event(parser, "smokegrenade_expired", available)
        if len(det):
            ev["smokes"] = pd.DataFrame(
                {
                    "entity_id": det["entityid"].astype("int64"),
                    "start_tick": det["tick"].astype("int64"),
                    "end_tick": smoke_expiries(det, exp),
                    "x": det["x"], "y": det["y"], "z": det["z"],
                    "thrower_steam_id": _sid(det["user_steamid"]),
                }
            )

        for src, dst in [("hegrenade_detonate", "he_grenades"), ("flashbang_detonate", "flashes")]:
            e = _event(parser, src, available)
            if len(e):
                ev[dst] = pd.DataFrame(
                    {"tick": e["tick"].astype("int64"), "x": e["x"], "y": e["y"], "z": e["z"],
                     "steam_id": _sid(e["user_steamid"])}
                )

        bl = _event(parser, "player_blind", available)
        if len(bl):
            ev["blinds"] = pd.DataFrame(
                {"tick": bl["tick"].astype("int64"), "victim_steam_id": _sid(bl["user_steamid"]),
                 "attacker_steam_id": _sid(bl["attacker_steamid"]), "blind_duration": bl["blind_duration"]}
            )

        for src, dst in [("player_footstep", "footsteps"), ("player_jump", "jumps"), ("weapon_reload", "reloads")]:
            e = _event(parser, src, available)
            if len(e):
                ev[dst] = pd.DataFrame({"tick": e["tick"].astype("int64"), "steam_id": _sid(e["user_steamid"])})

        bomb_rows = []
        for action in ["beginplant", "planted", "begindefuse", "defused", "exploded", "dropped", "pickup"]:
            e = _event(parser, f"bomb_{action}", available)
            for r in e.to_dict("records") if len(e) else []:
                bomb_rows.append(
                    {"tick": int(r["tick"]), "steam_id": _to_sid(r.get("user_steamid")),
                     "action": action, "site": r.get("site")}
                )
        ev["bomb"] = pd.DataFrame(bomb_rows, columns=["tick", "steam_id", "action", "site"])
        return ev

    @staticmethod
    def _players(parser, ticks: pd.DataFrame) -> pd.DataFrame:
        info = parser.parse_player_info()
        info = info.rename(columns={"steamid": "steam_id", "team_number": "start_team"})
        info["steam_id"] = _sid(info["steam_id"])
        info = info[info["steam_id"] != 0]
        # Only real participants (have ticks with a team) are analysed.
        present = ticks.loc[ticks["team"].isin([2, 3]), "steam_id"].unique()
        info = info[info["steam_id"].isin(present)].drop_duplicates("steam_id")
        return info[["steam_id", "name", "start_team"]].reset_index(drop=True)

    @staticmethod
    def _ranks(parser, available: set[str]) -> pd.DataFrame | None:
        r = _event(parser, "rank_update", available)
        if not len(r):
            return None
        out = pd.DataFrame(
            {"steam_id": _sid(r["user_steamid"]), "rank_old": r.get("rank_old"), "rank_new": r.get("rank_new"),
             "rank_type_id": r.get("rank_type_id"), "num_wins": r.get("num_wins")}
        )
        return out.drop_duplicates("steam_id", keep="last")

    @staticmethod
    def _match_id(path: Path, override: str | None, sha: str) -> tuple[str, str]:
        if override:
            return override, "user"
        m = _MATCH_FILE_RE.search(path.name)
        if m:
            return m.group(1), "valve_filename"
        return f"sha256-{sha[:24]}", "content_hash"

    @staticmethod
    def _mode(ranks: pd.DataFrame | None, header: dict) -> tuple[str | None, str | None]:
        if ranks is not None and len(ranks) and ranks["rank_type_id"].notna().any():
            rid = int(ranks["rank_type_id"].mode().iloc[0])
            return RANK_TYPES.get(rid, f"rank_type_{rid}"), "rank_update.rank_type_id"
        if "Valve" in str(header.get("server_name", "")):
            return "valve_matchmaking", "server_name"
        return None, None

    @staticmethod
    def _eye_height_check(bullets: pd.DataFrame | None, ticks: pd.DataFrame) -> dict:
        """Compare our eye estimate with ``fire_bullets.origin_z`` (ground truth at shot time)."""
        if bullets is None or not len(bullets):
            return {"available": False}
        m = bullets.merge(ticks[["tick", "steam_id", "eye_z", "view_pitch", "view_yaw", "aim_punch_pitch",
                                 "aim_punch_yaw"]], on=["tick", "steam_id"])
        if not len(m):
            return {"available": False}
        dz = (m["origin_z"] - m["eye_z"]).abs()
        dp = (m["angle_pitch"] - (m["view_pitch"] + 2 * m["aim_punch_pitch"])).abs()
        dy = ((m["angle_yaw"] - (m["view_yaw"] + 2 * m["aim_punch_yaw"]) + 180) % 360 - 180).abs()
        return {
            "available": True,
            "n": int(len(m)),
            "eye_z_abs_err_median": float(dz.median()),
            "eye_z_abs_err_p90": float(dz.quantile(0.9)),
            "bullet_pitch_err_median_deg": float(dp.median()),
            "bullet_yaw_err_median_deg": float(dy.median()),
        }
