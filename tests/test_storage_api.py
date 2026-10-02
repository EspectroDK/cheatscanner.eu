from datetime import datetime, timezone

import numpy as np
from fastapi.testclient import TestClient

from cs2_analyzer.api.app import create_app
from cs2_analyzer.pipeline import AnalysisResult
from cs2_analyzer.storage.repository import AlreadyProcessedError, Database


def _fake_result(match_id="m1"):
    import pandas as pd

    from cs2_analyzer.detectors.base import EvidenceEvent
    from cs2_analyzer.parser.base import MatchMeta
    from cs2_analyzer.scoring.aggregate import assess_match

    meta = MatchMeta(match_id=match_id, source="test", map_name="de_mirage", mode="premier", mode_source=None, played_at=None,
                     server_name=None, patch_version=None, tickrate=64, demo_sha256=match_id.ljust(64, "a"), first_tick=0,
                     last_tick=100, parser_name="t", parser_version="0", extra={})
    ev = EvidenceEvent(match_id=match_id, steam_id=76561198000000001, round_number=1, tick_start=1, tick_peak=2, tick_end=3,
                       detector_type="hidden_tracking", severity=0.8, reliability=0.7, information_confidence=0.9,
                       evidence_axis="HIDDEN_INFORMATION", evidence_group="information", target_steam_id=76561198000000002,
                       metrics={"tracking_corr": 0.95}, explanation="test")
    stats = pd.DataFrame([{"steam_id": 76561198000000001, "name": "a", "team": 2, "kills": 1, "deaths": 0, "assists": 0,
                           "headshots": 1, "damage": 100, "score": None, "rank_type": 11, "rank_old": 1, "rank_new": 2}])
    rounds = pd.DataFrame([{"round_number": 1, "start_tick": 0, "freeze_end_tick": 10, "end_tick": 100, "winner": 2,
                            "reason": 9, "live": True}])
    a = assess_match(76561198000000001, match_id, [ev], 30, 10, {"min_encounters": 1, "min_rounds": 1})
    r = AnalysisResult.__new__(AnalysisResult)
    r.meta, r.events, r.match_stats, r.rounds = meta, [ev], stats, rounds
    r.assessments = {76561198000000001: a}
    r.fingerprints = {76561198000000001: {"snap.peak_velocity_deg_s.median": 500.0}}
    return r


def test_repository_roundtrip_dedupe_and_history(tmp_path):
    db = Database(f"sqlite:///{tmp_path}/t.sqlite")
    db.init_schema()
    r = _fake_result()
    db.begin_match(r.meta, force=False, detector_version="d", scoring_version="s")
    db.save_results(r)
    h = db.recompute_history(76561198000000001, {})
    db.mark_completed("m1", demo_deleted=True)
    try:
        db.begin_match(r.meta, force=False, detector_version="d", scoring_version="s")
        raise AssertionError("duplicate match must be rejected")
    except AlreadyProcessedError:
        pass
    db.begin_match(r.meta, force=True, detector_version="d", scoring_version="s")  # explicit reprocess allowed
    db.save_results(r)
    db.mark_completed("m1", demo_deleted=True)
    h = db.recompute_history(76561198000000001, {})
    assert h["matches_analyzed"] == 1
    assert db.get_match("m1")["players"][0]["steamId"] == "76561198000000001"
    assert db.player_evidence(76561198000000001)[0]["metrics"]["tracking_corr"] == 0.95
    assert db.previous_fingerprints(76561198000000001, "other")[0]["snap.peak_velocity_deg_s.median"] == 500.0
    assert "password" not in db.safe_url()


