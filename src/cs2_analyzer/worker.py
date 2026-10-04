"""Analysis workers: take demos from the queue in the database and analyze them.

The queue is the ``analysis_jobs`` table (storage/models.py). The API only adds jobs (uploads, path
imports, matches the demo fetcher found); workers claim them one at a time with
``SELECT ... FOR UPDATE SKIP LOCKED``, so any number of them can run side by side:

- ``cs2-analyzer worker``: one worker per process, the production setup (the ``worker`` service in
  docker-compose.yml, scaled with ``CS2A_WORKERS``);
- ``api.workers`` threads inside ``cs2-analyzer serve`` (default 1), so a local server analyzes uploads
  without a separate worker. Production sets it to 0.

A running job sends a heartbeat every ``worker.heartbeat_s``; a job whose heartbeat is older than
``worker.stale_after_s`` (worker killed, server restarted) is claimed again by another worker, up to
``worker.max_attempts`` times. Once a demo is parsed its worker locks the match (by match id and demo
hash), so the same match is never analyzed twice at once: a second copy waits and then turns out to be a
duplicate. On SIGTERM a worker puts its job back in the queue and stops.
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
import traceback
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cs2_analyzer.config import Config
from cs2_analyzer.ingest.chat import SteamChat
from cs2_analyzer.ingest.service import download_demo, download_with_retries
from cs2_analyzer.memory import release_memory
from cs2_analyzer.storage.repository import AlreadyProcessedError, Database

log = logging.getLogger(__name__)


class MatchBusy(Exception):
    """Another worker is analyzing the same match right now."""


class _Shutdown(BaseException):
    """SIGTERM while a demo is being downloaded or analyzed (not an Exception: nothing may swallow it)."""


class AnalysisWorker:
    def __init__(self, config: Config, db: Database, name: str | None = None, demo_downloader=None,
                 wake: threading.Event | None = None, register: bool = True):
        self.config, self.db = config, db
        self.name = (name or f"{socket.gethostname()}-{os.getpid()}")[:128]
        self.download = demo_downloader or download_demo
        self.wake = wake or threading.Event()
        self.register = register           # listed on the admin page (the API counts its own threads itself)
        self.stop = threading.Event()
        self.stale_after = timedelta(seconds=float(config.get("worker.stale_after_s", 120)))
        self.heartbeat_s = float(config.get("worker.heartbeat_s", 20))
        self.poll_s = float(config.get("worker.poll_s", 2))
        self.busy_retry = timedelta(seconds=float(config.get("worker.busy_retry_s", 60)))
        self.max_attempts = int(config.get("worker.max_attempts", 3))
        self.max_compressed = int(config.get("ingest.max_download_mb", 400)) << 20
        self.max_demo = int(config.get("ingest.max_demo_mb", 1500)) << 20
        self.retry_delays = [float(d) for d in config.get("ingest.download_retry_delays_s", [10, 60, 300])]
        self.chat = SteamChat(db, str(config.get("auth.public_url", "http://localhost:8000")))
        self._interruptible = False

    # ------------------------------------------------------------------ loop

    def run(self) -> None:
        """Work until ``stop`` is set: claim, analyze, repeat; wait ``poll_s`` (or for ``wake``) when idle."""
        log.info("analysis worker %s started", self.name)
        last_seen = 0.0
        while not self.stop.is_set():
            try:
                if self.register and time.monotonic() - last_seen >= self.heartbeat_s:
                    self.db.worker_seen(self.name, None)
                    last_seen = time.monotonic()
                job = self.db.claim_analysis_job(self.name, self.stale_after)
            except Exception:
                log.warning("analysis worker %s: could not reach the database", self.name, exc_info=True)
                job = None
            if job is not None:
                try:
                    self.process(job)
                except _Shutdown:
                    break
                except Exception:  # a database error while finishing: the job's heartbeat stops, another worker retries
                    log.error("analysis worker %s: job %s broke off", self.name, job["jobId"], exc_info=True)
                continue
            if self.wake.wait(self.poll_s):
                self.wake.clear()
        if self.register:
            try:
                self.db.worker_gone(self.name)
            except Exception:
                pass
        log.info("analysis worker %s stopped", self.name)

    def run_process(self) -> None:
        """``cs2-analyzer worker``: like ``run``, and SIGTERM/SIGINT hand the current job back to the queue."""
        def on_signal(signum, frame):
            self.stop.set()
            self.wake.set()
            if self._interruptible:
                raise _Shutdown()

        signal.signal(signal.SIGTERM, on_signal)
        signal.signal(signal.SIGINT, on_signal)
        try:
            self.db.prune_workers(timedelta(days=1))
        except Exception:
            pass
        self.run()

    @contextmanager
    def interruptible(self):
        self._interruptible = True
        try:
            yield
        finally:
            self._interruptible = False

    # ------------------------------------------------------------------ one job

    def process(self, job: dict) -> None:
        jid = job["jobId"]
        done = threading.Event()
        beat = threading.Thread(target=self._heartbeat, args=(jid, done), daemon=True, name=f"heartbeat-{jid[:8]}")
        beat.start()
        try:
            if job["attempts"] > self.max_attempts:
                msg = (f"analysis stopped {job['attempts'] - 1} times without finishing "
                       f"(the worker was stopped or ran out of memory)")
                log.error("job %s (%s): %s; giving up", jid, job["file"], msg)
                self._fail(job, msg, "analysis failed: worker stopped repeatedly")
                return
            if job.get("takenOver"):
                log.warning("job %s (%s): its worker stopped sending heartbeats; analyzing it again", jid, job["file"])
            try:
                with self.interruptible():
                    if job["kind"] == "fetch" and not self._download(job):
                        return
                    self._analyze(job)
            except _Shutdown:
                log.warning("worker stopping: %s goes back to the queue", job["file"])
                self.db.requeue_analysis_job(jid, "the analysis worker restarted; queued again")
                raise
        finally:
            done.set()
            release_memory()  # an idle worker should not keep the last demo's peak

    def _heartbeat(self, jid: str, done: threading.Event) -> None:
        while not done.wait(self.heartbeat_s):
            try:
                if not self.db.heartbeat_analysis_job(jid, self.name):
                    log.warning("job %s is no longer this worker's (heartbeat refused)", jid)
                if self.register:
                    self.db.worker_seen(self.name, jid)
            except Exception:
                log.warning("heartbeat for job %s failed", jid, exc_info=True)

    def _download(self, job: dict) -> bool:
        """Fetched match: download and unpack the demo from Valve. False if that failed (job ended)."""
        code, dest = job["shareCode"], Path(job["path"])
        log.info("downloading the demo of %s", code)
        dest.unlink(missing_ok=True)  # a half-written file from a worker that stopped
        try:
            download_with_retries(self.download, job["demoUrl"], dest, self.max_compressed, self.max_demo,
                                  self.retry_delays, on_retry=lambda msg: self.db.update_share_code(code, error=msg))
        except Exception as exc:
            dest.unlink(missing_ok=True)
            log.warning("download of the demo of %s failed: %s: %s", code, type(exc).__name__, exc)
            err = f"download failed: {type(exc).__name__}: {exc}"[:500]
            self.db.update_share_code(code, status="FAILED", error=err)
            self.db.finish_analysis_job(job["jobId"], "FAILED", error=err)
            return False
        self.db.update_share_code(code, status="ANALYZING", error=None)
        return True

    def _analyze(self, job: dict) -> None:
        from cs2_analyzer import pipeline

        jid, path, user_id = job["jobId"], Path(job["path"]), job["userId"]
        wait_s = (datetime.now(timezone.utc) - job["createdAt"]).total_seconds() if job.get("createdAt") else 0.0
        started = time.monotonic()
        log.info("analysis of %s started (waited %.0f s in the queue)", path.name, wait_s)

        def on_parsed(meta):
            keys = [f"match:{meta.match_id}"] + ([f"sha256:{meta.demo_sha256}"] if meta.demo_sha256 else [])
            if not self.db.lock_analysis_match(jid, keys, self.stale_after):
                raise MatchBusy(meta.match_id)

        try:
            res = pipeline.analyze_demo(path, self.config, db=self.db, keep_demo=job["keepDemo"], force=job["force"],
                                        match_id=job["requestedMatchId"], generate_evidence=job["generateEvidence"],
                                        played_at=job["playedAt"], on_parsed=on_parsed)
        except MatchBusy as exc:
            log.info("%s: match %s is being analyzed by another worker; trying again later", path.name, exc)
            self.db.requeue_analysis_job(jid, None, delay=self.busy_retry)
            return
        except AlreadyProcessedError as exc:
            log.info("%s is already analyzed as %s", path.name, exc.match_id)
            # Supplying the demo of an already analyzed match still grants access to that match.
            if user_id is not None:
                self.db.record_upload(exc.match_id, user_id)
            if not job["keepDemo"]:
                path.unlink(missing_ok=True)
            self.db.finish_analysis_job(jid, "DUPLICATE", match_id=exc.match_id, error=str(exc))
            self._share_code_done(job, "DONE", exc.match_id, None)
            return
        except _Shutdown:
            raise
        except BaseException as exc:  # recorded for the client; details in the match row
            # demoparser2 reports corrupt demos as a Rust panic, which is a BaseException, not an Exception.
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            log.error("analysis of %s failed", path.name, exc_info=exc)
            self._fail(job, f"{type(exc).__name__}: {exc}", f"analysis failed: {type(exc).__name__}",
                       trace=traceback.format_exc()[-2000:])
            return

        mid = res.meta.match_id
        self.db.note_queue_wait(mid, wait_s)
        classes = Counter(a.classification for a in res.assessments.values())
        log.info("analysis of %s done in %.0f s: %s on %s, %d evidence event(s), players %s", path.name,
                 time.monotonic() - started, mid, res.meta.map_name, len(res.events),
                 ", ".join(f"{n} {c}" for c, n in sorted(classes.items())) or "none")
        for w in getattr(res, "warnings", ()):
            log.warning("%s: %s", mid, w)
        if user_id is not None:
            self.db.record_upload(mid, user_id)
        try:  # Steam chat message to opted-in players; never fails the analysis
            if n := self.chat.queue_for_match(mid):
                log.info("%s: %d Steam chat message(s) queued", mid, n)
        except Exception:
            log.warning("could not queue Steam chat messages for %s", mid, exc_info=True)
        self.db.finish_analysis_job(jid, "COMPLETED", match_id=mid, demo_deleted=bool(res.demo_deleted), error=None)
        self._share_code_done(job, "DONE", mid, None)

    def _fail(self, job: dict, error: str, share_error: str, trace: str | None = None) -> None:
        self.db.finish_analysis_job(job["jobId"], "FAILED", error=error[:4000], trace=trace)
        self._share_code_done(job, "FAILED", None, share_error)

    def _share_code_done(self, job: dict, status: str, match_id: str | None, error: str | None) -> None:
        if job.get("shareCode"):
            self.db.update_share_code(job["shareCode"], status=status, match_id=match_id, error=error)


class InProcessWorkers:
    """``api.workers`` analysis threads inside the API process, started when there is something to do."""

    def __init__(self, config: Config, db: Database, count: int, demo_downloader=None):
        self.config, self.db, self.count = config, db, max(0, count)
        self.downloader = demo_downloader
        self.wake = threading.Event()
        self.workers: list[AnalysisWorker] = []
        self._lock = threading.Lock()

    def kick(self) -> None:
        """New work is queued: wake the threads, starting them on first use."""
        if self.count == 0:
            return
        with self._lock:
            if not self.workers:
                prefix = f"{socket.gethostname()}-{os.getpid()}-api"
                for i in range(self.count):
                    w = AnalysisWorker(self.config, self.db, name=f"{prefix}{i + 1}", demo_downloader=self.downloader,
                                       wake=self.wake, register=False)
                    threading.Thread(target=w.run, daemon=True, name=f"analysis-{i + 1}").start()
                    self.workers.append(w)
        self.wake.set()

