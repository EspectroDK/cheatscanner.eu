"""Website visibility rules (api/app.py module docstring) with auth enabled."""

import urllib.parse
from pathlib import Path

from fastapi.testclient import TestClient

from cs2_analyzer.api.app import create_app
from cs2_analyzer.storage import models as M
from cs2_analyzer.storage.repository import Database
from test_auth import PUBLIC, FakeSteam, _assertion

ME, MATE, ENEMY, STRANGER, STRANGER2 = (76561198000000100 + i for i in range(5))


def _seed(db: Database):
    """m1: me + mate + enemy. m2: two strangers. m3: strangers only, uploaded by me later."""
    with db.session() as s:
        for sid in (ME, MATE, ENEMY, STRANGER, STRANGER2):
            s.add(M.Player(steam_id=sid, last_known_name=f"p{sid % 1000}", matches_analyzed=1))
            s.add(M.PlayerAssessment(steam_id=sid, matches_analyzed=1, confidence_level="LOW", classification="NORMAL"))
        for mid, players in (("m1", (ME, MATE, ENEMY)), ("m2", (STRANGER, STRANGER2)), ("m3", (STRANGER, STRANGER2))):
            s.add(M.Match(match_id=mid, map="de_mirage", processing_status="COMPLETED"))
            s.flush()
            for sid in players:
                s.add(M.MatchPlayer(match_id=mid, steam_id=sid, name=f"p{sid % 1000}", team=2))
                s.add(M.PlayerMatchAssessment(steam_id=sid, match_id=mid, classification="NORMAL"))
                s.add(M.EvidenceEvent(id=f"{mid}-{sid}", match_id=mid, steam_id=sid, tick_start=1, tick_peak=2, tick_end=3,
                                      detector_type="snap", severity=0.5, reliability=0.5, information_confidence=0.5,
                                      confidence=0.5, evidence_axis="AIM_MECHANICS", evidence_group="aim"))


def _signed_in(config, tmp_path, steam_id=ME):
    url = f"sqlite:///{tmp_path}/access.sqlite"
    db = Database(url)
    db.init_schema()
    _seed(db)
    cfg = config.with_overrides({"auth": {"enabled": True, "public_url": PUBLIC}})
    c = TestClient(create_app(cfg, db_url=url, steam_http_post=FakeSteam(), profile_fetcher=lambda sid: {}))
    loc = c.get("/auth/steam/login", follow_redirects=False).headers["location"]
    return_to = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(loc).query))["openid.return_to"]
    params = _assertion(return_to=return_to, steam_id=steam_id) | dict(urllib.parse.parse_qsl(urllib.parse.urlparse(return_to).query))
    assert c.get("/auth/steam/callback", params=params, follow_redirects=False).status_code == 303
    return c, db


def test_players_visible_only_if_played_with(config, tmp_path):
    c, _ = _signed_in(config, tmp_path)
    for sid in (ME, MATE, ENEMY):
        assert c.get(f"/players/{sid}").status_code == 200
        assert c.get(f"/risk/{sid}").json()["classification"] == "NORMAL"
    for sid in (STRANGER, STRANGER2):
        assert c.get(f"/players/{sid}").status_code == 404
        assert c.get(f"/players/{sid}/evidence").status_code == 404
        assert c.get(f"/risk/{sid}").status_code == 404
    batch = c.post("/risk/batch", json={"steamIds": [str(MATE), str(STRANGER)]}).json()["results"]
    assert batch[0]["classification"] == "NORMAL" and batch[1]["visible"] is False


