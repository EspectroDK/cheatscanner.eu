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

Evidence clips are a job of their own (kind ``clips``): an analysis saves the match's results at once and
queues the clips (``worker.defer_clips``), so the match page shows the results while the clips, which take
most of the time, are still being rendered. Each worker has a role (``worker.role``, env
``CS2A_WORKER_ROLE``, or ``cs2-analyzer worker --role``):

- ``analysis`` (default): analyzes demos first. With no demo waiting it renders clips, and steps aside
  between two clips as soon as a demo arrives (the clips job goes back to the queue, its finished clips kept);
- ``clips``: renders clips first, and analyzes demos while no clips are waiting.

So no core sits idle, and results never wait behind a clips job on an analysis worker.

While the pause file (``worker.pause_file``, default ``<output.parquet_dir>/.pause-analysis``) exists,
workers finish the demo they have but claim no new one (a clips job hands itself back after the clip it is
drawing): deploy/server-deploy.sh creates it before waiting for running jobs and removes it once the new
workers run. A pause file older than
``worker.pause_max_s`` (default 30 minutes; a deploy that broke off) is ignored.
"""

from __future__ import annotations

import logging
import os
import shutil
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
from cs2_analyzer.storage.repository import ANALYSIS_KINDS, CLIP_KINDS, AlreadyProcessedError, Database

log = logging.getLogger(__name__)


class MatchBusy(Exception):
    """Another worker is analyzing the same match right now."""


class _Shutdown(BaseException):
    """SIGTERM while a demo is being downloaded or analyzed (not an Exception: nothing may swallow it)."""


ROLES = ("analysis", "clips")


def pause_file(config: Config) -> Path:
    return Path(config.get("worker.pause_file", None) or Path(config.get("output.parquet_dir", "./data/work")) / ".pause-analysis")


class AnalysisWorker:
    def __init__(self, config: Config, db: Database, name: str | None = None, demo_downloader=None,
                 wake: threading.Event | None = None, register: bool = True, role: str | None = None):
        self.config, self.db = config, db
        self.role = role or str(config.get("worker.role", "analysis") or "analysis")
        if self.role not in ROLES:
            raise ValueError(f"worker role must be one of {', '.join(ROLES)}, not {self.role!r}")
        # The job kinds this worker takes, its own first.
        self.kinds = [CLIP_KINDS, ANALYSIS_KINDS] if self.role == "clips" else [ANALYSIS_KINDS, CLIP_KINDS]
        self.defer_clips = bool(config.get("worker.defer_clips", True))
        # Below this much free disk, clips are rendered inside the analysis again (no demos kept waiting).
        self.defer_min_free = float(config.get("worker.defer_min_free_gb", 30)) * 2**30
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
        self.pause_file = pause_file(config)
        self.pause_max_s = float(config.get("worker.pause_max_s", 1800))
        self._interruptible = False

    # ------------------------------------------------------------------ loop

    def run(self) -> None:
        """Work until ``stop`` is set: claim, analyze, repeat; wait ``poll_s`` (or for ``wake``) when idle."""
        log.info("analysis worker %s started (role: %s)", self.name, self.role)
        last_seen = 0.0
        was_paused = False
        while not self.stop.is_set():
            paused = self.paused()
            if paused != was_paused:
                log.info("analysis worker %s: %s", self.name,
                         "paused, taking no new demos" if paused else "taking new demos again")
                was_paused = paused
            try:
                if self.register and time.monotonic() - last_seen >= self.heartbeat_s:
                    self.db.worker_seen(self.name, None, self.role)
                    last_seen = time.monotonic()
                job = None if paused else self.claim()
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

    def claim(self) -> dict | None:
        """The oldest job of this worker's own kind, else the oldest of the other kind."""
        for kinds in self.kinds:
            job = self.db.claim_analysis_job(self.name, self.stale_after, kinds)
            if job is not None:
                return job
        return None

    def paused(self) -> bool:
        """The pause file exists and is recent (a deploy is waiting for the running analyses)."""
        try:
            return time.time() - self.pause_file.stat().st_mtime < self.pause_max_s
        except OSError:
            return False

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
                msg = (f"{'clip rendering' if job['kind'] in CLIP_KINDS else 'analysis'} stopped "
                       f"{job['attempts'] - 1} times without finishing (the worker was stopped or ran out of memory)")
                log.error("job %s (%s): %s; giving up", jid, job["file"], msg)
                if job["kind"] in CLIP_KINDS:
                    self._clips_failed(job, msg)
                else:
                    self._fail(job, msg, "analysis failed: worker stopped repeatedly")
                return
            if job.get("takenOver"):
                log.warning("job %s (%s): its worker stopped sending heartbeats; analyzing it again", jid, job["file"])
            try:
                with self.interruptible():
                    if job["kind"] in CLIP_KINDS:
                        self._clips(job)
                        return
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
                                        played_at=job["playedAt"], on_parsed=on_parsed,
                                        filter_modes=True, defer_clips=self.should_defer(path))
        except MatchBusy as exc:
            log.info("%s: match %s is being analyzed by another worker; trying again later", path.name, exc)
            self.db.requeue_analysis_job(jid, None, delay=self.busy_retry)
            return
        except AlreadyProcessedError as exc:
            log.info("%s is already analyzed as %s", path.name, exc.match_id)
            # Supplying the demo of an already analyzed match still grants access to that match.
            if user_id is not None:
                self.db.record_upload(exc.match_id, user_id)
            if (self.db.clip_plan(exc.match_id) or {}).get("pending") and not self.db.has_active_clips_job(exc.match_id):
                # Analyzed, but its clips job never got queued (the worker stopped right then): this demo serves.
                self.db.enqueue_analysis("clips", path, keep_demo=job["keepDemo"], match_id=exc.match_id)
            elif not job["keepDemo"]:
                path.unlink(missing_ok=True)
            self.db.finish_analysis_job(jid, "DUPLICATE", match_id=exc.match_id, error=str(exc))
            self._share_code_done(job, "DONE", exc.match_id, None)
            return
        except pipeline.SkippedMatch as exc:
            log.info("%s not analyzed: %s", path.name, exc)
            self.db.finish_analysis_job(jid, "SKIPPED", error=str(exc))
            self._share_code_done(job, "SKIPPED", None, str(exc))
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
        pending = list(getattr(res, "pending_clips", None) or [])
        log.info("analysis of %s done in %.0f s: %s on %s, %d evidence event(s), players %s%s", path.name,
                 time.monotonic() - started, mid, res.meta.map_name, len(res.events),
                 ", ".join(f"{n} {c}" for c, n in sorted(classes.items())) or "none",
                 f"; {len(pending)} clip(s) queued" if pending else "")
        if pending:  # the clips come later, from the demo kept for them
            self.db.enqueue_analysis("clips", path, keep_demo=job["keepDemo"], match_id=mid)
            self.wake.set()
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

    def should_defer(self, path: Path) -> bool:
        """Leave the clips to a clips job (keeping the demo until then), unless switched off or disk runs low."""
        if not self.defer_clips:
            return False
        try:
            free = shutil.disk_usage(path.parent).free
        except OSError:
            return True
        if free < self.defer_min_free:
            log.info("%s: only %.0f GB disk free, so its clips are rendered now (no demo kept for later)",
                     path.name, free / 2**30)
            return False
        return True

    def _clips(self, job: dict) -> None:
        """Render the evidence clips an analysis left for later, then let the demo go."""
        from cs2_analyzer import pipeline

        jid, path, mid = job["jobId"], Path(job["path"]), job.get("matchId")
        if not self.db.lock_analysis_match(jid, [f"clips:{mid}"], self.stale_after):
            self.db.requeue_analysis_job(jid, None, delay=self.busy_retry)
            return
        plan = self.db.clip_plan(mid)
        if plan is None:  # the match was deleted meanwhile
            self._clips_done(job, "SKIPPED", "the match no longer exists")
            return
        if not plan.get("pending"):
            self._clips_done(job, "COMPLETED", None)
            return
        if not path.is_file():
            self._clips_failed(job, "the demo is no longer there")
            return
        own_kind = self.kinds[0]

        def should_stop() -> bool:
            """Step aside between two clips: shutting down, a deploy pausing, or a demo waiting for this
            (analysis) worker. The finished clips are kept; the next worker carries on with the rest."""
            if self.stop.is_set() or self.paused():
                return True
            try:
                return own_kind is not CLIP_KINDS and self.db.jobs_waiting(own_kind)
            except Exception:
                return False

        started = time.monotonic()
        self.db.update_clip_plan(mid, state="RENDERING")
        log.info("clips of %s started (%d to render)", mid, len(plan["pending"]))
        try:
            n = pipeline.render_clips(path, mid, self.config, self.db, should_stop=should_stop)
        except pipeline.ClipsInterrupted:
            left = len((self.db.clip_plan(mid) or {}).get("pending") or [])
            log.info("clips of %s: stepping aside with %d clip(s) left; back in the queue", mid, left)
            self.db.update_clip_plan(mid, state="QUEUED")
            self.db.requeue_analysis_job(jid, None)
            return
        except _Shutdown:
            self.db.update_clip_plan(mid, state="QUEUED")
            raise
        except BaseException as exc:
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            log.error("clips of %s failed", mid, exc_info=exc)
            self._clips_failed(job, f"{type(exc).__name__}: {exc}", trace=traceback.format_exc()[-2000:])
            return
        log.info("clips of %s done in %.0f s: %d clip(s)", mid, time.monotonic() - started, n)
        self._clips_done(job, "COMPLETED", None)

    def _clips_done(self, job: dict, status: str, error: str | None, trace: str | None = None) -> None:
        mid, path = job.get("matchId"), Path(job["path"])
        deleted = False
        if not job["keepDemo"]:
            path.unlink(missing_ok=True)
            deleted = True
        if mid:
            self.db.update_clip_plan(mid, state="FAILED" if status == "FAILED" else "DONE",
                                     demo_deleted=True if deleted else None)
        self.db.finish_analysis_job(job["jobId"], status, demo_deleted=deleted, error=error, trace=trace)

    def _clips_failed(self, job: dict, error: str, trace: str | None = None) -> None:
        self._clips_done(job, "FAILED", error[:4000], trace)

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

