"""Admin page data (api/admin.py, storage/stats.py): who may see it, and what it counts."""

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from cs2_analyzer.api.app import create_app
from cs2_analyzer.storage import models as M
from cs2_analyzer.storage.repository import Database
from test_access import ENEMY, ME, MATE, _seed
from test_auth import PUBLIC, FakeSteam
from test_companion import _link, _sign_in


def _client(config, tmp_path, admins=(ME,)):
    url = f"sqlite:///{tmp_path}/admin.sqlite"
    db = Database(url)
    db.init_schema()
    _seed(db)
    cfg = config.with_overrides({"auth": {"enabled": True, "public_url": PUBLIC, "admin_steam_ids": [str(a) for a in admins]}})
    return TestClient(create_app(cfg, db_url=url, steam_http_post=FakeSteam(), profile_fetcher=lambda sid: {})), db


def test_only_admins_see_the_admin_page(config, tmp_path):
    c, _ = _client(config, tmp_path)
    assert c.get("/admin/overview").status_code == 404          # not signed in
    _sign_in(c, MATE)
    assert c.get("/me").json()["isAdmin"] is False
    assert c.get("/admin/overview").status_code == 404
    c.cookies.clear()
    _sign_in(c, ME)
    assert c.get("/me").json()["isAdmin"] is True
    assert c.get("/admin/overview").status_code == 200


def test_admin_needs_a_browser_session_not_an_app_token(config, tmp_path):
    c, _ = _client(config, tmp_path)
    token = _link(c)
    app = TestClient(c.app)
    assert app.get("/admin/overview", headers={"Authorization": f"Bearer {token}"}).status_code == 404


def test_overview_counts(config, tmp_path):
    c, db = _client(config, tmp_path)
    now = datetime.now(timezone.utc)
    with db.session() as s:
        for mid, secs in (("m1", 40.0), ("m2", 80.0)):
            m = s.get(M.Match, mid)
            m.processed_at = now - timedelta(hours=1)
            m.meta = {"analysis_s": secs, "queue_wait_s": 5.0}
        s.get(M.Match, "m3").processing_status = "FAILED"
        s.get(M.PlayerAssessment, ENEMY).classification = "VERY_HIGH"
        s.get(M.PlayerMatchAssessment, (MATE, "m1")).classification = "HIGH"   # HIGH in one match, NORMAL overall
    token = _link(c)  # signs in as ME and links a companion app
    app = TestClient(c.app)
    ok = app.post("/lobby/risk", json={"players": [{"steamId": str(ENEMY)}, {"steamId": str(MATE)}]},
                  headers={"Authorization": f"Bearer {token}"})
    assert ok.status_code == 200

    o = c.get("/admin/overview").json()
    assert o["users"]["total"] == 1 and o["users"]["active24h"] == 1
    assert o["matches"]["analyzed"] == 2 and o["matches"]["failed"] == 1
    assert o["matches"]["analyzed24h"] == 2 and sum(d["count"] for d in o["matches"]["perDay"]) == 2
    assert o["matches"]["byMap"] == [{"map": "de_mirage", "count": 2}]
    assert o["players"]["total"] == 5 and o["players"]["byClass"]["HIGH"] == 1 and o["players"]["byClass"]["NORMAL"] == 4
    # Per match: m3 failed; MATE counted once at HIGH, ENEMY stays NORMAL in its match.
    assert o["players"]["byHighestMatchClass"] == {"NORMAL": 4, "ELEVATED": 0, "HIGH": 1, "INSUFFICIENT_DATA": 0}
    assert o["speed"]["analysisSeconds"] == {"count": 2, "median": 60.0, "p90": 80.0, "mean": 60.0}
    assert o["speed"]["queueWaitSeconds"]["median"] == 5.0
    assert o["companion"]["linkedApps"] == 1 and o["companion"]["activeLast15m"] == 1
    assert o["companion"]["lookups24h"] == 1 and o["companion"]["lookupUsers24h"] == 1
    assert o["live"] == {"queued": 0, "processing": 0, "uploads": 0, "oldestWaitingSeconds": 0,
                         "receivingUploads": 0, "workers": 1}
    assert o["system"]["fetcherLastSeenAt"] is None
    # The lookup is only counted: no Steam IDs are kept.
    with db.session() as s:
        assert [(r.players, r.user_id is not None) for r in s.query(M.LobbyLookup)] == [(2, True)]


def test_open_without_auth(config, tmp_path):
    url = f"sqlite:///{tmp_path}/local.sqlite"
    c = TestClient(create_app(config, db_url=url))
    assert c.get("/admin/overview").status_code == 200


def test_overview_lists_map_meshes(config, tmp_path):
    from test_map_status import _mesh

    _mesh(tmp_path / "maps" / "de_mirage.tri", patch=14185)
    cfg = config.with_overrides({"geometry": {"maps_dir": str(tmp_path / "maps")},
                                 "evidence": {"render_maps_dir": str(tmp_path / "maps" / "render")}})
    c = TestClient(create_app(cfg, db_url=f"sqlite:///{tmp_path}/maps.sqlite"))
    maps = c.get("/admin/overview").json()["maps"]
    assert maps == [{"map": "de_mirage", "mesh": True, "renderMesh": False, "meshPatch": 14185,
                     "meshUpdatedAt": maps[0]["meshUpdatedAt"], "latestDemoPatch": None,
                     "missing": False, "stale": False}]
