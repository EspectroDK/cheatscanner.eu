"""Companion app: linking by code, and the overlay's lobby lookup (api/companion.py)."""

import urllib.parse

from fastapi.testclient import TestClient

from cs2_analyzer.api.app import create_app
from cs2_analyzer.api.companion import RateLimiter
from cs2_analyzer.storage import models as M
from cs2_analyzer.storage.repository import Database, normalize_user_code
from test_access import ENEMY, ME, STRANGER, _seed
from test_auth import PUBLIC, FakeSteam, _assertion

UNSEEN = 76561198000000999


def _client(config, tmp_path, **companion):
    url = f"sqlite:///{tmp_path}/companion.sqlite"
    db = Database(url)
    db.init_schema()
    _seed(db)
    with db.session() as s:
        s.get(M.PlayerAssessment, ENEMY).classification = "VERY_HIGH"
        s.get(M.PlayerAssessment, ENEMY).matches_analyzed = 7
    cfg = config.with_overrides({"auth": {"enabled": True, "public_url": PUBLIC}, "companion": companion})
    return TestClient(create_app(cfg, db_url=url, steam_http_post=FakeSteam(), profile_fetcher=lambda sid: {})), db


def _sign_in(c: TestClient, steam_id=ME):
    loc = c.get("/auth/steam/login", follow_redirects=False).headers["location"]
    return_to = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(loc).query))["openid.return_to"]
    params = _assertion(return_to=return_to, steam_id=steam_id) | dict(urllib.parse.parse_qsl(urllib.parse.urlparse(return_to).query))
    assert c.get("/auth/steam/callback", params=params, follow_redirects=False).status_code == 303


def _link(c: TestClient) -> str:
    app = TestClient(c.app)  # the companion app: no browser cookies
    start = app.post("/companion/pair", json={"deviceName": "GAMING-PC"}).json()
    _sign_in(c)
    assert c.post("/companion/pair/confirm", json={"userCode": start["userCode"]}).status_code == 200
    return app.post("/companion/pair/token", json={"deviceCode": start["deviceCode"]}).json()["token"]


def test_link_app_by_code(config, tmp_path):
    c, _ = _client(config, tmp_path)
    app = TestClient(c.app)
    start = app.post("/companion/pair", json={"deviceName": "GAMING-PC"})
    assert start.status_code == 201
    body = start.json()
    assert body["verifyUrl"] == f"{PUBLIC}/#/link?code={body['userCode']}"
    assert len(body["userCode"]) == 9 and body["userCode"][4] == "-"

    # Not confirmed yet: the app keeps waiting.
    assert app.post("/companion/pair/token", json={"deviceCode": body["deviceCode"]}).status_code == 202

    # Confirming needs a signed-in browser; the code can be typed loosely.
    assert c.post("/companion/pair/confirm", json={"userCode": body["userCode"]}).status_code == 401
    _sign_in(c)
    typed = body["userCode"].replace("-", " ").lower()
    confirm = c.post("/companion/pair/confirm", json={"userCode": typed})
    assert confirm.status_code == 200 and confirm.json() == {"deviceName": "GAMING-PC"}

    got = app.post("/companion/pair/token", json={"deviceCode": body["deviceCode"]})
    assert got.status_code == 200 and got.json()["status"] == "LINKED"
    assert got.json()["user"]["steamId"] == str(ME)
    token = got.json()["token"]
    # The token is handed out once, works as an API token and is listed (revocable) on the website.
    assert app.post("/companion/pair/token", json={"deviceCode": body["deviceCode"]}).status_code == 410
    assert app.get("/me", headers={"Authorization": f"Bearer {token}"}).json()["steamId"] == str(ME)
    assert [t["name"] for t in c.get("/me/tokens").json()] == ["Companion app: GAMING-PC"]


