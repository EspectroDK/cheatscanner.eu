"""REST API (FastAPI) and website host.

Exposes stored assessments; it does not make verdicts. Uploaded demos go into
the analysis queue in the database (``worker.py``); analysis workers take them
from there and delete them after successful analysis (same retention rules as
the CLI).

With ``auth.enabled`` every data endpoint needs a signed-in user, and what a
user sees follows these rules (also described in the README, section Website):

- a match: if the user played in it or supplied its demo;
- a player's history and risk: only for the user and players they played with
  or against, with the match list limited to those shared matches;
- a player's evidence events, clips and per-match timeline: from all of that
  player's analyzed matches, once the viewer has played with or against them.
  Events and timeline points from matches the viewer can't open carry the map and date, and no link to the match. Other players keep their
  names there; their Steam ID (the link to their page) is only sent when the
  viewer has played with or against them somewhere.
- uploading a demo counts like playing in that match: the uploader sees every
  player in it, with their evidence.

Without auth (local use) everything is visible, as before. If the website has
been built (``web/dist``), it is served at ``/``.
"""

from __future__ import annotations

import logging
import re
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from cs2_analyzer.api import admin, companion
from cs2_analyzer.api.auth import Auth
from cs2_analyzer.api.steam_openid import HttpPost
from cs2_analyzer.config import Config
from cs2_analyzer.ingest.chat import SteamChat
from cs2_analyzer.ingest.service import Ingest
from cs2_analyzer.ingest.steam_history import HttpGet
from cs2_analyzer.storage.repository import Database
from cs2_analyzer.worker import InProcessWorkers

log = logging.getLogger(__name__)

_INSTALLER = re.compile(r"Cheatscanner-Setup-(\d+(?:\.\d+)*)\.exe")


class RiskBatchRequest(BaseModel):
    steamIds: list[str] = Field(..., max_length=500)


class ImportRequest(BaseModel):
    path: str
    force: bool = False
    matchId: str | None = None
    keepDemo: bool = False
    generateEvidence: bool = False


class _UploadLimit:
    """Guards ``POST /matches`` before the form is parsed to disk.

    Refuses bodies over the size limit while they arrive, and asks ``admit(request)`` first, which returns
    ``(status, message)`` to refuse (per-user limits, low disk) or a release callback to run when done.
    """

    def __init__(self, app, limit: int, message: str, admit=None):
        self.app, self.limit, self.message, self.admit = app, limit, message, admit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope["method"] != "POST" or scope["path"] != "/matches":
            return await self.app(scope, receive, send)
        headers = dict(scope.get("headers") or [])
        if int(headers.get(b"content-length", b"0") or 0) > self.limit:
            return await JSONResponse({"detail": self.message}, status_code=413)(scope, receive, send)
        release = None
        if self.admit is not None:
            verdict = self.admit(Request(scope))
            if isinstance(verdict, tuple):
                return await JSONResponse({"detail": verdict[1]}, status_code=verdict[0])(scope, receive, send)
            release = verdict
        try:
            await self._limited(scope, receive, send)
        finally:
            if release:
                release()

    async def _limited(self, scope, receive, send):
        seen = 0

        async def limited():
            nonlocal seen
            msg = await receive()
            if msg["type"] == "http.request":
                seen += len(msg.get("body", b""))
                if seen > self.limit:
                    raise _TooLarge
            return msg

        try:
            await self.app(scope, limited, send)
        except _TooLarge:
            await JSONResponse({"detail": self.message}, status_code=413)(scope, receive, send)


class _TooLarge(Exception):
    pass


def _find_web_dir(configured: str | Path) -> Path | None:
    """The built website: as configured, else relative to the repository (so the working folder doesn't matter)."""
    p = Path(configured)
    candidates = [p] if p.is_absolute() else [Path.cwd() / p, Path(__file__).resolve().parents[3] / p]
    return next((c for c in candidates if (c / "index.html").is_file()), None)


def _sid(s: str) -> int:
    if not s.isdigit() or len(s) > 20:
        raise HTTPException(400, "steamId must be a SteamID64")
    return int(s)


