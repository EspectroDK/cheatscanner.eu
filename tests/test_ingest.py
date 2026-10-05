"""Automatic demo fetching: share codes, Steam match history, onboarding, fetcher hand-off."""

import bz2
import json
import urllib.parse

import pytest
from fastapi.testclient import TestClient

from cs2_analyzer.api.app import create_app
from cs2_analyzer.ingest import sharecode, steam_history
from cs2_analyzer.ingest.service import Ingest, is_replay_url, download_demo
from cs2_analyzer.storage.repository import Database
from test_auth import PUBLIC, FakeSteam, _assertion

SID = 76561198000000042
AUTH = "ABCD-EFGHJ-KLMN"
CODES = ["CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2xK"] + [
    sharecode.encode(sharecode.ShareCode(3230642215713767580 + i, 3230647599455273103 + i, 55788)) for i in range(1, 4)]
URL = "http://replay183.valve.net/730/003230642215713767581_0123456789.dem.bz2"


def test_share_code_decode_known_vector_and_roundtrip():
    d = sharecode.decode(CODES[0])
    assert (d.match_id, d.reservation_id, d.tv_port) == (3230642215713767580, 3230647599455273103, 55788)
    assert sharecode.encode(d) == CODES[0]
    for bad in ("", "CSGO-", "CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2x", "CSGO-GADqf-jjyJ8-cSP2r-smZRo-TO2xl"):
        assert not sharecode.is_share_code(bad)
        with pytest.raises(ValueError):
            sharecode.decode(bad)


class FakeHistory:
    """Steam's GetNextMatchSharingCode over a fixed history."""

    def __init__(self, history=CODES, auth=AUTH, status=None):
        self.history, self.auth, self.status, self.calls = list(history), auth, status, 0

    def __call__(self, url, timeout):
        self.calls += 1
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))
        if self.status:
            return self.status, ""
        if q["steamidkey"] != self.auth:
            return 403, ""
        if q["knowncode"] not in self.history:
            return 412, ""
        i = self.history.index(q["knowncode"])
        nxt = self.history[i + 1] if i + 1 < len(self.history) else "n/a"
        return 200, json.dumps({"result": {"nextcode": nxt}})


def test_next_share_code_answers():
    h = FakeHistory()
    assert steam_history.next_share_code("key", SID, AUTH, CODES[0], h) == CODES[1]
    assert steam_history.next_share_code("key", SID, AUTH, CODES[-1], h) is None
    with pytest.raises(steam_history.AuthCodeRejected):
        steam_history.next_share_code("key", SID, "ZZZZ-ZZZZZ-ZZZZ", CODES[0], h)
    with pytest.raises(steam_history.KnownCodeRejected):
        steam_history.next_share_code("key", SID, AUTH, "CSGO-aaaaa-aaaaa-aaaaa-aaaaa-aaaaa", h)
    with pytest.raises(steam_history.SteamUnavailable):
        steam_history.next_share_code("key", SID, AUTH, CODES[0], FakeHistory(status=429))
    with pytest.raises(steam_history.SteamUnavailable, match="API key"):
        steam_history.next_share_code("", SID, AUTH, CODES[0], h)


def _app(config, tmp_path, history, downloader=None, required=True):
    cfg = config.with_overrides({
        "auth": {"enabled": True, "public_url": PUBLIC, "steam_api_key": "k"},
        "ingest": {"require_match_access": required, "service_token": "svc"},
    })
    url = f"sqlite:///{tmp_path}/ingest.sqlite"
    app = create_app(cfg, db_url=url, steam_http_post=FakeSteam(), profile_fetcher=lambda sid: {},
                     steam_history_get=history, demo_downloader=downloader)
    c = TestClient(app)
    loc = c.get("/auth/steam/login", follow_redirects=False).headers["location"]
    return_to = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(loc).query))["openid.return_to"]
    params = _assertion(return_to=return_to, steam_id=SID) | dict(urllib.parse.parse_qsl(urllib.parse.urlparse(return_to).query))
    c.get("/auth/steam/callback", params=params, follow_redirects=False)
    return c, Database(url), cfg


