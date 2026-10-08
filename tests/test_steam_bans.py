"""Steam ban badges (ingest/steam_bans.py): parsing GetPlayerBans, caching, and where the API shows them.

A separate layer: bans never change a class, and they are shown for every player a page names, outside the
"only players you met" rule, because Steam shows them on every profile anyway.
"""

import json
import urllib.parse
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from cs2_analyzer.api.app import create_app
from cs2_analyzer.ingest.steam_bans import BanLookup, SteamUnavailable, fetch_bans, public_bans
from cs2_analyzer.storage.repository import Database
from test_access import ENEMY, MATE, ME, STRANGER, STRANGER2, _seed
from test_auth import PUBLIC, FakeSteam, _assertion

# The shape Steam documents for ISteamUser/GetPlayerBans/v1 (copied structure, made-up IDs).
def _answer(*players):
    return json.dumps({"players": list(players)})


def _player(sid, vac=0, game=0, days=0, community=False, economy="none"):
    return {"SteamId": str(sid), "CommunityBanned": community, "VACBanned": vac > 0, "NumberOfVACBans": vac,
            "DaysSinceLastBan": days, "NumberOfGameBans": game, "EconomyBan": economy}


class FakeSteamApi:
    def __init__(self, players=(), status=200):
        self.players, self.status, self.calls = {int(p["SteamId"]): p for p in players}, status, []

    def __call__(self, url, timeout):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        ids = [int(s) for s in q["steamids"].split(",")]
        self.calls.append(ids)
        if self.status != 200:
            return self.status, ""
        return 200, _answer(*(self.players[s] for s in ids if s in self.players))


def test_parses_get_player_bans():
    get = FakeSteamApi([_player(1, vac=2, days=40), _player(2, game=1, days=3, community=True, economy="probation")])
    got = fetch_bans("KEY", [1, 2, 3], http_get=get)
    assert got[1] == {"vac_bans": 2, "game_bans": 0, "days_since_last_ban": 40, "community_banned": False,
                      "economy_ban": "none"}
    assert got[2]["game_bans"] == 1 and got[2]["community_banned"] and got[2]["economy_ban"] == "probation"
    assert 3 not in got
    with pytest.raises(ValueError):
        fetch_bans("KEY", list(range(101)), http_get=get)


def test_errors_never_show_the_key():
    with pytest.raises(SteamUnavailable) as exc:
        fetch_bans("SECRETKEY", [1], http_get=FakeSteamApi(status=429))
    assert "SECRETKEY" not in str(exc.value)

    def broken(url, timeout):
        raise OSError(url)
    with pytest.raises(SteamUnavailable) as exc:
        fetch_bans("SECRETKEY", [1], http_get=broken)
    assert "SECRETKEY" not in str(exc.value)
    with pytest.raises(SteamUnavailable):
        fetch_bans("KEY", [1], http_get=lambda u, t: (200, "<html>"))


def test_only_vac_or_game_bans_make_a_badge():
    now = datetime.now(timezone.utc)
    row = {"vac_bans": 0, "game_bans": 0, "days_since_last_ban": 0, "community_banned": True,
           "economy_ban": "banned", "checked_at": now}
    assert public_bans(row, 5) is None
    assert public_bans(None, 5) is None
    b = public_bans(row | {"vac_bans": 1, "days_since_last_ban": 10, "checked_at": now - timedelta(days=2)}, 5)
    assert b["vacBans"] == 1 and b["daysSinceLastBan"] == 12
    assert b["lastBanOn"] == (now - timedelta(days=12)).date().isoformat()
    assert b["profileUrl"] == "https://steamcommunity.com/profiles/5"


def test_lookup_caches_and_backs_off(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/bans.sqlite")
    db.init_schema()
    get = FakeSteamApi([_player(1, vac=1, days=5)])
    lookup = BanLookup(db, "KEY", http_get=get)
    got = lookup.get([1, 2])
    assert got[1]["vacBans"] == 1 and got[2] is None
    assert lookup.get([1, 2])[1]["vacBans"] == 1
    assert get.calls == [[1, 2]]                  # the second time from the database

    # Stale records are asked again; a failing Steam keeps the last answer and isn't asked on every request.
    lookup.ttl = timedelta(0)
    get.status = 503
    assert lookup.get([1])[1]["vacBans"] == 1
    assert lookup.get([1])[1]["vacBans"] == 1
    assert len(get.calls) == 2

    # No API key: nothing is asked, nothing is shown.
    off = BanLookup(db, "", http_get=get)
    assert off.get([1]) == {1: None} and len(get.calls) == 2


def _signed_in(config, tmp_path, steam_api):
    url = f"sqlite:///{tmp_path}/bans-api.sqlite"
    db = Database(url)
    db.init_schema()
    _seed(db)
    cfg = config.with_overrides({"auth": {"enabled": True, "public_url": PUBLIC, "steam_api_key": "KEY"}})
    c = TestClient(create_app(cfg, db_url=url, steam_http_post=FakeSteam(), profile_fetcher=lambda sid: {},
                              steam_bans_get=steam_api))
    loc = c.get("/auth/steam/login", follow_redirects=False).headers["location"]
    return_to = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(loc).query))["openid.return_to"]
    params = _assertion(return_to=return_to, steam_id=ME) | dict(urllib.parse.parse_qsl(urllib.parse.urlparse(return_to).query))
    assert c.get("/auth/steam/callback", params=params, follow_redirects=False).status_code == 303
    return c, db


def test_api_shows_bans_without_touching_the_class(config, tmp_path):
    steam = FakeSteamApi([_player(ENEMY, vac=1, days=30), _player(STRANGER, game=2, days=1)])
    c, _ = _signed_in(config, tmp_path, steam)

    match = c.get("/matches/m1").json()
    by_id = {p["steamId"]: p for p in match["players"]}
    assert by_id[str(ENEMY)]["bans"]["vacBans"] == 1
    assert by_id[str(MATE)]["bans"] is None
    assert by_id[str(ENEMY)]["assessment"]["classification"] == "NORMAL"
    assert len(steam.calls) == 1                     # one request for the whole match

    assert c.get(f"/players/{ENEMY}").json()["bans"]["vacBans"] == 1
    risk = c.get(f"/risk/{ENEMY}").json()
    assert risk["classification"] == "NORMAL" and risk["bans"]["vacBans"] == 1

    # A stranger's class stays hidden, the public ban record doesn't.
    batch = c.post("/risk/batch", json={"steamIds": [str(STRANGER), str(STRANGER2)]}).json()["results"]
    assert batch[0]["visible"] is False and batch[0]["classification"] is None
    assert batch[0]["bans"]["gameBans"] == 2 and batch[1]["bans"] is None


def test_overlay_gets_bans_for_strangers_too(config, tmp_path):
    unseen = 76561198000000999
    steam = FakeSteamApi([_player(unseen, vac=3, days=400)])
    c, _ = _signed_in(config, tmp_path, steam)
    app = TestClient(c.app, headers={"Authorization": f"Bearer {c.post('/me/tokens', json={'name': 'app'}).json()['token']}"})
    rows = app.post("/lobby/risk", json={"players": [{"steamId": str(unseen)}, {"steamId": str(MATE)}]}).json()["players"]
    assert rows[0]["classification"] == "INSUFFICIENT_DATA" and rows[0]["bans"]["vacBans"] == 3
    assert "bans" not in rows[1]
