"""Steam sign-in (OpenID 2.0) and optional profile lookup.

Steam is the OpenID provider; it tells us which SteamID64 signed in and nothing
else (no password, no Steam session). The redirect back from Steam is only a
claim: ``verify`` checks it locally and then asks Steam directly
(``openid.mode=check_authentication``) whether Steam really issued it. Only
then is the SteamID trusted.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from typing import Callable

STEAM_OPENID = "https://steamcommunity.com/openid/login"
OPENID_NS = "http://specs.openid.net/auth/2.0"
_IDENTIFIER_SELECT = "http://specs.openid.net/auth/2.0/identifier_select"
_CLAIMED_ID = re.compile(r"^https://steamcommunity\.com/openid/id/(7656119\d{10})$")

# (url, form fields, timeout) -> response body
HttpPost = Callable[[str, dict, float], str]


class OpenIDError(Exception):
    """The sign-in response was not a valid, Steam-confirmed assertion."""


def login_url(return_to: str, realm: str) -> str:
    """URL to send the browser to so the user can sign in on steamcommunity.com."""
    params = {
        "openid.ns": OPENID_NS,
        "openid.mode": "checkid_setup",
        "openid.return_to": return_to,
        "openid.realm": realm,
        "openid.identity": _IDENTIFIER_SELECT,
        "openid.claimed_id": _IDENTIFIER_SELECT,
    }
    return f"{STEAM_OPENID}?{urllib.parse.urlencode(params)}"


def _default_post(url: str, fields: dict, timeout: float) -> str:
    data = urllib.parse.urlencode(fields).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
        return resp.read(64 * 1024).decode("utf-8", "replace")


def verify(params: dict[str, str], expected_return_to: str, http_post: HttpPost | None = None,
           timeout: float = 10.0) -> int:
    """Return the SteamID64 from a Steam OpenID callback, or raise ``OpenIDError``.

    ``params`` are the callback's query parameters. ``expected_return_to`` is the
    callback URL we sent to Steam, without its query string; the assertion must
    have been issued for it (otherwise a response meant for another site could be
    replayed here).
    """
    if params.get("openid.mode") != "id_res":
        raise OpenIDError("sign-in was cancelled or failed")
    if params.get("openid.ns") != OPENID_NS:
        raise OpenIDError("unexpected OpenID namespace")
    if params.get("openid.op_endpoint") != STEAM_OPENID:
        raise OpenIDError("assertion is not from Steam")
    return_to = params.get("openid.return_to", "")
    if return_to.split("?", 1)[0] != expected_return_to:
        raise OpenIDError("assertion was issued for a different return address")
    claimed = params.get("openid.claimed_id", "")
    m = _CLAIMED_ID.match(claimed)
    if not m or params.get("openid.identity") != claimed:
        raise OpenIDError("claimed id is not a Steam profile")
    signed = params.get("openid.signed", "")
    if not {"claimed_id", "identity", "return_to", "response_nonce", "op_endpoint"} <= set(signed.split(",")):
        raise OpenIDError("required fields are not signed")

    fields = {k: v for k, v in params.items() if k.startswith("openid.")}
    fields["openid.mode"] = "check_authentication"
    try:
        body = (http_post or _default_post)(STEAM_OPENID, fields, timeout)
    except Exception as exc:  # network errors: sign-in fails closed
        raise OpenIDError(f"could not reach Steam to confirm sign-in ({type(exc).__name__})") from exc
    answers = dict(line.split(":", 1) for line in body.splitlines() if ":" in line)
    if answers.get("is_valid", "").strip() != "true":
        raise OpenIDError("Steam did not confirm the sign-in")
    return int(m.group(1))


def fetch_profile(steam_id: int, api_key: str, timeout: float = 10.0) -> dict:
    """Public profile summary (name, avatar, profile URL). Empty dict if unavailable.

    Only used for display; sign-in never depends on it.
    """
    if not api_key:
        return {}
    url = ("https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v2/?"
           + urllib.parse.urlencode({"key": api_key, "steamids": str(steam_id)}))
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - fixed https URL
            players = json.loads(resp.read(256 * 1024))["response"]["players"]
        return players[0] if players else {}
    except Exception:  # the key is in the URL, so never log the exception text
        return {}
