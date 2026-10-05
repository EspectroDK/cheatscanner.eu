"""Evidence clips rendered by a clips job of their own, after the match's results are saved (worker.py).

The analysis saves the results and the list of events to clip; a clips job (kind ``clips``) parses the demo
again, renders them one by one and then deletes the demo. Analysis workers step aside between two clips
when a demo arrives; clip workers render clips first.
"""

import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from cs2_analyzer.storage import models as M
from cs2_analyzer.storage.repository import Database
from cs2_analyzer.worker import AnalysisWorker

STALE = timedelta(minutes=2)
SID = 76561198000000001


@pytest.fixture
def db(tmp_path):
    d = Database(f"sqlite:///{tmp_path}/clips.sqlite")
    d.init_schema()
    yield d
    d.engine.dispose()


def _demo(tmp_path, name="a.dem"):
    p = tmp_path / name
    p.write_bytes(b"demo")
    return p


def _worker(config, db, name, role="analysis", **cfg):
    config = config.with_overrides({"worker": {"heartbeat_s": 0.05, "poll_s": 0.02, **cfg}})
    return AnalysisWorker(config, db, name=name, role=role)


def _events(match_id, n):
    from cs2_analyzer.detectors.base import EvidenceEvent

    return [EvidenceEvent(match_id=match_id, steam_id=SID, round_number=1, tick_start=10 * i, tick_peak=10 * i + 2,
                          tick_end=10 * i + 4, detector_type="hidden_tracking", severity=0.8 - 0.1 * i, reliability=0.7,
                          information_confidence=0.9, evidence_axis="HIDDEN_INFORMATION", evidence_group="information",
                          target_steam_id=76561198000000002, metrics={"tracking_corr": 0.9}, explanation="test")
            for i in range(n)]


def _fake_analysis(monkeypatch, clips_per_match=3):
    """analyze_demo(defer_clips=True) without a real demo: results saved, clips left for a clips job."""
    from test_storage_api import _fake_result

    import cs2_analyzer.pipeline as pipeline

    def fake(path, config, db=None, defer_clips=False, **kw):
        mid = path.stem
        r = _fake_result(mid)
        r.events = _events(mid, clips_per_match)
        db.begin_match(r.meta, force=False, detector_version="d", scoring_version="s")
        db.save_results(r)
        r.pending_clips = [e.id for e in r.events] if defer_clips else []
        if r.pending_clips:
            db.set_clip_plan(mid, r.pending_clips)
        r.demo_deleted = not r.pending_clips
        if r.demo_deleted:
            path.unlink()
        db.mark_completed(mid, r.demo_deleted)
        return r

    monkeypatch.setattr(pipeline, "analyze_demo", fake)


def _fake_render(monkeypatch, rendered, delay=0.0, on_clip=None):
    """Clips job without parsing: a stand-in scene, and an "mp4" written per event."""
    import cs2_analyzer.pipeline as pipeline

    scene = SimpleNamespace(world=SimpleNamespace(index_of={SID: 0}, names=["a"]), geometry=SimpleNamespace(available=True))
    monkeypatch.setattr(pipeline, "load_clip_scene", lambda path, mid, config: scene)

    def render(result, ev, path, cfg):
        time.sleep(delay)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"mp4")
        rendered.append(ev.id)
        if on_clip:
            on_clip(ev)
        return path

    monkeypatch.setattr("cs2_analyzer.evidence.clips.render_clip", render)


def _job(db, kind):
    with db.session() as s:
        return [j for j in s.query(M.AnalysisJob).order_by(M.AnalysisJob.created_at) if j.kind == kind]


def test_results_come_first_and_a_clips_job_renders_the_clips_after(config, db, tmp_path, monkeypatch):
    _fake_analysis(monkeypatch)
    rendered = []
    _fake_render(monkeypatch, rendered)
    demo = _demo(tmp_path, "m1.dem")
    jid = db.enqueue_analysis("upload", demo)["jobId"]
    w = _worker(config, db, "w1")

    w.process(db.claim_analysis_job("w1", STALE))
    assert db.get_analysis_job(jid)["status"] == "COMPLETED"
    assert demo.exists()                                          # kept for the clips
    m = db.get_match("m1")
    assert m["processingStatus"] == "COMPLETED" and not m["demoDeleted"]
    clips = db.clip_status("m1")
    assert (clips["state"], clips["total"], clips["ready"], clips["pending"]) == ("QUEUED", 3, 0, 3)
    assert clips["etaSeconds"] == 90 + 3 * 120                     # defaults: no clips job finished yet
    assert all(e["clipPending"] and e["clipUrl"] is None for e in db.match_evidence("m1"))
    (clip_job,) = _job(db, "clips")
    assert clip_job.match_id == "m1" and clip_job.path == str(demo)

    w.process(db.claim_analysis_job("w1", STALE))                 # the clips job
    assert len(rendered) == 3
    assert db.get_analysis_job(clip_job.id)["status"] == "COMPLETED"
    assert not demo.exists() and db.get_match("m1")["demoDeleted"]
    assert db.clip_status("m1")["state"] == "DONE"
    ev = db.match_evidence("m1")
    assert all(e["clipUrl"] and not e["clipPending"] for e in ev)
    assert db.player_evidence(SID)[0]["clipUrl"]
    plan = db.clip_plan("m1")
    assert plan["rendered"] == 3 and plan["preps"] == 1


