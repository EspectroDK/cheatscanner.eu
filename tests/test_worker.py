"""Analysis queue in the database and the workers that share it (worker.py).

Set CS2A_TEST_PG_URL to also run these against a real PostgreSQL (row locks, ON CONFLICT); its tables are
dropped and created again for each test.
"""

import os
import threading
import time
from datetime import datetime, timedelta, timezone

import pytest

from cs2_analyzer.storage import models as M
from cs2_analyzer.storage.repository import Database
from cs2_analyzer.worker import AnalysisWorker, _Shutdown

STALE = timedelta(minutes=2)


@pytest.fixture(params=["sqlite", "postgresql"])
def db(request, tmp_path):
    if request.param == "sqlite":
        d = Database(f"sqlite:///{tmp_path}/queue.sqlite")
    elif os.environ.get("CS2A_TEST_PG_URL"):
        d = Database(os.environ["CS2A_TEST_PG_URL"])
        M.Base.metadata.drop_all(d.engine)
    else:
        pytest.skip("set CS2A_TEST_PG_URL to test against PostgreSQL")
    d.init_schema()
    yield d
    d.engine.dispose()


def _demo(tmp_path, name="a.dem"):
    p = tmp_path / name
    p.write_bytes(b"demo")
    return p


def _worker(config, db, name, **cfg):
    config = config.with_overrides({"worker": {"heartbeat_s": 0.05, "poll_s": 0.02, **cfg}})
    return AnalysisWorker(config, db, name=name)


def _run_until(worker, done, timeout=10.0):
    t = threading.Thread(target=worker.run, daemon=True)
    t.start()
    end = time.monotonic() + timeout
    ok = False
    while time.monotonic() < end and not (ok := done()):
        time.sleep(0.02)
    worker.stop.set()
    worker.wake.set()
    t.join(5)
    assert ok


def test_a_job_is_claimed_once_and_taken_over_when_its_worker_dies(db, tmp_path):
    job = db.enqueue_analysis("upload", _demo(tmp_path), user_id=None)
    assert job["status"] == "QUEUED"
    first = db.claim_analysis_job("w1", STALE)
    assert first["jobId"] == job["jobId"] and first["attempts"] == 1 and not first["takenOver"]
    assert db.claim_analysis_job("w2", STALE) is None             # nobody else gets it
    assert db.heartbeat_analysis_job(job["jobId"], "w1")
    assert not db.heartbeat_analysis_job(job["jobId"], "w2")

    with db.session() as s:                                       # w1 stops sending heartbeats
        s.get(M.AnalysisJob, job["jobId"]).heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    again = db.claim_analysis_job("w2", STALE)
    assert again["jobId"] == job["jobId"] and again["attempts"] == 2 and again["takenOver"]
    assert not db.heartbeat_analysis_job(job["jobId"], "w1")      # w1 no longer owns it