class Viewer:
    """Access scope of one request. ``unrestricted`` for local use without auth."""

    def __init__(self, db: Database, user: dict | None, restricted: bool):
        self.user = user
        self.unrestricted = not restricted
        if self.unrestricted:
            return
        self.steam_id = int(user["steamId"])
        acc = db.viewer_access(self.steam_id, user["id"])
        self.played, self.uploaded, self.co_players = acc["played"], acc["uploaded"], acc["co_players"]

    def can_see_match(self, match_id: str) -> bool:
        return self.unrestricted or match_id in self.played or match_id in self.uploaded

    def can_see_player(self, steam_id: int) -> bool:
        return self.unrestricted or steam_id in self.co_players

    def match_scope(self, db: Database, steam_id: int) -> set[str] | None:
        """Matches whose details about ``steam_id`` this viewer may see (None = all)."""
        if self.unrestricted:
            return None
        if steam_id == self.steam_id:
            return set(self.played)
        uploaded = {m for m, roster in db.match_rosters(self.uploaded).items() if steam_id in roster}
        return db.shared_matches(self.steam_id, steam_id) | uploaded


def create_app(config: Config | None = None, db_url: str | None = None, steam_http_post: HttpPost | None = None,
               profile_fetcher=None, web_dir: str | Path | None = None,
               steam_history_get: HttpGet | None = None, demo_downloader=None) -> FastAPI:
    config = config or Config.load()
    db = Database(db_url or config.get("storage.database_url"))
    db.init_schema()
    work_dir = Path(config.get("output.parquet_dir")) / "uploads"
    work_dir.mkdir(parents=True, exist_ok=True)
    import_dir = config.get("api.import_dir", None)
    # Clips and plots for flagged players (evidence.generate_for_classes), shown on the website.
    generate_evidence = bool(config.get("api.generate_evidence", True))
    max_upload = int(config.get("api.max_upload_mb", 1000)) << 20
    too_large = f"The demo is larger than the {max_upload >> 20} MB upload limit."
    # Analysis threads in this process (0 when separate `cs2-analyzer worker` processes do the work).
    local_workers = InProcessWorkers(config, db, int(config.get("api.workers", 1)), demo_downloader)
    worker_alive = timedelta(seconds=float(config.get("worker.stale_after_s", 120)))
    lock = threading.Lock()
    (work_dir / ".active-jobs").unlink(missing_ok=True)  # the old in-process queue's count for the deploy script

    app = FastAPI(title=f"{config.get('site.name', 'Cheatscanner')} API", version="0.1.0",
                  description="Evidence/risk scores from behavioral anomalies. Not verdicts, not probabilities of cheating.")
    auth = Auth(config, db, http_post=steam_http_post, profile_fetcher=profile_fetcher)
    app.include_router(auth.router())
    max_parallel = int(config.get("api.max_parallel_uploads", 1))
    max_per_day = int(config.get("api.max_uploads_per_day", 10))
    max_queued = int(config.get("api.max_queued_uploads", 3))
    min_free = float(config.get("api.min_free_disk_gb", 20)) * (1 << 30)
    receiving: dict[int, int] = {}      # user id -> uploads being received right now

    def admit_upload(request: Request):
        """Per-user upload limits and the free-disk floor; checked before the body is read."""
        if min_free > 0 and shutil.disk_usage(work_dir).free < min_free:
            return 507, "The server is low on disk space and can't take uploads right now. Try again later."
        user = auth.current_user(request)
        if user is None:  # local use without accounts, or a request the endpoint will refuse anyway
            return None
        uid = user["id"]
        with lock:
            if receiving.get(uid, 0) >= max_parallel:
                return 429, "You already have an upload in progress. Wait for it to finish, then upload the next demo."
            waiting = db.queued_uploads(uid)
            if waiting >= max_queued:
                return 429, f"You have {waiting} uploaded demos waiting for analysis. Upload more once they are done."
            if db.upload_attempts_since(uid, datetime.now(timezone.utc) - timedelta(days=1)) >= max_per_day:
                return 429, f"You have reached the limit of {max_per_day} uploads per 24 hours. Try again later."
            receiving[uid] = receiving.get(uid, 0) + 1
        db.record_upload_attempt(uid)

        def release():
            with lock:
                receiving[uid] -= 1
        return release

    # + 1 MB for the multipart framing around the file
    app.add_middleware(_UploadLimit, limit=max_upload + (1 << 20), message=too_large, admit=admit_upload)

    ingest = Ingest(config, db, http_get=steam_history_get)
    chat = SteamChat(db, auth.public_url)
    if n := db.requeue_interrupted_share_codes():
        log.warning("%d fetched match(es) were interrupted by the last shutdown and are queued again", n)

    def viewer(user: dict | None = Depends(auth.guard)) -> Viewer:
        # Onboarding is mandatory: the Steam authentication code comes first.
        if auth.enabled and ingest.required and user is not None and not ingest.has_access(user["id"]):
            raise HTTPException(403, "onboarding: add your Steam authentication code first")
        return Viewer(db, user, restricted=auth.enabled)

    def fetch_and_analyze(job: dict, url: str):
        """The demo fetcher found a replay URL: queue the download and analysis (demo deleted afterwards)."""
        code = job["shareCode"]
        if db.has_active_fetch_job(code):
            return
        dest = work_dir / f"{uuid.uuid4().hex}_{code[5:].replace('-', '')}.dem"
        db.enqueue_analysis("fetch", dest, user_id=job.get("userId"), share_code=code, demo_url=url,
                            generate_evidence=generate_evidence, played_at=job.get("playedAt"))
        local_workers.kick()

    app.include_router(companion.router(config, db, auth.public_url, auth.require_user, viewer))
    def live_jobs() -> dict:
        """Demos in the analysis queue right now (uploads and fetched matches), and the workers taking them."""
        q = db.analysis_queue(worker_alive)
        with lock:
            receiving_now = sum(receiving.values())
        return {"queued": q["queued"], "processing": q["processing"], "uploads": q["uploads"],
                "oldestWaitingSeconds": q["oldestWaitingSeconds"], "receivingUploads": receiving_now,
                "workers": q["workers"] + local_workers.count}

    app.include_router(admin.router(db, auth, live_jobs, work_dir,
                                    lambda: ingest.fetcher_last_seen,
                                    {"dir": config.get("geometry.maps_dir"),
                                     "render_dir": config.get("evidence.render_maps_dir"),
                                     "patch_tolerance": config.get("geometry.patch_tolerance", 10)}))
    app.include_router(ingest.router(auth.require_user, fetch_and_analyze))
    app.include_router(chat.router(auth.require_user, ingest.require_service))

    def submit(path: Path, force=False, match_id=None, keep=False, gen=False, user_id=None, upload=False) -> dict:
        job = db.enqueue_analysis("upload" if upload else "import", path, user_id=user_id, force=force, keep_demo=keep,
                                  generate_evidence=gen, requested_match_id=match_id)
        local_workers.kick()
        return job

    # Work left in the queue by the last run (if this process analyzes at all).
    if any(db.analysis_queue(worker_alive)[k] for k in ("queued", "processing")):
        local_workers.kick()

    @app.get("/health")
    def health():
        if auth.enabled:  # public endpoint: don't reveal infrastructure details
            return {"status": "ok"}
        return {"status": "ok", "database": db.safe_url(), **db.counts()}

    contact = str(config.get("site.contact_email", "") or "")
    site_name = str(config.get("site.name", "Cheatscanner"))
    domain = str(config.get("site.domain", "cheatscanner.eu"))

    downloads_dir = Path(config.get("site.downloads_dir", "./data/downloads"))

    def companion_installer() -> tuple[Path, str] | None:
        """The newest Cheatscanner-Setup-<version>.exe in downloads_dir, with its version."""
        found = []
        for f in downloads_dir.glob("Cheatscanner-Setup-*.exe") if downloads_dir.is_dir() else []:
            m = _INSTALLER.fullmatch(f.name)
            if m and f.is_file():
                found.append((tuple(int(x) for x in m.group(1).split(".")), f, m.group(1)))
        if not found:
            return None
        _, f, version = max(found)
        return f, version

    @app.get("/site-info")
    def site_info():
        """Public facts the website and companion app show: name, upload limit, where privacy requests go."""
        inst = companion_installer()
        app_download = None if inst is None else {
            "version": inst[1], "url": "/download/companion", "sizeMb": round(inst[0].stat().st_size / (1 << 20), 1)}
        return {"name": site_name, "domain": domain, "publicUrl": auth.public_url, "authEnabled": auth.enabled,
                "maxUploadMb": max_upload >> 20, "maxUploadsPerDay": max_per_day, "contactEmail": contact or None,
                "companionDownload": app_download}

    @app.api_route("/download/companion", methods=["GET", "HEAD"])
    def download_companion():
        """The companion app installer (newest version in site.downloads_dir)."""
        inst = companion_installer()
        if inst is None:
            raise HTTPException(404, "The app is not available for download yet.")
        return FileResponse(inst[0], media_type="application/vnd.microsoft.portable-executable", filename=inst[0].name,
                            headers={"Cache-Control": "no-cache"})

    @app.post("/matches", status_code=202)
    async def post_match(file: UploadFile = File(...), force: bool = False, matchId: str | None = None,
                         generateEvidence: bool | None = None, v: Viewer = Depends(viewer)):
        if not (file.filename or "").lower().endswith(".dem"):
            raise HTTPException(400, "upload a .dem file")
        if not v.unrestricted and (force or matchId):
            raise HTTPException(403, "force and matchId are only available without auth (local use)")
        name = Path(file.filename).name
        dst = work_dir / f"{uuid.uuid4().hex}_{name}"
        written = 0
        with open(dst, "wb") as fh:
            while chunk := await file.read(1 << 20):
                written += len(chunk)
                if written > max_upload:  # the file itself, without the form framing
                    fh.close()
                    dst.unlink(missing_ok=True)
                    raise HTTPException(413, too_large)
                fh.write(chunk)
        gen = generate_evidence if generateEvidence is None else generateEvidence
        return submit(dst, force, matchId, False, gen, user_id=v.user["id"] if v.user else None, upload=True)

    @app.post("/matches/import", status_code=202)
    def import_match(req: ImportRequest, v: Viewer = Depends(viewer)):
        """Queue a demo already on the server's disk (only inside ``api.import_dir``; local use only)."""
        if not import_dir or not v.unrestricted:
            raise HTTPException(403, "path import disabled (set api.import_dir; not available with auth)")
        base = Path(import_dir).resolve()
        p = Path(req.path).resolve()
        if base not in p.parents or not p.is_file() or p.suffix.lower() != ".dem":
            raise HTTPException(400, "path must be a .dem file inside api.import_dir")
        return submit(p, req.force, req.matchId, req.keepDemo, req.generateEvidence)

    @app.get("/jobs/{job_id}")
    def get_job(job_id: str, v: Viewer = Depends(viewer)):
        job = db.get_analysis_job(job_id) if len(job_id) <= 64 else None
        if not job or (not v.unrestricted and job["userId"] != v.user["id"]):
            raise HTTPException(404, "unknown job")
        return {k: job[k] for k in ("jobId", "status", "file", "matchId", "demoDeleted", "error") if k in job}

    @app.get("/me/matches")
    def my_matches(v: Viewer = Depends(viewer)):
        """Matches the signed-in user played in or supplied, newest first."""
        if v.user is None:
            raise HTTPException(401, "sign in with Steam")
        if v.unrestricted:
            acc = db.viewer_access(int(v.user["steamId"]), v.user["id"])
            ids = acc["played"] | acc["uploaded"]
        else:
            ids = v.played | v.uploaded
        matches = db.matches_overview(ids)
        for m in matches:
            for p in m["players"]:
                if not v.can_see_player(int(p["steamId"])):
                    p["classification"] = None
        return matches

    @app.get("/matches/{match_id}")
    def get_match(match_id: str, v: Viewer = Depends(viewer)):
        m = db.get_match(match_id) if v.can_see_match(match_id) else None
        if m is None:
            raise HTTPException(404, "match not found")
        for p in m["players"]:
            p["visible"] = v.can_see_player(int(p["steamId"]))
            if not p["visible"]:
                p["assessment"] = None
        return m

    @app.get("/matches/{match_id}/evidence")
    def get_match_evidence(match_id: str, v: Viewer = Depends(viewer)):
        if not v.can_see_match(match_id):
            raise HTTPException(404, "match not found")
        return _for_viewer([e for e in db.match_evidence(match_id) if _may_see_event(e, v)], v)

    def _may_see_event(e: dict, v: Viewer) -> bool:
        """Same rule as the player evidence lists: any event of a player the viewer has played with or against."""
        return v.can_see_player(int(e["steamId"]))

    def _for_viewer(events: list[dict], v: Viewer) -> list[dict]:
        """Events from matches the viewer can't open: the other player is only linkable if the viewer knows them."""
        rosters = db.match_rosters({e["matchId"] for e in events if e.get("targetSteamId")})
        out = []
        for e in events:
            visible = v.can_see_match(e["matchId"])
            target = e.get("targetSteamId")
            name = rosters.get(e["matchId"], {}).get(int(target), (None, None))[0] if target else None
            e = e | {"targetName": name, "matchVisible": visible}
            if not visible:
                e = e | {"context": None}
                if target and not v.can_see_player(int(target)):
                    e["targetSteamId"] = None
            out.append(e)
        return out

    output_root = Path(config.get("output.dir", "./output")).resolve()

    def _evidence_file(event_id: str, v: Viewer, key: str, media_type: str):
        e = db.get_evidence_event(event_id)
        if e is None or not _may_see_event(e, v):
            raise HTTPException(404, "evidence not found")
        path = Path(e[key]).resolve() if e[key] else None
        if path is None or output_root not in path.parents or not path.is_file():
            raise HTTPException(404, "no file for this event")
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/evidence/{event_id}/clip")
    def get_evidence_clip(event_id: str, v: Viewer = Depends(viewer)):
        return _evidence_file(event_id, v, "_videoPath", "video/mp4")

    @app.get("/evidence/{event_id}/poster")
    def get_evidence_poster(event_id: str, v: Viewer = Depends(viewer)):
        return _evidence_file(event_id, v, "_posterPath", "image/jpeg")

    @app.get("/evidence/{event_id}/plot")
    def get_evidence_plot(event_id: str, v: Viewer = Depends(viewer)):
        return _evidence_file(event_id, v, "_plotPath", "image/png")

    def _player_or_404(steam_id: str, v: Viewer) -> int:
        sid = _sid(steam_id)
        if not v.can_see_player(sid):
            raise HTTPException(404, "player not found")
        return sid

    @app.get("/players/{steam_id}")
    def get_player(steam_id: str, v: Viewer = Depends(viewer)):
        p = db.get_player(_player_or_404(steam_id, v))
        if p is None:
            raise HTTPException(404, "player not found")
        return p

    @app.get("/players/{steam_id}/matches")
    def get_player_matches(steam_id: str, v: Viewer = Depends(viewer)):
        sid = _player_or_404(steam_id, v)
        return db.player_matches(sid, only_match_ids=v.match_scope(db, sid))

    @app.get("/players/{steam_id}/evidence")
    def get_player_evidence(steam_id: str, limit: int = 100, v: Viewer = Depends(viewer)):
        sid = _player_or_404(steam_id, v)
        return _for_viewer(db.player_evidence(sid, limit=min(max(limit, 1), 1000)), v)

    @app.get("/players/{steam_id}/timeline")
    def get_player_timeline(steam_id: str, v: Viewer = Depends(viewer)):
        """The player's class and evidence per analyzed match, oldest first (all matches, like evidence)."""
        sid = _player_or_404(steam_id, v)
        rows = db.player_timeline(sid)
        for r in rows:
            r["matchVisible"] = v.can_see_match(r["matchId"])
            if not r["matchVisible"]:
                r["matchId"] = None
        return rows

    @app.get("/players/{steam_id}/pattern")
    def get_player_pattern(steam_id: str, v: Viewer = Depends(viewer)):
        """The numbers behind the play-pattern score: per match the pattern's standing among clean players,
        and per measurement the player's typical value next to the clean reference (all matches, like evidence)."""
        from cs2_analyzer.scoring import player_evidence as pe

        sid = _player_or_404(steam_id, v)
        rows = db.player_pattern_matches(sid)
        model = pe.load_model(config.get("player_evidence.model", None) or None)
        features = []
        if model is not None and rows:
            obs = pe.load_observations(config.get("output.observations_dir", "./data/observations"),
                                       [r["matchId"] for r in rows], sid)
            features = pe.breakdown(obs, model)
        for r in rows:
            r["matchVisible"] = v.can_see_match(r["matchId"])
            if not r["matchVisible"]:
                r["matchId"] = None
        return {"matches": rows, "features": features,
                "reference": None if model is None else {"source": model.get("source"),
                                                         "cleanPlayers": model.get("clean_players"),
                                                         "cleanMatches": model.get("clean_matches")}}

    def _risk(steam_id: str, v: Viewer) -> dict:
        sid = _sid(steam_id)
        if not v.can_see_player(sid):
            return {"steamId": steam_id, "classification": None, "visible": False,
                    "note": "only players you have played with or against are shown"}
        r = db.risk(sid)
        if r is None:
            return {"steamId": steam_id, "classification": "INSUFFICIENT_DATA", "evidenceScore": None,
                    "matchesAnalyzed": 0, "highSeverityMatches": 0, "axes": {}}
        return r

    @app.get("/risk/{steam_id}")
    def get_risk(steam_id: str, v: Viewer = Depends(viewer)):
        r = _risk(steam_id, v)
        if r.get("visible") is False:
            raise HTTPException(404, r["note"])
        return r

    @app.post("/risk/batch")
    def risk_batch(req: RiskBatchRequest, v: Viewer = Depends(viewer)):
        return {"results": [_risk(s, v) for s in req.steamIds]}

    web = _find_web_dir(web_dir or config.get("api.web_dir", "web/dist"))
    if web is not None:
        app.mount("/", StaticFiles(directory=web, html=True), name="web")
    else:
        log.warning("website not built: run `npm install && npm run build` in web/ (looked for web/dist/index.html)")

        @app.get("/", include_in_schema=False)
        def no_website():
            return {"detail": "The website has not been built. Run `npm install` and `npm run build` in the "
                              "repository's web folder, then restart the server. The API itself works: see /docs."}

    return app