def test_an_analysis_worker_steps_aside_between_clips_when_a_demo_arrives(config, db, tmp_path, monkeypatch):
    _fake_analysis(monkeypatch)
    demo = _demo(tmp_path, "m1.dem")
    db.enqueue_analysis("upload", demo)
    w = _worker(config, db, "w1")
    w.process(db.claim_analysis_job("w1", STALE))

    rendered = []
    _fake_render(monkeypatch, rendered,
                 on_clip=lambda ev: len(rendered) == 1 and db.enqueue_analysis("upload", _demo(tmp_path, "m2.dem")))
    job = w.claim()
    assert job["kind"] == "clips"
    w.process(job)
    assert len(rendered) == 1                                     # one clip, then the new demo goes first
    again = db.get_analysis_job(job["jobId"])
    assert again["status"] == "QUEUED" and db.clip_status("m1")["pending"] == 2
    assert demo.exists()

    nxt = w.claim()
    assert nxt["kind"] == "upload"                                # the new demo, before the rest of the clips
    w.process(nxt)
    clip_worker = _worker(config, db, "c1", role="clips")
    first = clip_worker.claim()
    assert first["kind"] == "clips" and first["jobId"] == job["jobId"]   # the older match's clips first
    clip_worker.process(first)
    assert db.clip_status("m1")["state"] == "DONE" and not demo.exists()
    assert sorted(rendered) == sorted(set(rendered))              # no clip rendered twice


def test_a_clip_worker_renders_clips_while_demos_wait(config, db, tmp_path, monkeypatch):
    _fake_analysis(monkeypatch)
    db.enqueue_analysis("upload", _demo(tmp_path, "m1.dem"))
    _worker(config, db, "w1").process(db.claim_analysis_job("w1", STALE))
    db.enqueue_analysis("upload", _demo(tmp_path, "m2.dem"))       # a demo waits, and so do m1's clips
    rendered = []
    _fake_render(monkeypatch, rendered)
    c = _worker(config, db, "c1", role="clips")
    job = c.claim()
    assert job["kind"] == "clips"
    c.process(job)
    assert len(rendered) == 3                                     # a clip worker doesn't step aside
    assert c.claim()["kind"] == "upload"                          # no clips left: it analyzes


def test_a_paused_clips_job_hands_itself_back_after_the_clip_it_is_drawing(config, db, tmp_path, monkeypatch):
    _fake_analysis(monkeypatch)
    demo = _demo(tmp_path, "m1.dem")
    db.enqueue_analysis("upload", demo)
    pause = tmp_path / ".pause-analysis"
    c = _worker(config, db, "c1", role="clips", pause_file=str(pause))
    c.process(c.claim())
    rendered = []
    _fake_render(monkeypatch, rendered, on_clip=lambda ev: pause.touch())   # the deploy pauses during the first clip
    job = c.claim()
    c.process(job)
    assert len(rendered) == 1 and db.get_analysis_job(job["jobId"])["status"] == "QUEUED"
    assert db.clip_status("m1")["pending"] == 2 and demo.exists()
    assert db.analysis_queue(STALE)["busy"] == 0                  # the deploy can go on


def test_a_stopped_clips_job_keeps_its_finished_clips(config, db, tmp_path, monkeypatch):
    from cs2_analyzer.worker import _Shutdown

    _fake_analysis(monkeypatch)
    db.enqueue_analysis("upload", _demo(tmp_path, "m1.dem"))
    w = _worker(config, db, "w1")
    w.process(w.claim())
    rendered = []

    def stop(ev):
        if len(rendered) == 2:
            raise _Shutdown()

    _fake_render(monkeypatch, rendered, on_clip=stop)
    job = w.claim()
    with pytest.raises(_Shutdown):
        w.process(job)
    assert db.get_analysis_job(job["jobId"])["status"] == "QUEUED"
    assert db.clip_status("m1")["pending"] == 2                   # the first clip is saved
    _fake_render(monkeypatch, rendered)
    w.process(w.claim())
    assert db.clip_status("m1")["state"] == "DONE" and len(rendered) == 4