def test_api_endpoints(tmp_path, config):
    url = f"sqlite:///{tmp_path}/api.sqlite"
    db = Database(url)
    db.init_schema()
    r = _fake_result("m2")
    db.begin_match(r.meta, force=False, detector_version="d", scoring_version="s")
    db.save_results(r)
    db.mark_completed("m2", True)
    db.recompute_history(76561198000000001, {})
    client = TestClient(create_app(config, db_url=url))
    assert client.get("/health").json()["matches"] == 1
    assert client.get("/matches/m2").json()["map"] == "de_mirage"
    assert client.get("/matches/nope").status_code == 404
    assert client.get("/players/76561198000000001").json()["matchesAnalyzed"] == 1
    assert len(client.get("/players/76561198000000001/matches").json()) == 1
    assert client.get("/players/76561198000000001/evidence").json()[0]["detector"] == "hidden_tracking"
    risk = client.get("/risk/76561198000000001").json()
    assert risk["classification"] and "axes" in risk and "disclaimer" in risk
    batch = client.post("/risk/batch", json={"steamIds": ["76561198000000001", "76561198999999999"]}).json()
    assert batch["results"][1]["classification"] == "INSUFFICIENT_DATA"
    assert client.get("/risk/notanid").status_code == 400
    assert client.post("/matches", files={"file": ("x.txt", b"abc")}).status_code == 400
    assert client.post("/matches/import", json={"path": "/etc/passwd"}).status_code == 403


def test_upload_goes_through_the_database_queue(tmp_path, config, monkeypatch, caplog):
    # deploy/server-deploy.sh asks `cs2-analyzer queue --field processing` and waits for 0; a failed analysis
    # must not keep it up.
    import time
    from datetime import timedelta

    import cs2_analyzer.pipeline as pipeline

    config = config.with_overrides({"output": {"parquet_dir": str(tmp_path / "work")}})
    url = f"sqlite:///{tmp_path}/api.sqlite"
    seen = []

    def failing(path, *a, db=None, **kw):
        seen.append(db.analysis_queue(timedelta(minutes=2))["processing"])
        raise ValueError("bad demo")

    monkeypatch.setattr(pipeline, "analyze_demo", failing)
    caplog.set_level("INFO", logger="cs2_analyzer")
    client = TestClient(create_app(config, db_url=url))
    job = client.post("/matches", files={"file": ("x.dem", b"not a demo")}).json()
    assert job["status"] == "QUEUED" and job["file"].endswith("_x.dem")
    for _ in range(100):
        if client.get(f"/jobs/{job['jobId']}").json()["status"] == "FAILED":
            break
        time.sleep(0.05)
    got = client.get(f"/jobs/{job['jobId']}").json()
    assert got["status"] == "FAILED" and "bad demo" in got["error"] and "trace" not in got
    assert seen == [1]
    assert Database(url).analysis_queue(timedelta(minutes=2))["processing"] == 0
    assert client.get("/jobs/nope").status_code == 404
    # the server log shows each analysis (docker compose logs -f api / worker)
    assert "analysis of" in caplog.text and "started" in caplog.text and "failed" in caplog.text


def test_player_class_from_play_pattern_is_explained(tmp_path):
    """ELEVATED from the play pattern alone (no evidence events): the player API says so."""
    from cs2_analyzer.storage import models as M

    db = Database(f"sqlite:///{tmp_path}/why.sqlite")
    db.init_schema()
    sid = 76561198000000009
    feats = {"trigger_timing.trigger_ms": {"n": 40, "mean_llr": 0.4, "contribution": 12.0},
             "aim_acquisition.peak_jerk_deg_s3": {"n": 40, "mean_llr": -0.1, "contribution": -3.0}}
    with db.session() as s:
        s.add(M.Player(steam_id=sid, matches_analyzed=3))
        for i in range(3):
            s.add(M.Match(match_id=f"w{i}", map="de_mirage", processing_status="COMPLETED",
                          played_at=datetime(2026, 9, 1 + i, tzinfo=timezone.utc)))
            s.flush()
            s.add(M.PlayerMatchAssessment(
                steam_id=sid, match_id=f"w{i}", classification="ELEVATED", overall_evidence_score=0.3,
                encounters_analyzed=40, evidence_event_count=0,
                details={"player_evidence": {"score": 9.0, "clean_percentile": 0.99, "strength": 0.3, "features": feats}}))
    h = db.recompute_history(sid, {})
    assert h["classification"] == "ELEVATED" and db.player_evidence(sid) == []
    a = db.get_player(sid)["assessment"]
    assert a["eventEvidenceScore"] == 0.0
    assert a["profile"]["strength"] > 0.25 and a["profile"]["matches"] == 3
    assert a["profile"]["features"] == [{"feature": "trigger_timing.trigger_ms", "contribution": 36.0, "matches": 3}]
    assert db.player_timeline(sid)[0]["profileStrength"] == 0.3