def test_onboarding_is_required_and_validates_codes(config, tmp_path):
    c, db, _ = _app(config, tmp_path, FakeHistory())
    assert c.get("/me").status_code == 200                      # account itself works
    assert c.get("/me/steam-match-access").json() == {"status": "MISSING", "required": True}
    assert c.get("/me/matches").status_code == 403              # data needs onboarding first
    assert c.put("/me/steam-match-access", json={"authCode": "nope", "knownCode": CODES[0]}).status_code == 400
    r = c.put("/me/steam-match-access", json={"authCode": "ZZZZ-ZZZZZ-ZZZZ", "knownCode": CODES[0]})
    assert r.status_code == 400 and "authentication code" in r.json()["detail"]
    r = c.put("/me/steam-match-access", json={"authCode": AUTH.lower(), "knownCode": CODES[0]})
    assert r.status_code == 200 and r.json()["status"] == "ACTIVE"
    assert c.get("/me/matches").status_code == 200
    assert AUTH not in json.dumps(c.get("/me/steam-match-access").json())   # never echoed back
    stored = db.get_match_access(c.get("/me").json()["id"])
    assert stored.auth_code_enc and AUTH not in stored.auth_code_enc      # encrypted at rest
    assert [j["shareCode"] for j in c.get("/me/sharecodes").json()] == [CODES[0]]

    assert c.delete("/me/steam-match-access").status_code == 204
    assert c.get("/me/steam-match-access").json() == {"status": "MISSING", "required": True}
    assert db.get_match_access(c.get("/me").json()["id"]).auth_code_enc is None
    assert c.get("/me/matches").status_code == 403


def test_poll_queues_new_codes_and_handles_rejection(config, tmp_path):
    hist = FakeHistory(history=CODES[:2])
    c, db, cfg = _app(config, tmp_path, hist)
    c.put("/me/steam-match-access", json={"authCode": AUTH, "knownCode": CODES[0]})
    ing = Ingest(cfg, db, http_get=hist)
    assert ing.poll_once() == {"users": 1, "queued": 1, "errors": 0}
    hist.history = CODES                                    # two more matches played since
    assert ing.poll_once()["queued"] == 2
    assert ing.poll_once()["queued"] == 0
    assert {j["shareCode"] for j in c.get("/me/sharecodes").json()} == set(CODES)
    assert c.get("/me/steam-match-access").json()["lastShareCode"] == CODES[-1]

    hist.auth = "WXYZ-WXYZW-WXYZ"                           # user regenerated the code on Steam
    assert ing.poll_once()["errors"] == 1
    assert c.get("/me/steam-match-access").json()["status"] == "REJECTED"
    assert ing.poll_once()["users"] == 0                    # no longer polled until re-entered