def test_a_clips_job_whose_demo_is_gone_fails_and_clears_the_wait(config, db, tmp_path, monkeypatch):
    _fake_analysis(monkeypatch)
    demo = _demo(tmp_path, "m1.dem")
    db.enqueue_analysis("upload", demo)
    w = _worker(config, db, "w1")
    w.process(w.claim())
    demo.unlink()
    job = w.claim()
    w.process(job)
    assert db.get_analysis_job(job["jobId"])["status"] == "FAILED"
    st = db.clip_status("m1")
    assert st["state"] == "FAILED" and st["pending"] == 0
    assert not any(e["clipPending"] for e in db.match_evidence("m1"))


def test_eta_counts_the_clips_queued_ahead_and_shares_them_among_clip_workers(config, db, tmp_path, monkeypatch):
    _fake_analysis(monkeypatch, clips_per_match=2)
    w = _worker(config, db, "w1")
    for mid in ("m1", "m2"):
        db.enqueue_analysis("upload", _demo(tmp_path, f"{mid}.dem"))
        w.process(w.claim())
    # m1's clips: 90 s preparing + 2 x 120 s; m2 waits behind them too.
    assert db.clip_status("m1")["etaSeconds"] == 330
    assert db.clip_status("m2")["etaSeconds"] == 660
    db.worker_seen("c1", None, "clips")
    db.worker_seen("c2", None, "clips")
    assert db.clip_status("m2")["etaSeconds"] == 330 / 2 + 330
    q = db.analysis_queue(STALE)
    assert (q["queued"], q["clipsQueued"], q["clipWorkers"]) == (0, 2, 2)

    # Real render times replace the defaults once a clips job has finished.
    rendered = []
    _fake_render(monkeypatch, rendered)
    w.process(w.claim())
    plan = db.clip_plan("m1")
    per_clip = plan["renderSeconds"] / plan["rendered"]
    eta = db.clip_status("m2")["etaSeconds"]
    assert eta == round(plan["prepSeconds"] + 2 * per_clip)


def test_match_page_shows_the_clips_on_their_way(config, db, tmp_path, monkeypatch):
    from cs2_analyzer.api.app import create_app

    _fake_analysis(monkeypatch)
    db.enqueue_analysis("upload", _demo(tmp_path, "m1.dem"))
    _worker(config, db, "w1").process(db.claim_analysis_job("w1", STALE))
    client = TestClient(create_app(config.with_overrides({"api": {"workers": 0}}), db_url=db.url))
    m = client.get("/matches/m1").json()
    assert m["processingStatus"] == "COMPLETED"
    assert m["clips"]["state"] == "QUEUED" and m["clips"]["pending"] == 3 and m["clips"]["etaSeconds"] > 0
    assert all(e["clipPending"] for e in client.get("/matches/m1/evidence").json())


def test_without_deferred_clips_the_analysis_renders_them_itself(config, db, tmp_path, monkeypatch):
    _fake_analysis(monkeypatch)
    demo = _demo(tmp_path, "m1.dem")
    db.enqueue_analysis("upload", demo)
    _worker(config, db, "w1", defer_clips=False).process(db.claim_analysis_job("w1", STALE))
    assert not _job(db, "clips") and not demo.exists() and db.clip_status("m1") is None


def test_render_clips_draws_a_real_clip_from_a_stored_event(config, db, tmp_path, monkeypatch):
    """The event as stored in the database draws the same clip as the one the analysis found."""
    import cs2_analyzer.pipeline as pipeline
    from cs2_analyzer.pipeline import ClipScene, build_analysis
    from cs2_analyzer.testing.synthetic import SynthPlayer, aim_at, static, wall_scenario, weave

    sc = wall_scenario(30)
    T = sc.T
    enemy = weave(T, sc.tickrate, 800, 0, 250, 3.0, start=sc.s(16.0))
    observer = static(T, (0, 0, 0))
    eye, head = observer.copy(), enemy.copy()
    eye[:, 2] += 64
    head[:, 2] += 64
    pitch, yaw = aim_at(eye, head, lag_ticks=10, noise_deg=0.3)
    sc.add(SynthPlayer(1001, 3, "observer", observer, pitch, yaw))
    sc.add(SynthPlayer(2001, 2, "enemy", enemy, pitch * 0, yaw * 0 + 180))
    demo = sc.to_demo()
    world, geometry, smoke, vis, knowledge, _, _, events = build_analysis(
        demo, config, detector_names=["hidden_tracking"], geometry=sc.geometry())
    ev = max((e for e in events if e.steam_id == 1001), key=lambda e: e.severity)
    mid = demo.meta.match_id
    db.begin_match(demo.meta, force=False, detector_version="d", scoring_version="s")
    with db.session() as s:
        s.add(M.EvidenceEvent(id=ev.id, match_id=mid, steam_id=ev.steam_id, round_number=ev.round_number,
                              tick_start=ev.tick_start, tick_peak=ev.tick_peak, tick_end=ev.tick_end,
                              detector_type=ev.detector_type, detector_version=ev.detector_version, severity=ev.severity,
                              reliability=ev.reliability, information_confidence=ev.information_confidence,
                              confidence=ev.confidence, evidence_axis=ev.evidence_axis, evidence_group=ev.evidence_group,
                              target_steam_id=ev.target_steam_id, metrics=ev.to_dict()["metrics"],
                              context=ev.to_dict()["context"], explanation=ev.explanation))
    db.set_clip_plan(mid, [ev.id])
    monkeypatch.setattr(pipeline, "load_clip_scene",
                        lambda path, m, cfg: ClipScene(world, geometry, smoke, vis, knowledge))
    cfg = config.with_overrides({"evidence": {"clip_seconds_before": 0.5, "clip_seconds_after": 0.25, "clip_fps": 8,
                                              "clip_width": 320, "clip_height": 180}})
    assert pipeline.render_clips(tmp_path / "x.dem", mid, cfg, db) == 1
    (e,) = db.match_evidence(mid)
    assert e["clipUrl"] and e["posterUrl"] and not e["clipPending"]