def test_racing_workers_never_claim_the_same_job(db, tmp_path):
    ids = {db.enqueue_analysis("upload", _demo(tmp_path, f"{i}.dem"))["jobId"] for i in range(30)}
    claimed, lock = [], threading.Lock()

    def claimer(name):
        misses = 0
        while misses < 20:
            j = db.claim_analysis_job(name, STALE)
            if j is None:
                misses += 1
                continue
            with lock:
                claimed.append(j["jobId"])

    threads = [threading.Thread(target=claimer, args=(f"w{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert sorted(claimed) == sorted(ids)


def test_the_same_match_is_never_analyzed_twice_at_once(db, tmp_path):
    a = db.enqueue_analysis("upload", _demo(tmp_path, "a.dem"))["jobId"]
    b = db.enqueue_analysis("fetch", _demo(tmp_path, "b.dem"))["jobId"]
    db.claim_analysis_job("w1", STALE)
    db.claim_analysis_job("w2", STALE)
    assert db.lock_analysis_match(a, ["match:m1", "sha256:x"], STALE)
    assert not db.lock_analysis_match(b, ["match:m1"], STALE)
    assert not db.lock_analysis_match(b, ["match:other", "sha256:x"], STALE)   # same demo file, other name
    db.finish_analysis_job(a, "COMPLETED", match_id="m1")
    assert db.lock_analysis_match(b, ["match:m1", "sha256:x"], STALE)


def test_two_workers_share_the_queue_and_a_second_copy_becomes_a_duplicate(config, db, tmp_path, monkeypatch):
    """Two copies of one match uploaded at once: one worker analyzes it, the other waits, then finds it done."""
    from test_storage_api import _fake_result

    import cs2_analyzer.pipeline as pipeline

    running = []

    def fake_analyze(path, config, db=None, on_parsed=None, **kw):
        r = _fake_result("same-match")
        existing = db.find_existing("same-match", r.meta.demo_sha256)
        if existing is not None and existing.processing_status == "COMPLETED":
            from cs2_analyzer.storage.repository import AlreadyProcessedError
            raise AlreadyProcessedError("same-match", "COMPLETED")
        on_parsed(r.meta)
        running.append(path.name)
        assert len(running) == 1, "two workers analyzed the same match at once"
        time.sleep(0.3)
        db.begin_match(r.meta, force=False, detector_version="d", scoring_version="s")
        db.save_results(r)
        db.mark_completed("same-match", True)
        running.remove(path.name)
        path.unlink()
        r.demo_deleted = True
        return r

    monkeypatch.setattr(pipeline, "analyze_demo", fake_analyze)
    jobs = [db.enqueue_analysis("upload", _demo(tmp_path, f"{n}.dem"))["jobId"] for n in ("one", "two", "three")]
    w1 = _worker(config, db, "w1", busy_retry_s=0.1)
    w2 = _worker(config, db, "w2", busy_retry_s=0.1)
    w2.wake = w1.wake
    t2 = threading.Thread(target=w2.run, daemon=True)
    t2.start()
    _run_until(w1, lambda: all(db.get_analysis_job(j)["status"] in ("COMPLETED", "DUPLICATE") for j in jobs))
    w2.stop.set()
    t2.join(5)
    statuses = sorted(db.get_analysis_job(j)["status"] for j in jobs)
    assert statuses == ["COMPLETED", "DUPLICATE", "DUPLICATE"]
    assert all(db.get_analysis_job(j)["matchId"] == "same-match" for j in jobs)
    with db.session() as s:
        assert s.query(M.AnalysisLock).count() == 0


def test_a_stopping_worker_hands_its_demo_back(config, db, tmp_path, monkeypatch):
    import cs2_analyzer.pipeline as pipeline

    def interrupted(path, *a, **kw):
        raise _Shutdown()

    monkeypatch.setattr(pipeline, "analyze_demo", interrupted)
    demo = _demo(tmp_path)
    jid = db.enqueue_analysis("upload", demo)["jobId"]
    w = _worker(config, db, "w1")
    with pytest.raises(_Shutdown):
        w.process(db.claim_analysis_job("w1", STALE))
    job = db.get_analysis_job(jid)
    assert job["status"] == "QUEUED" and job["attempts"] == 0 and "restarted" in job["error"]
    assert demo.exists()                                          # the next worker analyzes the same file


def test_a_demo_whose_worker_keeps_dying_fails(config, db, tmp_path):
    jid = db.enqueue_analysis("upload", _demo(tmp_path))["jobId"]
    for _ in range(3):
        db.claim_analysis_job("w1", STALE)
        with db.session() as s:
            s.get(M.AnalysisJob, jid).heartbeat_at = datetime.now(timezone.utc) - timedelta(minutes=5)
    w = _worker(config, db, "w2")
    w.process(db.claim_analysis_job("w2", STALE))
    job = db.get_analysis_job(jid)
    assert job["status"] == "FAILED" and "stopped 3 times" in job["error"]


def test_workers_show_on_the_admin_numbers(config, db, tmp_path):
    w = _worker(config, db, "box-1")
    _run_until(w, lambda: db.analysis_queue(STALE)["workers"] == 1)
    assert db.analysis_queue(STALE)["workers"] == 0               # gone after a clean stop
    db.enqueue_analysis("upload", _demo(tmp_path))
    q = db.analysis_queue(STALE)
    assert (q["queued"], q["processing"], q["uploads"]) == (1, 0, 1)


def test_queue_command_prints_the_numbers(db, tmp_path, capsys):
    from cs2_analyzer.cli import main

    db.enqueue_analysis("upload", _demo(tmp_path))
    assert main(["--db-url", db.url, "queue", "--field", "queued"]) == 0
    assert capsys.readouterr().out.strip() == "1"


def test_concurrent_saves_create_each_new_player_once(db):
    """Two workers saving matches with the same new player must not collide on the players table."""
    rows = [{"steam_id": 7, "first_seen_at": datetime.now(timezone.utc), "last_seen_at": datetime.now(timezone.utc),
             "matches_analyzed": 0}]
    db._insert_missing(M.Player, rows)
    db._insert_missing(M.Player, rows)
    with db.session() as s:
        assert s.query(M.Player).count() == 1



def test_wingman_and_short_demos_are_skipped(config, db, tmp_path, monkeypatch):
    """Only Premier and Competitive are analyzed; the job ends SKIPPED with the reason and no match row."""
    from types import SimpleNamespace

    import cs2_analyzer.pipeline as pipeline

    def meta(mode, source="rank_update.rank_type_id"):
        return SimpleNamespace(mode=mode, mode_source=source)

    assert "Wingman" in pipeline.skip_reason(meta("wingman"), 4, config)
    assert "Danger Zone" in pipeline.skip_reason(meta("danger_zone"), 10, config)
    assert "4 players" in pipeline.skip_reason(meta(None, None), 4, config)
    assert pipeline.skip_reason(meta("premier"), 10, config) is None
    assert pipeline.skip_reason(meta("competitive"), 10, config) is None
    assert pipeline.skip_reason(meta(None, None), 10, config) is None          # tournament demo: no rank updates
    assert pipeline.skip_reason(meta("valve_matchmaking", "server_name"), 10, config) is None

    def wingman(path, config, filter_modes=False, **kw):
        assert filter_modes
        raise pipeline.SkippedMatch(pipeline.skip_reason(meta("wingman"), 4, config))

    monkeypatch.setattr(pipeline, "analyze_demo", wingman)
    jid = db.enqueue_analysis("upload", _demo(tmp_path))["jobId"]
    _worker(config, db, "w1").process(db.claim_analysis_job("w1", STALE))
    job = db.get_analysis_job(jid)
    assert job["status"] == "SKIPPED" and "Wingman" in job["error"]
    with db.session() as s:
        assert s.query(M.Match).count() == 0 and s.query(M.AnalysisLock).count() == 0
