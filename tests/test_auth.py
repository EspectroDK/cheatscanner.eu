"""Steam sign-in, sessions, API tokens and the auth guard on data endpoints."""

import urllib.parse

import pytest
from fastapi.testclient import TestClient

from cs2_analyzer.api import steam_openid
from cs2_analyzer.api.app import create_app
from cs2_analyzer.api.auth import SESSION_COOKIE

SID = 76561198000000042
PUBLIC = "http://testserver"
CALLBACK = f"{PUBLIC}/auth/steam/callback"


def _assertion(return_to=CALLBACK, steam_id=SID, **over):
    claimed = f"https://steamcommunity.com/openid/id/{steam_id}"
    p = {
        "openid.ns": steam_openid.OPENID_NS, "openid.mode": "id_res", "openid.op_endpoint": steam_openid.STEAM_OPENID,
        "openid.claimed_id": claimed, "openid.identity": claimed, "openid.return_to": return_to,
        "openid.response_nonce": "2026-09-27T12:00:00Zabc", "openid.assoc_handle": "1234567890",
        "openid.signed": "signed,op_endpoint,claimed_id,identity,return_to,response_nonce,assoc_handle",
        "openid.sig": "c2ln",
    }
    p.update(over)
    return p


class FakeSteam:
    def __init__(self, valid=True):
        self.valid, self.calls = valid, []

    def __call__(self, url, fields, timeout):
        self.calls.append((url, fields))
        return f"ns:{steam_openid.OPENID_NS}\nis_valid:{'true' if self.valid else 'false'}\n"


def test_login_url_points_to_steam():
    u = urllib.parse.urlparse(steam_openid.login_url(CALLBACK, PUBLIC))
    q = dict(urllib.parse.parse_qsl(u.query))
    assert f"{u.scheme}://{u.netloc}{u.path}" == steam_openid.STEAM_OPENID
    assert q["openid.return_to"] == CALLBACK and q["openid.realm"] == PUBLIC and q["openid.mode"] == "checkid_setup"


def test_verify_accepts_confirmed_assertion_and_asks_steam():
    fake = FakeSteam()
    assert steam_openid.verify(_assertion(), CALLBACK, fake) == SID
    url, fields = fake.calls[0]
    assert url == steam_openid.STEAM_OPENID and fields["openid.mode"] == "check_authentication"
    assert fields["openid.sig"] == "c2ln"


@pytest.mark.parametrize("params, why", [
    (_assertion(**{"openid.mode": "cancel"}), "cancelled"),
    (_assertion(**{"openid.op_endpoint": "https://evil.example/openid/login"}), "not from Steam"),
    (_assertion(return_to="https://other.example/auth/steam/callback"), "different return"),
    (_assertion(**{"openid.claimed_id": "https://evil.example/openid/id/76561198000000042"}), "claimed id"),
    (_assertion(**{"openid.identity": "https://steamcommunity.com/openid/id/76561198000000099"}), "claimed id"),
    (_assertion(**{"openid.signed": "op_endpoint,claimed_id"}), "not signed"),
])
def test_verify_rejects_forged_assertions_without_calling_steam(params, why):
    fake = FakeSteam()
    with pytest.raises(steam_openid.OpenIDError, match=why):
        steam_openid.verify(params, CALLBACK, fake)
    assert fake.calls == []


def test_verify_rejects_when_steam_says_invalid_or_is_unreachable():
    with pytest.raises(steam_openid.OpenIDError, match="did not confirm"):
        steam_openid.verify(_assertion(), CALLBACK, FakeSteam(valid=False))

    def down(*_):
        raise OSError("network down")

    with pytest.raises(steam_openid.OpenIDError, match="could not reach"):
        steam_openid.verify(_assertion(), CALLBACK, down)


def _client(config, tmp_path, enabled=True, valid=True):
    cfg = config.with_overrides({"auth": {"enabled": enabled, "public_url": PUBLIC}})
    app = create_app(cfg, db_url=f"sqlite:///{tmp_path}/auth.sqlite", steam_http_post=FakeSteam(valid),
                     profile_fetcher=lambda sid: {"personaname": "Tester", "avatarfull": "https://avatars.example/a.jpg"})
    return TestClient(app)


def _sign_in(client):
    r = client.get("/auth/steam/login", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith(steam_openid.STEAM_OPENID)
    return_to = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(r.headers["location"]).query))["openid.return_to"]
    query = urllib.parse.urlparse(return_to).query
    params = _assertion(return_to=return_to) | dict(urllib.parse.parse_qsl(query))
    return client.get("/auth/steam/callback", params=params, follow_redirects=False)


def test_sign_in_flow_session_and_logout(config, tmp_path):
    c = _client(config, tmp_path)
    assert c.get("/me").status_code == 401
    r = _sign_in(c)
    assert r.status_code == 303 and SESSION_COOKIE in r.cookies
    me = c.get("/me").json()
    assert me["steamId"] == str(SID) and me["personaName"] == "Tester"
    assert c.post("/auth/logout", follow_redirects=False).status_code == 303
    c.cookies.clear()
    assert c.get("/me").status_code == 401


def test_callback_requires_matching_state(config, tmp_path):
    c = _client(config, tmp_path)
    params = _assertion(return_to=f"{CALLBACK}?state=attacker") | {"state": "attacker"}
    assert c.get("/auth/steam/callback", params=params, follow_redirects=False).status_code == 400


def test_callback_rejected_when_steam_does_not_confirm(config, tmp_path):
    c = _client(config, tmp_path, valid=False)
    assert _sign_in(c).status_code == 401
    assert c.get("/me").status_code == 401


def test_guard_and_api_tokens(config, tmp_path):
    c = _client(config, tmp_path)
    assert c.get("/health").json() == {"status": "ok"}
    assert c.get(f"/risk/{SID}").status_code == 401
    assert c.post("/risk/batch", json={"steamIds": [str(SID)]}).status_code == 401
    _sign_in(c)
    assert c.get(f"/risk/{SID}").json()["classification"] == "INSUFFICIENT_DATA"

    created = c.post("/me/tokens", json={"name": "companion"})
    assert created.status_code == 201
    token = created.json()["token"]
    assert [t["name"] for t in c.get("/me/tokens").json()] == ["companion"]

    bot = TestClient(c.app)
    headers = {"Authorization": f"Bearer {token}"}
    assert bot.get(f"/risk/{SID}", headers=headers).status_code == 200
    assert bot.get("/me", headers=headers).json()["steamId"] == str(SID)
    assert bot.post("/me/tokens", json={"name": "x"}, headers=headers).status_code == 403  # tokens can't mint tokens
    assert bot.get(f"/risk/{SID}", headers={"Authorization": "Bearer nope"}).status_code == 401

    assert c.delete(f"/me/tokens/{created.json()['id']}").status_code == 204
    assert bot.get(f"/risk/{SID}", headers=headers).status_code == 401
    assert c.delete(f"/me/tokens/{created.json()['id']}").status_code == 404


def test_session_cookie_is_not_an_api_token(config, tmp_path):
    c = _client(config, tmp_path)
    r = _sign_in(c)
    session = r.cookies[SESSION_COOKIE]
    other = TestClient(c.app)
    assert other.get("/me", headers={"Authorization": f"Bearer {session}"}).status_code == 401


def test_auth_disabled_keeps_local_api_open(config, tmp_path):
    c = _client(config, tmp_path, enabled=False)
    assert c.get(f"/risk/{SID}").status_code == 200
    assert "database" in c.get("/health").json()
    assert c.get("/me").status_code == 401
