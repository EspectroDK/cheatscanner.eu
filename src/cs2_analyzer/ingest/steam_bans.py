"""Steam Web API: VAC and game bans on record for an account (``ISteamUser/GetPlayerBans/v1``).

A separate, factual layer next to the evidence classes: a ban is account-level, can come from another game,
and usually arrives long after a match, so it never feeds scoring, incidents, classes or the siren. The site
shows it only when there is a VAC or game ban; "no ban" is never shown as a clean result.

Steam answers for up to 100 SteamIDs per request, for private profiles too (ban notices are public on every
Steam profile). A normal Web API key gets totals only: ``NumberOfVACBans``, ``NumberOfGameBans``,
``DaysSinceLastBan``, ``CommunityBanned`` and ``EconomyBan``, not which game a ban is from.

Results are kept in ``steam_bans`` and asked again after ``ttl`` (default a day). Without an API key nothing is
looked up and no ban is shown; when Steam can't be reached, the last known answers are used.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Callable, Iterable

log = logging.getLogger(__name__)

URL = "https://api.steampowered.com/ISteamUser/GetPlayerBans/v1/"
BATCH = 100   # Steam's limit of SteamIDs per request

# (url, timeout) -> (status, body)
HttpGet = Callable[[str, float], tuple[int, str]]


class SteamUnavailable(Exception):
    """Network error, rate limit or an unexpected answer."""


def _default_get(url: str, timeout: float) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
            return resp.status, resp.read(512 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def _int(v) -> int:
    try:
        return max(0, int(v))
    except (TypeError, ValueError):
        return 0


def fetch_bans(api_key: str, steam_ids: list[int], http_get: HttpGet | None = None,
               timeout: float = 5.0) -> dict[int, dict]:
    """Ban record per SteamID (at most ``BATCH`` per call): vac_bans, game_bans, days_since_last_ban, ..."""
    if not steam_ids:
        return {}
    if len(steam_ids) > BATCH:
        raise ValueError(f"at most {BATCH} SteamIDs per request")
    url = URL + "?" + urllib.parse.urlencode({"key": api_key, "steamids": ",".join(str(s) for s in steam_ids)})
    try:
        status, body = (http_get or _default_get)(url, timeout)
    except Exception as exc:  # the URL holds the key: never include it in messages
        raise SteamUnavailable(f"could not reach Steam ({type(exc).__name__})") from None
    if status != 200:
        raise SteamUnavailable(f"Steam answered HTTP {status}")
    try:
        players = json.loads(body)["players"]
        out = {}
        for p in players:
            out[int(p["SteamId"])] = {
                "vac_bans": _int(p.get("NumberOfVACBans")) or (1 if p.get("VACBanned") else 0),
                "game_bans": _int(p.get("NumberOfGameBans")),
                "days_since_last_ban": _int(p.get("DaysSinceLastBan")),
                "community_banned": bool(p.get("CommunityBanned")),
                "economy_ban": str(p.get("EconomyBan") or "none")[:32],
            }
        return out
    except (ValueError, KeyError, TypeError):
        raise SteamUnavailable("unexpected answer from Steam") from None


def public_bans(row: dict | None, steam_id: int) -> dict | None:
    """The API/website shape of a stored ban record, or None when there is no VAC or game ban on record."""
    if not row or (row["vac_bans"] <= 0 and row["game_bans"] <= 0):
        return None
    checked: datetime = row["checked_at"]
    last_ban: date = (checked - timedelta(days=row["days_since_last_ban"])).date()
    return {"vacBans": row["vac_bans"], "gameBans": row["game_bans"],
            "daysSinceLastBan": max(0, (datetime.now(timezone.utc).date() - last_ban).days),
            "lastBanOn": last_ban.isoformat(), "communityBanned": row["community_banned"],
            "economyBan": row["economy_ban"], "checkedAt": checked.isoformat(),
            "profileUrl": f"https://steamcommunity.com/profiles/{steam_id}",
            "note": "Account-level ban on record at Steam. Not a statement about any analyzed match."}


class BanLookup:
    """Ban records for a set of players: from the database, asking Steam for the ones older than ``ttl``."""

    def __init__(self, db, api_key: str, http_get: HttpGet | None = None, ttl: timedelta = timedelta(days=1),
                 timeout: float = 5.0, backoff_s: float = 300.0):
        self.db, self.api_key, self.http_get = db, api_key, http_get
        self.ttl, self.timeout, self.backoff_s = ttl, timeout, backoff_s
        self._lock = threading.Lock()
        self._down_until = 0.0   # after a failure, don't make every request wait for Steam

    @property
    def enabled(self) -> bool:
        return bool(self.api_key)

    def refresh(self, steam_ids: Iterable[int], force: bool = False) -> None:
        """Ask Steam about the players whose record is missing or stale. Never raises."""
        if not self.enabled:
            return
        ids = sorted({int(s) for s in steam_ids if 0 < int(s) < 1 << 64})
        if not ids:
            return
        if not force:
            fresh = self.db.fresh_ban_ids(ids, datetime.now(timezone.utc) - self.ttl)
            ids = [s for s in ids if s not in fresh]
        with self._lock:
            if not ids or time.monotonic() < self._down_until:
                return
        for i in range(0, len(ids), BATCH):
            chunk = ids[i:i + BATCH]
            try:
                found = fetch_bans(self.api_key, chunk, self.http_get, self.timeout)
            except SteamUnavailable as exc:
                log.warning("Steam ban lookup failed: %s", exc)
                with self._lock:
                    self._down_until = time.monotonic() + self.backoff_s
                return
            # An ID Steam doesn't answer for (no such account) is stored as no bans, so it isn't asked every time.
            empty = {"vac_bans": 0, "game_bans": 0, "days_since_last_ban": 0, "community_banned": False,
                     "economy_ban": "none"}
            self.db.save_bans({s: found.get(s, empty) for s in chunk}, datetime.now(timezone.utc))

    def get(self, steam_ids: Iterable[int]) -> dict[int, dict | None]:
        """``public_bans`` per SteamID (None without a VAC/game ban or without an API key)."""
        ids = [int(s) for s in steam_ids]
        if not self.enabled or not ids:
            return {s: None for s in ids}
        self.refresh(ids)
        rows = self.db.bans(ids)
        return {s: public_bans(rows.get(s), s) for s in ids}


def from_config(config, db, http_get: HttpGet | None = None) -> BanLookup:
    """The lookup as configured (``[steam_bans]``); disabled without an API key or with ``enabled = false``."""
    key = str(config.get("auth.steam_api_key", "") or "") if config.get("steam_bans.enabled", True) else ""
    return BanLookup(db, key, http_get=http_get, ttl=timedelta(hours=float(config.get("steam_bans.refresh_hours", 24))),
                     timeout=float(config.get("steam_bans.timeout_s", 5)))
