"""Steam Web API: a user's new match share codes.

``ICSGOPlayers_730/GetNextMatchSharingCode`` walks a user's match history one
match at a time. It needs the user's *game authentication code* (the
"steamidkey", created at help.steampowered.com → Counter-Strike 2 → personal
game data) and a share code they already know. Valve documents the answers as:

- 200 with ``nextcode`` = the next share code, or ``"n/a"`` when there is none yet;
- 202 when there is no newer match yet (some responses);
- 403 when the authentication code is wrong for this SteamID;
- 412 when the known share code doesn't belong to this user.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

URL = "https://api.steampowered.com/ICSGOPlayers_730/GetNextMatchSharingCode/v1/"
AUTH_CODE = re.compile(r"^[A-Z0-9]{4}-[A-Z0-9]{5}-[A-Z0-9]{4}$")

# (url, timeout) -> (status, body)
HttpGet = Callable[[str, float], tuple[int, str]]


class AuthCodeRejected(Exception):
    """Steam says the authentication code doesn't match this SteamID (403)."""


class KnownCodeRejected(Exception):
    """Steam says the known share code isn't one of this user's matches (412)."""


class SteamUnavailable(Exception):
    """Network error, missing API key, rate limit or an unexpected answer. Retry later."""


def is_auth_code(code: str) -> bool:
    return bool(AUTH_CODE.match((code or "").strip().upper()))


def _default_get(url: str, timeout: float) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
            return resp.status, resp.read(64 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, ""


def next_share_code(api_key: str, steam_id: int, auth_code: str, known_code: str,
                    http_get: HttpGet | None = None, timeout: float = 10.0) -> str | None:
    """The share code after ``known_code`` in the user's history, or None if there is none yet."""
    if not api_key:
        raise SteamUnavailable("no Steam Web API key configured (CS2A_STEAM_API_KEY)")
    url = URL + "?" + urllib.parse.urlencode(
        {"key": api_key, "steamid": str(steam_id), "steamidkey": auth_code.strip().upper(), "knowncode": known_code})
    try:
        status, body = (http_get or _default_get)(url, timeout)
    except Exception as exc:  # the URL holds the key and the user's code: never include it in messages
        raise SteamUnavailable(f"could not reach Steam ({type(exc).__name__})") from None
    if status == 403:
        raise AuthCodeRejected("Steam rejected the authentication code for this account")
    if status == 412:
        raise KnownCodeRejected("Steam says this share code is not one of your matches")
    if status == 202:
        return None
    if status != 200:
        raise SteamUnavailable(f"Steam answered HTTP {status}")
    try:
        nxt = json.loads(body)["result"]["nextcode"]
    except (ValueError, KeyError, TypeError):
        raise SteamUnavailable("unexpected answer from Steam") from None
    return None if not nxt or nxt == "n/a" else str(nxt)