def test_old_databases_get_the_new_columns(tmp_path):
    from sqlalchemy import create_engine, inspect, text

    url = f"sqlite:///{tmp_path}/old.sqlite"
    eng = create_engine(url)
    with eng.begin() as c:   # analysis_workers as it was before worker roles
        c.execute(text("CREATE TABLE analysis_workers (name VARCHAR(128) PRIMARY KEY, started_at DATETIME, "
                       "last_seen_at DATETIME, job_id VARCHAR(32))"))
    eng.dispose()
    d = Database(url)
    d.init_schema()
    d.init_schema()                                               # a second start changes nothing
    assert "role" in {c["name"] for c in inspect(d.engine).get_columns("analysis_workers")}
    d.worker_seen("c1", None, "clips")
    assert d.analysis_queue(STALE)["clipWorkers"] == 1


def test_workers_with_both_roles_share_a_busy_queue(config, db, tmp_path, monkeypatch):
    """Two analysis and two clip workers, five demos: everything ends analyzed, every clip rendered once."""
    _fake_analysis(monkeypatch, clips_per_match=2)
    rendered = []
    _fake_render(monkeypatch, rendered, delay=0.01)
    for i in range(5):
        db.enqueue_analysis("upload", _demo(tmp_path, f"m{i}.dem"))
    slow = {"heartbeat_s": 0.5, "poll_s": 0.05}                    # SQLite has one writer at a time
    workers = ([_worker(config, db, f"a{i}", **slow) for i in range(2)]
               + [_worker(config, db, f"c{i}", role="clips", **slow) for i in range(2)])
    wake = workers[0].wake
    threads = []
    for w in workers:
        w.wake = wake
        t = threading.Thread(target=w.run, daemon=True)
        t.start()
        threads.append(t)
    end = time.monotonic() + 60
    while time.monotonic() < end and not all((db.clip_status(f"m{i}") or {}).get("state") == "DONE" for i in range(5)):
        time.sleep(0.05)
    for w in workers:
        w.stop.set()
    wake.set()
    for t in threads:
        t.join(5)
    assert len(rendered) == 10 and len(set(rendered)) == 10
    assert all(db.clip_status(f"m{i}")["state"] == "DONE" for i in range(5))
    assert not list(tmp_path.glob("m*.dem"))


def test_a_clips_job_lost_between_analysis_and_queueing_is_queued_again(config, db, tmp_path, monkeypatch):
    """The worker stopped after saving the results but before queueing the clips: the retried job finds the
    match analyzed and queues its clips from the same demo instead of deleting it."""
    from cs2_analyzer.storage.repository import AlreadyProcessedError

    import cs2_analyzer.pipeline as pipeline

    _fake_analysis(monkeypatch)
    demo = _demo(tmp_path, "m1.dem")
    pipeline.analyze_demo(demo, config, db=db, defer_clips=True)        # results saved, no clips job
    assert not _job(db, "clips")

    def already(path, *a, **kw):
        raise AlreadyProcessedError("m1", "COMPLETED")

    monkeypatch.setattr(pipeline, "analyze_demo", already)
    jid = db.enqueue_analysis("upload", demo)["jobId"]
    _worker(config, db, "w1").process(db.claim_analysis_job("w1", STALE))
    assert db.get_analysis_job(jid)["status"] == "DUPLICATE" and demo.exists()
    (clip_job,) = _job(db, "clips")
    assert clip_job.match_id == "m1" and clip_job.path == str(demo)