def test_app_token_cannot_confirm_other_links(config, tmp_path):
    c, _ = _client(config, tmp_path)
    token = _link(c)
    other = TestClient(c.app).post("/companion/pair", json={}).json()
    r = TestClient(c.app).post("/companion/pair/confirm", json={"userCode": other["userCode"]},
                               headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_expired_code(config, tmp_path):
    c, _ = _client(config, tmp_path, pairing_ttl_s=0)
    app = TestClient(c.app)
    body = app.post("/companion/pair", json={}).json()
    _sign_in(c)
    assert c.post("/companion/pair/confirm", json={"userCode": body["userCode"]}).status_code == 404
    assert app.post("/companion/pair/token", json={"deviceCode": body["deviceCode"]}).status_code == 410


def test_lobby_shows_global_class_for_everyone(config, tmp_path):
    c, _ = _client(config, tmp_path)
    token = _link(c)
    app = TestClient(c.app, headers={"Authorization": f"Bearer {token}"})
    assert TestClient(c.app).post("/lobby/risk", json={"players": []}).status_code == 401

    # A stranger's history is hidden on the website, but the overlay shows their class (plan 4.5).
    assert app.get(f"/risk/{STRANGER}").status_code == 404
    res = app.post("/lobby/risk", json={"players": [
        {"steamId": str(STRANGER)}, {"steamId": str(ENEMY)}, {"steamId": str(UNSEEN)}, {"name": "BOT Kask"},
    ]})
    assert res.status_code == 200
    players = res.json()["players"]
    assert players[0] == {"steamId": str(STRANGER), "classification": "NORMAL", "matchesAnalyzed": 1,
                          "name": f"p{STRANGER % 1000}"}
    assert players[1]["classification"] == "HIGH" and players[1]["matchesAnalyzed"] == 7  # VERY_HIGH folded
    assert "detail" in players[1] and "detail" not in players[0]  # extended card only for flagged players
    assert players[2] == {"steamId": str(UNSEEN), "classification": "INSUFFICIENT_DATA", "matchesAnalyzed": 0}
    assert players[3]["classification"] is None and players[3]["note"]
    assert "not a verdict" in res.json()["disclaimer"].lower()


def test_lobby_detail_card_for_flagged_players(config, tmp_path):
    c, db = _client(config, tmp_path)
    with db.session() as s:
        pa = s.get(M.PlayerAssessment, ENEMY)
        pa.historical_evidence_score, pa.high_severity_matches = 0.913, 8
        pa.information_score, pa.aim_score, pa.trigger_score = 0.8, 0.3, 0.1
        pma = s.get(M.PlayerMatchAssessment, (ENEMY, "m1"))
        pma.classification, pma.overall_evidence_score = "HIGH", 0.94
    _sign_in(c)
    enemy = c.post("/lobby/risk", json={"players": [{"steamId": str(ENEMY)}]}).json()["players"][0]
    assert enemy["detail"] == {
        "evidenceScore": 91, "highEvidenceMatches": 8,
        "axes": {"wallTracking": "HIGH", "aim": "MEDIUM", "reaction": "LOW"},
        "recent": [{"map": "de_mirage", "evidenceScore": 94, "playedAt": None}],
    }
    assert "matchId" not in str(enemy)


def test_lobby_rate_limit(config, tmp_path):
    c, _ = _client(config, tmp_path, lobby_lookups_per_minute=2)
    _sign_in(c)
    body = {"players": [{"steamId": str(STRANGER)}]}
    assert [c.post("/lobby/risk", json=body).status_code for _ in range(3)] == [200, 200, 429]


def test_rate_limiter_window():
    now = [0.0]
    rl = RateLimiter(2, 60, clock=lambda: now[0])
    assert rl.allow("a") and rl.allow("a") and not rl.allow("a") and rl.allow("b")
    now[0] = 61
    assert rl.allow("a")


def test_user_code_normalization():
    assert normalize_user_code("abcd 2345") == normalize_user_code("ABCD-2345") == "ABCD-2345"


def test_site_info_has_branding(config, tmp_path):
    c, _ = _client(config, tmp_path)
    info = c.get("/site-info").json()
    assert info["name"] == "Cheatscanner" and info["domain"] == "cheatscanner.eu"
    assert info["publicUrl"] == PUBLIC and info["authEnabled"] is True


def test_companion_download_serves_newest_installer(config, tmp_path):
    downloads = tmp_path / "downloads"
    cfg = config.with_overrides({"site": {"downloads_dir": str(downloads)}})
    c, _ = _client(cfg, tmp_path)
    assert c.get("/site-info").json()["companionDownload"] is None
    assert c.get("/download/companion").status_code == 404

    downloads.mkdir()
    (downloads / "Cheatscanner-Setup-0.1.0.exe").write_bytes(b"old")
    (downloads / "Cheatscanner-Setup-0.10.0.exe").write_bytes(b"new")
    (downloads / "Cheatscanner-Setup-0.9.0.exe.part").write_bytes(b"upload in progress")
    info = c.get("/site-info").json()["companionDownload"]
    assert info["version"] == "0.10.0" and info["url"] == "/download/companion"
    r = c.get("/download/companion")
    assert r.status_code == 200 and r.content == b"new"
    assert "Cheatscanner-Setup-0.10.0.exe" in r.headers["content-disposition"]