def test_matches_visible_if_played_or_uploaded(config, tmp_path):
    c, db = _signed_in(config, tmp_path)
    assert [m["matchId"] for m in c.get("/me/matches").json()] == ["m1"]
    m1 = c.get("/matches/m1").json()
    assert all(p["visible"] for p in m1["players"])
    assert c.get("/matches/m2").status_code == 404

    # Supplying a demo shows that match, but not the history of strangers in it.
    user_id = c.get("/me").json()["id"]
    db.record_upload("m3", user_id)
    assert {m["matchId"] for m in c.get("/me/matches").json()} == {"m1", "m3"}
    m3 = c.get("/matches/m3").json()
    assert m3["players"] and not any(p["visible"] for p in m3["players"])
    assert all(p["assessment"] is None for p in m3["players"])          # strangers' evidence stays hidden
    card = next(m for m in c.get("/me/matches").json() if m["matchId"] == "m3")
    assert all(p["classification"] is None for p in card["players"])
    assert c.get(f"/players/{STRANGER}").status_code == 404
    assert c.get("/matches/m2").status_code == 404


def _file(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\x89PNG")
    return path


def test_evidence_from_all_matches_of_a_known_player(config, tmp_path):
    c, db = _signed_in(config, tmp_path, steam_id=MATE)
    # MATE later plays m2 without ME: ME sees MATE's evidence from m2 too, but not the m2 match itself.
    with db.session() as s:
        s.add(M.MatchPlayer(match_id="m2", steam_id=MATE, name="mate", team=3))
        s.add(M.EvidenceEvent(id="m2-mate", match_id="m2", steam_id=MATE, tick_start=1, tick_peak=2, tick_end=3,
                              detector_type="snap", severity=0.5, reliability=0.5, information_confidence=0.5,
                              confidence=0.9, evidence_axis="AIM_MECHANICS", evidence_group="aim",
                              debug_plot_path=str(_file(tmp_path / "out" / "m2" / "plot.png"))))
    assert {e["matchId"] for e in c.get(f"/players/{MATE}/evidence").json()} == {"m1", "m2"}  # own evidence: all

    me = TestClient(c.app)
    loc = me.get("/auth/steam/login", follow_redirects=False).headers["location"]
    return_to = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(loc).query))["openid.return_to"]
    params = _assertion(return_to=return_to, steam_id=ME) | dict(urllib.parse.parse_qsl(urllib.parse.urlparse(return_to).query))
    me.get("/auth/steam/callback", params=params, follow_redirects=False)
    events = {e["matchId"]: e for e in me.get(f"/players/{MATE}/evidence").json()}
    assert set(events) == {"m1", "m2"}
    assert events["m1"]["matchVisible"] and not events["m2"]["matchVisible"]
    assert me.get("/matches/m2").status_code == 404
    assert me.get("/evidence/m2-mate/plot").status_code == 200
    assert {m["matchId"] for m in me.get(f"/players/{MATE}/matches").json()} == {"m1"}


def test_upload_options_restricted_with_auth(config, tmp_path):
    c, _ = _signed_in(config, tmp_path)
    r = c.post("/matches?force=true", files={"file": ("a.dem", b"x")})
    assert r.status_code == 403
    assert c.post("/matches/import", json={"path": "/tmp/a.dem"}).status_code == 403
    assert c.get("/jobs/nope").status_code == 404


def test_built_website_is_served_at_root(config, tmp_path):
    web = tmp_path / "dist"
    web.mkdir()
    (web / "index.html").write_text("<html>cheatscanner</html>")
    c = TestClient(create_app(config, db_url=f"sqlite:///{tmp_path}/w.sqlite", web_dir=web))
    assert "cheatscanner" in c.get("/").text
    assert c.get("/health").json()["status"] == "ok"  # API routes still win over the static mount


def test_missing_website_explains_how_to_build(config, tmp_path):
    c = TestClient(create_app(config, db_url=f"sqlite:///{tmp_path}/n.sqlite", web_dir=tmp_path / "nothing"))
    assert "npm run build" in c.get("/").json()["detail"]