def test_fetcher_claim_result_download_and_analysis(config, tmp_path, monkeypatch):
    downloads = []

    def fake_download(url, dest, max_c, max_d):
        downloads.append(url)
        dest.write_bytes(b"not a real demo")
        return dest

    c, db, _ = _app(config, tmp_path, FakeHistory(), downloader=fake_download)
    c.put("/me/steam-match-access", json={"authCode": AUTH, "knownCode": CODES[0]})
    svc = TestClient(c.app)
    assert svc.post("/internal/sharecodes/claim").status_code == 401
    h = {"Authorization": "Bearer svc"}
    job = svc.post("/internal/sharecodes/claim", headers=h).json()
    assert job["shareCode"] == CODES[0] and job["matchId"] == "3230642215713767580" and job["tvPort"] == 55788
    assert svc.post("/internal/sharecodes/claim", headers=h).status_code == 204   # nothing else queued

    assert svc.post(f"/internal/sharecodes/{CODES[0]}/result", headers=h,
                    json={"demoUrl": "http://evil.example/730/1_2.dem.bz2"}).status_code == 400

    # A retryable failure puts it back in the queue.
    db.update_share_code(CODES[0], status="FETCHING")
    assert svc.post(f"/internal/sharecodes/{CODES[0]}/result", headers=h, json={"error": "GC timeout"}).json()["status"] == "QUEUED"
    svc.post("/internal/sharecodes/claim", headers=h)

    r = svc.post(f"/internal/sharecodes/{CODES[0]}/result", headers=h, json={"demoUrl": URL})
    assert r.status_code == 200
    # The fake file isn't a demo: the parser panics, and the job must still end as FAILED, not hang.
    assert _wait(c) == "FAILED" and "analysis failed" in c.get("/me/sharecodes").json()[0]["error"]
    assert downloads == [URL]

    # Successful analysis: DONE with the analyzed match id, and the match is attributed to the user.
    from test_storage_api import _fake_result

    def fake_analyze(path, config, db=None, **kw):
        r = _fake_result("gc-match")
        db.begin_match(r.meta, force=False, detector_version="d", scoring_version="s")
        db.save_results(r)
        db.mark_completed("gc-match", True)
        path.unlink()
        r.demo_deleted = True
        return r

    monkeypatch.setattr("cs2_analyzer.pipeline.analyze_demo", fake_analyze)
    db.update_share_code(CODES[0], status="FETCHING")
    svc.post(f"/internal/sharecodes/{CODES[0]}/result", headers=h, json={"demoUrl": URL})
    assert _wait(c) == "DONE"
    assert c.get("/me/sharecodes").json()[0]["matchId"] == "gc-match"
    assert [m["matchId"] for m in c.get("/me/matches").json()] == ["gc-match"]
    assert not list((tmp_path).rglob("*.dem"))           # raw demo removed


def _wait(c):
    import time
    for _ in range(200):
        status = c.get("/me/sharecodes").json()[0]["status"]
        if status in ("DONE", "FAILED"):
            return status
        time.sleep(0.05)
    return status


def test_download_demo_checks_url_and_unpacks(tmp_path, monkeypatch):
    assert is_replay_url(URL)
    for ok in ("https://replay412.valve.net/730/003845372578323497175_0723140305.dem.bz2",
               "http://replay.valve.net/730/003845372578323497175_0723140305.dem.bz2",
               "http://replay183.valve.net:80/730/003845372578323497175_0723140305.dem.bz2",
               "http://REPLAY183.Valve.net/730/003845372578323497175_0723140305_273.dem.bz2",
               "http://replay129.wmsj.cn/730/003845372578323497175_0723140305.dem.bz2",
               "http://replay308.csgo.com.cn/730/003846579163485962624_0597676167.dem.bz2"):
        assert is_replay_url(ok), ok
    for bad in ("http://replay1.valve.net.evil.com/730/1_2.dem.bz2", "http://evil.com/730/1_2.dem.bz2",
                "http://user@replay1.valve.net/730/1_2.dem.bz2", "http://replay1.valve.net:8080/730/1_2.dem.bz2",
                "http://replay1.valve.net/730/../x.dem.bz2", "http://replay1.valve.net/740/1_2.dem.bz2",
                "http://replay1.valve.net/730/1_2.dem.bz2?x=1", "ftp://replay1.valve.net/730/1_2.dem.bz2",
                "http://evilvalve.net/730/1_2.dem.bz2", "http://replay1.csgo.com.cn.evil.com/730/1_2.dem.bz2", "not a url"):
        assert not is_replay_url(bad), bad
    with pytest.raises(ValueError):
        download_demo("http://example.com/x.dem.bz2", tmp_path / "x.dem", 10, 10)

    payload = bz2.compress(b"DEMO" * 1000)

    class Resp:
        def __init__(self):
            self.data = payload

        def read(self, n):
            chunk, self.data = self.data[:n], self.data[n:]
            return chunk

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda url, timeout: Resp())
    out = download_demo(URL, tmp_path / "m.dem", 1 << 20, 1 << 20)
    assert out.read_bytes() == b"DEMO" * 1000 and not (tmp_path / "m.dem.bz2").exists()
    with pytest.raises(ValueError, match="unpacked"):
        download_demo(URL, tmp_path / "big.dem", 1 << 20, 100)
    assert not (tmp_path / "big.dem").exists()