def test_evidence_clips_follow_the_same_rules(config, tmp_path):
    c, db = _signed_in(config, tmp_path)
    out = tmp_path / "out"                          # conftest points output.dir here
    clip = out / "m1" / "clip.mp4"
    clip.parent.mkdir(parents=True, exist_ok=True)
    clip.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    outside = tmp_path / "secret.mp4"
    outside.write_bytes(b"x")
    with db.session() as s:
        s.get(M.EvidenceEvent, f"m1-{MATE}").video_path = str(clip)
        s.get(M.EvidenceEvent, f"m1-{ENEMY}").video_path = str(outside)   # not under output: never served
        s.get(M.EvidenceEvent, f"m2-{STRANGER}").video_path = str(clip)

    events = {e["id"]: e for e in c.get("/matches/m1/evidence").json()}
    assert set(events) == {f"m1-{ME}", f"m1-{MATE}", f"m1-{ENEMY}"}
    assert events[f"m1-{MATE}"]["clipUrl"] == f"/evidence/m1-{MATE}/clip" and events[f"m1-{ME}"]["clipUrl"] is None
    assert "videoPath" not in events[f"m1-{MATE}"]                         # server paths stay private
    r = c.get(f"/evidence/m1-{MATE}/clip")
    assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
    assert c.get(f"/evidence/m1-{ENEMY}/clip").status_code == 404
    assert c.get(f"/evidence/m2-{STRANGER}/clip").status_code == 404        # never played with or against
    assert c.get("/matches/m2/evidence").status_code == 404
    assert c.get("/evidence/nope/plot").status_code == 404


def test_other_players_in_unseen_matches_keep_names_but_link_only_if_known(config, tmp_path):
    c, db = _signed_in(config, tmp_path)
    # MATE plays m2 without ME, against STRANGER (unknown to ME) and ENEMY (known to ME from m1).
    with db.session() as s:
        s.add(M.MatchPlayer(match_id="m2", steam_id=MATE, name="mate", team=3))
        s.add(M.MatchPlayer(match_id="m2", steam_id=ENEMY, name="enemy", team=2))
        for eid, target in (("m2-mate-a", STRANGER), ("m2-mate-b", ENEMY)):
            s.add(M.EvidenceEvent(id=eid, match_id="m2", steam_id=MATE, tick_start=1, tick_peak=2, tick_end=3,
                                  detector_type="snap", severity=0.5, reliability=0.5, information_confidence=0.5,
                                  confidence=0.9, evidence_axis="AIM_MECHANICS", evidence_group="aim",
                                  target_steam_id=target, context={"x": 1}))
    events = {e["id"]: e for e in c.get(f"/players/{MATE}/evidence").json()}
    a, b = events["m2-mate-a"], events["m2-mate-b"]
    assert a["targetName"] == f"p{STRANGER % 1000}" and a["targetSteamId"] is None
    assert b["targetName"] == "enemy" and b["targetSteamId"] == str(ENEMY)
    assert not a["matchVisible"] and a["context"] is None


def test_player_timeline_covers_all_matches(config, tmp_path):
    c, db = _signed_in(config, tmp_path)
    with db.session() as s:
        s.add(M.MatchPlayer(match_id="m2", steam_id=MATE, name="mate", team=3))
        s.add(M.PlayerMatchAssessment(steam_id=MATE, match_id="m2", classification="ELEVATED", overall_evidence_score=0.4))
    rows = c.get(f"/players/{MATE}/timeline").json()
    assert {(r["matchId"], r["matchVisible"]) for r in rows} == {("m1", True), (None, False)}
    assert {r["classification"] for r in rows} == {"NORMAL", "ELEVATED"}
    assert c.get(f"/players/{STRANGER}/timeline").status_code == 404