def test_dotenv_sets_missing_variables_only(tmp_path, monkeypatch):
    from cs2_analyzer.config import load_dotenv
    f = tmp_path / ".env"
    f.write_text('﻿# comment\nCS2A_T1=one\nCS2A_T2 = "two"\nCS2A_T3=\nCS2A_T4=file\n', "utf-8")
    monkeypatch.setenv("CS2A_T4", "shell")
    for k in ("CS2A_T1", "CS2A_T2", "CS2A_T3"):
        monkeypatch.delenv(k, raising=False)
    assert load_dotenv(f) == ["CS2A_T1", "CS2A_T2"]
    import os
    assert (os.environ["CS2A_T1"], os.environ["CS2A_T2"], os.environ["CS2A_T4"]) == ("one", "two", "shell")
    assert "CS2A_T3" not in os.environ
    for k in ("CS2A_T1", "CS2A_T2"):
        monkeypatch.delenv(k)


def test_restart_requeues_interrupted_matches(config, tmp_path):
    c, db, cfg = _app(config, tmp_path, FakeHistory())
    c.put("/me/steam-match-access", json={"authCode": AUTH, "knownCode": CODES[0]})
    db.update_share_code(CODES[0], status="ANALYZING")
    _app(config, tmp_path, FakeHistory())                  # API starts again on the same database
    job = db.share_code_jobs(c.get("/me").json()["id"])[0]
    assert job["status"] == "QUEUED" and "restart" in job["error"]


def test_transient_download_errors_are_retried(tmp_path):
    import urllib.error
    from cs2_analyzer.ingest.service import download_with_retries, is_transient

    assert is_transient(ConnectionResetError(10054, "forcibly closed"))
    assert is_transient(urllib.error.HTTPError(URL, 503, "busy", {}, None))
    assert not is_transient(urllib.error.HTTPError(URL, 404, "gone", {}, None))
    assert not is_transient(ValueError("demo download is larger than allowed"))

    calls, waits, notes = [], [], []

    def flaky(url, dest, max_c, max_d):
        calls.append(url)
        if len(calls) < 3:
            raise ConnectionResetError(10054, "forcibly closed")
        dest.write_bytes(b"demo")
        return dest

    dest = tmp_path / "x.dem"
    assert download_with_retries(flaky, URL, dest, 1, 1, [1, 2, 3], sleep=waits.append, on_retry=notes.append) == dest
    assert len(calls) == 3 and waits == [1, 2] and "retry 2 of 3" in notes[-1]

    def failing(exc):
        def download(url, dest, max_c, max_d):
            calls.append(url)
            raise exc
        return download

    calls.clear()
    with pytest.raises(ConnectionResetError):          # gives up after the last retry
        download_with_retries(failing(ConnectionResetError(10054, "reset")), URL, dest, 1, 1, [0, 0], sleep=waits.append)
    assert len(calls) == 3
    calls.clear()
    with pytest.raises(ValueError):                    # size limits and bad URLs are not retried
        download_with_retries(failing(ValueError("too big")), URL, dest, 1, 1, [0, 0], sleep=waits.append)
    assert len(calls) == 1