def test_upload_size_limit(config, tmp_path):
    c, _ = _signed_in(config, tmp_path)
    small = TestClient(create_app(config.with_overrides({"auth": {"enabled": True, "public_url": PUBLIC},
                                                         "api": {"max_upload_mb": 1}}),
                                  db_url=f"sqlite:///{tmp_path}/access.sqlite", steam_http_post=FakeSteam(),
                                  profile_fetcher=lambda sid: {}))
    small.cookies = c.cookies
    info = small.get("/site-info").json()
    assert (info["maxUploadMb"], info["maxUploadsPerDay"], info["contactEmail"]) == (1, 10, None)
    r = small.post("/matches", files={"file": ("big.dem", b"x" * (3 << 20))})
    assert r.status_code == 413 and "1 MB" in r.json()["detail"]
    r = small.post("/matches", files={"file": ("big.dem", b"x" * (3 << 19))})   # 1.5 MB: past the framing allowance
    assert r.status_code == 413
    uploads = Path(config.get("output.parquet_dir")) / "uploads"
    assert not list(uploads.glob("*big.dem"))                            # the partial file is removed


def _uploader(config, tmp_path, **api):
    c, db = _signed_in(config, tmp_path)
    app = create_app(config.with_overrides({"auth": {"enabled": True, "public_url": PUBLIC}, "api": api}),
                     db_url=f"sqlite:///{tmp_path}/access.sqlite", steam_http_post=FakeSteam(),
                     profile_fetcher=lambda sid: {})
    u = TestClient(app)
    u.cookies = c.cookies
    return u, db


def test_uploads_per_day_limit(config, tmp_path):
    u, _ = _uploader(config, tmp_path, max_uploads_per_day=2, max_queued_uploads=99)
    for _ in range(2):
        assert u.post("/matches", files={"file": ("a.dem", b"not a demo")}).status_code == 202
    r = u.post("/matches", files={"file": ("a.dem", b"not a demo")})
    assert r.status_code == 429 and "2 uploads per 24 hours" in r.json()["detail"]


def test_queued_uploads_limit(config, tmp_path, monkeypatch):
    import threading
    import cs2_analyzer.pipeline as pipeline

    gate = threading.Event()
    monkeypatch.setattr(pipeline, "analyze_demo", lambda *a, **k: gate.wait(5) and (_ for _ in ()).throw(RuntimeError("x")))
    u, _ = _uploader(config, tmp_path, max_queued_uploads=2, max_uploads_per_day=99)
    try:
        for _ in range(2):
            assert u.post("/matches", files={"file": ("a.dem", b"x")}).status_code == 202
        r = u.post("/matches", files={"file": ("a.dem", b"x")})
        assert r.status_code == 429 and "waiting for analysis" in r.json()["detail"]
    finally:
        gate.set()


def test_low_disk_refuses_uploads(config, tmp_path):
    u, _ = _uploader(config, tmp_path, min_free_disk_gb=10 ** 9)
    r = u.post("/matches", files={"file": ("a.dem", b"x")})
    assert r.status_code == 507 and "disk space" in r.json()["detail"]


def test_one_upload_at_a_time(config, tmp_path):
    import asyncio

    import httpx

    u, _ = _uploader(config, tmp_path, max_queued_uploads=99, max_uploads_per_day=99)
    boundary = "b0undary"
    head = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.dem\"\r\n"
            "Content-Type: application/octet-stream\r\n\r\n").encode()
    tail = f"\r\n--{boundary}--\r\n".encode()
    headers = {"content-type": f"multipart/form-data; boundary={boundary}"}

    async def run():
        release = asyncio.Event()

        async def slow_body():
            yield head
            await release.wait()
            yield b"x" * 10 + tail

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=u.app), base_url="http://t",
                                     cookies=dict(u.cookies)) as ac:
            first = asyncio.create_task(ac.post("/matches", content=slow_body(), headers=headers))
            await asyncio.sleep(0.2)                      # the first upload is now being received
            second = await ac.post("/matches", content=head + b"x" + tail, headers=headers)
            release.set()
            return (await first).status_code, second

    first, second = asyncio.run(run())
    assert second.status_code == 429 and "already have an upload" in second.json()["detail"]
    assert first == 202
    assert u.post("/matches", files={"file": ("a.dem", b"x")}).status_code == 202   # free again afterwards