def test_failed_match_can_be_retried_from_settings(config, tmp_path, monkeypatch):
    fail = [True]

    def download(url, dest, max_c, max_d):
        if fail[0]:
            raise ValueError("simulated permanent failure")
        dest.write_bytes(b"demo")
        return dest

    c, db, _ = _app(config, tmp_path, FakeHistory(), downloader=download)
    c.put("/me/steam-match-access", json={"authCode": AUTH, "knownCode": CODES[0]})
    h = {"Authorization": "Bearer svc"}
    assert c.post(f"/me/sharecodes/{CODES[0]}/retry").status_code == 409     # still queued: nothing to retry
    c.post("/internal/sharecodes/claim", headers=h)
    c.post(f"/internal/sharecodes/{CODES[0]}/result", headers=h, json={"demoUrl": URL})
    assert _wait(c) == "FAILED" and "download failed" in c.get("/me/sharecodes").json()[0]["error"]

    from test_storage_api import _fake_result

    def fake_analyze(path, config, db=None, **kw):
        r = _fake_result("gc-match")
        db.begin_match(r.meta, force=False, detector_version="d", scoring_version="s")
        db.save_results(r)
        db.mark_completed("gc-match", True)
        path.unlink()
        r.demo_deleted = True
        return r

    monkeypatch.setattr("cs2_analyzer.pipeline.analyze_demo", fake_analyze)
    fail[0] = False
    r = c.post(f"/me/sharecodes/{CODES[0]}/retry")
    assert r.status_code == 200 and r.json()["attempts"] == 0
    assert _wait(c) == "DONE" and c.get("/me/sharecodes").json()[0]["matchId"] == "gc-match"
    assert c.post(f"/me/sharecodes/{CODES[0]}/retry").status_code == 409     # done: nothing to retry

    # Failed before Valve's URL was known: back to the fetcher's queue.
    db.update_share_code(CODES[0], status="FAILED", demo_url=None)
    assert c.post(f"/me/sharecodes/{CODES[0]}/retry").json()["status"] == "QUEUED"
    assert c.post("/internal/sharecodes/claim", headers=h).json()["shareCode"] == CODES[0]


def test_other_users_cannot_retry_a_match(config, tmp_path):
    c, db, _ = _app(config, tmp_path, FakeHistory())
    c.put("/me/steam-match-access", json={"authCode": AUTH, "knownCode": CODES[0]})
    db.update_share_code(CODES[0], status="FAILED")
    assert db.retry_share_code(CODES[0], user_id=999) is None
    assert db.retry_share_code(CODES[0], user_id=c.get("/me").json()["id"])["status"] == "QUEUED"


def test_match_page_links_valve_demo_for_a_month(config, tmp_path, monkeypatch):
    import time
    from datetime import datetime, timedelta, timezone

    from test_storage_api import _fake_result

    def fake_analyze(path, config, db=None, played_at=None, **kw):
        r = _fake_result("gc-match")
        db.begin_match(r.meta, force=False, detector_version="d", scoring_version="s", played_at=played_at)
        db.save_results(r)
        db.mark_completed("gc-match", True)
        return r

    monkeypatch.setattr("cs2_analyzer.pipeline.analyze_demo", fake_analyze)
    c, db, _ = _app(config, tmp_path, FakeHistory(), downloader=lambda url, dest, *a: dest.write_bytes(b"x") and dest)
    c.put("/me/steam-match-access", json={"authCode": AUTH, "knownCode": CODES[0]})
    h = {"Authorization": "Bearer svc"}
    c.post("/internal/sharecodes/claim", headers=h)
    played = int(time.time()) - 3 * 86400
    c.post(f"/internal/sharecodes/{CODES[0]}/result", headers=h, json={"demoUrl": URL, "matchTime": played})
    assert _wait(c) == "DONE"

    m = c.get("/matches/gc-match").json()
    assert m["playedAt"].startswith(datetime.fromtimestamp(played, timezone.utc).isoformat()[:19])
    assert m["valveDemo"]["url"] == URL and m["valveDemo"]["shareCode"] == CODES[0]
    until = datetime.fromisoformat(m["valveDemo"]["availableUntil"])
    assert abs(until - datetime.fromtimestamp(played, timezone.utc) - timedelta(days=30)) < timedelta(seconds=1)

    # Older than a month: Valve no longer has it, so no link.
    with db.session() as s:
        from cs2_analyzer.storage import models as M
        s.get(M.Match, "gc-match").played_at = datetime.now(timezone.utc) - timedelta(days=31)
    assert c.get("/matches/gc-match").json()["valveDemo"] is None
    # Matches fetched before we knew the play time count from when the share code was found.
    with db.session() as s:
        s.get(M.Match, "gc-match").played_at = None
    assert c.get("/matches/gc-match").json()["valveDemo"]["url"] == URL
