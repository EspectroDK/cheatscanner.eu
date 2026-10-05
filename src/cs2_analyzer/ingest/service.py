"""Automatic demo fetching (README: Automatic demo fetching).

Flow per user:

1. Onboarding stores the user's game authentication code (encrypted) and their
   latest share code, after Steam has confirmed both (``register``).
2. ``poll_once`` asks Steam for each user's newer share codes and queues them.
3. The demo fetcher (``services/demo-fetcher``, a separate Steam bot account)
   claims queued codes, asks Valve's Game Coordinator for the replay URL and
   reports it back (``/internal/sharecodes/...``).
4. The API downloads the ``.dem.bz2`` from Valve's replay server, unpacks it
   and runs the normal analysis; the raw demo is deleted afterwards.
"""

from __future__ import annotations

import bz2
import http.client
import logging
import re
import secrets
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from cs2_analyzer.ingest import sharecode, steam_history
from cs2_analyzer.ingest.secrets import SecretBox
from cs2_analyzer.storage.repository import Database

log = logging.getLogger(__name__)

# Only Valve's replay servers: the fetcher is trusted, but a URL from it must never make us fetch arbitrary hosts.
# The host is what matters; the exact host and file name forms vary (e.g. no number after "replay", a port,
# a different file name), so those are checked loosely.
REPLAY_HOST = re.compile(r"^replay[a-z0-9-]{0,20}\.(valve\.net|wmsj\.cn)$")
REPLAY_PATH = re.compile(r"^/730/[A-Za-z0-9_.-]{1,120}\.dem\.bz2$")


def is_replay_url(url: str) -> bool:
    """True for a ``.dem.bz2`` on one of Valve's (or Perfect World's) replay servers."""
    try:
        u = urlsplit(url.strip())
        port = u.port
    except ValueError:
        return False
    return (u.scheme.lower() in ("http", "https") and not u.username and not u.password
            and port in (None, 80, 443) and not u.query and not u.fragment
            and bool(REPLAY_HOST.match((u.hostname or "").lower())) and bool(REPLAY_PATH.match(u.path)))

MAX_ATTEMPTS = 3


class AccessRequest(BaseModel):
    authCode: str = Field(..., max_length=32)
    knownCode: str = Field(..., max_length=40)


class FetchResult(BaseModel):
    demoUrl: str | None = Field(None, max_length=512)
    matchTime: int | None = Field(None, ge=0)   # when the match was played (Unix seconds, from the Game Coordinator)
    error: str | None = Field(None, max_length=500)
    expired: bool = False


def download_demo(url: str, dest: Path, max_compressed: int, max_demo: int, timeout: float = 60.0) -> Path:
    """Download a ``.dem.bz2`` from Valve and unpack it to ``dest``, with size limits."""
    if not is_replay_url(url):
        raise ValueError("not a Valve replay URL")
    url = url.strip()
    raw = dest.with_suffix(".dem.bz2")
    with urllib.request.urlopen(url, timeout=timeout) as resp, open(raw, "wb") as out:  # noqa: S310 - checked above
        got = 0
        while chunk := resp.read(1 << 20):
            got += len(chunk)
            if got > max_compressed:
                raise ValueError("demo download is larger than allowed")
            out.write(chunk)
    try:
        dec = bz2.BZ2Decompressor()
        written = 0
        with open(raw, "rb") as src, open(dest, "wb") as out:
            while chunk := src.read(1 << 20):
                data = dec.decompress(chunk)
                written += len(data)
                if written > max_demo:
                    raise ValueError("unpacked demo is larger than allowed")
                out.write(data)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    finally:
        raw.unlink(missing_ok=True)
    return dest


def is_transient(exc: BaseException) -> bool:
    """Network trouble worth another try: a reset or dropped connection, a timeout, a 5xx or 429 from Valve."""
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code >= 500 or exc.code == 429
    return isinstance(exc, (ConnectionError, TimeoutError, http.client.HTTPException, urllib.error.URLError))


def download_with_retries(download: Callable[..., Path], url: str, dest: Path, max_compressed: int, max_demo: int,
                          delays: list[float], sleep: Callable[[float], None] = time.sleep,
                          on_retry: Callable[[str], None] | None = None) -> Path:
    """``download`` with a wait-and-retry after each transient failure (one retry per entry in ``delays``)."""
    for attempt, delay in enumerate([*delays, None]):
        try:
            return download(url, dest, max_compressed, max_demo)
        except Exception as exc:
            dest.unlink(missing_ok=True)
            if delay is None or not is_transient(exc):
                raise
            log.warning("demo download failed (%s: %s); retry %d of %d in %gs",
                        type(exc).__name__, exc, attempt + 1, len(delays), delay)
            if on_retry:
                on_retry(f"download interrupted ({type(exc).__name__}); retry {attempt + 1} of {len(delays)}")
            sleep(delay)
    raise AssertionError("unreachable")


class Ingest:
    def __init__(self, config, db: Database, box: SecretBox | None = None,
                 http_get: steam_history.HttpGet | None = None):
        self.db = db
        self.box = box or SecretBox.from_config(config)
        self.http_get = http_get
        self.api_key = str(config.get("auth.steam_api_key", "") or "")
        self.required = bool(config.get("ingest.require_match_access", True))
        self.service_token = str(config.get("ingest.service_token", "") or "")
        self.max_per_poll = int(config.get("ingest.max_codes_per_poll", 30))
        self.max_compressed = int(config.get("ingest.max_download_mb", 400)) << 20
        self.max_demo = int(config.get("ingest.max_demo_mb", 1500)) << 20
        self.download_retry_delays = [float(d) for d in config.get("ingest.download_retry_delays_s", [10, 60, 300])]
        self.fetcher_last_seen: datetime | None = None   # last claim call from the demo fetcher (admin page)

    # ------------------------------------------------------------ core logic

    def status(self, user_id: int) -> dict:
        a = self.db.get_match_access(user_id)
        if a is None or a.status == "REMOVED":
            return {"status": "MISSING", "required": self.required}
        return {"status": a.status, "required": self.required, "lastShareCode": a.last_share_code,
                "lastError": a.last_error, "lastCheckedAt": a.last_checked_at.isoformat() if a.last_checked_at else None}

    def has_access(self, user_id: int) -> bool:
        a = self.db.get_match_access(user_id)
        return a is not None and a.status == "ACTIVE"

    def _queue(self, code: str, user_id: int) -> bool:
        d = sharecode.decode(code)
        return self.db.queue_share_code(code, user_id, d.match_id, d.reservation_id, d.tv_port)

    def register(self, user: dict, auth_code: str, known_code: str) -> dict:
        """Check the codes with Steam, then store them and queue the known match."""
        auth_code = auth_code.strip().upper()
        known_code = known_code.strip()
        if not steam_history.is_auth_code(auth_code):
            raise HTTPException(400, "The authentication code should look like AAAA-AAAAA-AAAA.")
        if not sharecode.is_share_code(known_code):
            raise HTTPException(400, "The match share code should look like CSGO-xxxxx-xxxxx-xxxxx-xxxxx-xxxxx.")
        try:
            steam_history.next_share_code(self.api_key, int(user["steamId"]), auth_code, known_code, self.http_get)
        except steam_history.AuthCodeRejected:
            raise HTTPException(400, "Steam says this authentication code doesn't belong to your account.") from None
        except steam_history.KnownCodeRejected:
            raise HTTPException(400, "Steam says this match share code isn't one of your matches.") from None
        except steam_history.SteamUnavailable as exc:
            raise HTTPException(503, f"Couldn't check the codes with Steam right now: {exc}") from None
        self.db.set_match_access(user["id"], self.box.encrypt(auth_code), known_code)
        self._queue(known_code, user["id"])
        return self.status(user["id"])

    def remove(self, user_id: int) -> None:
        """Forget the authentication code. Matches already analyzed stay."""
        self.db.set_match_access(user_id, None, None, status="REMOVED")

    def poll_once(self) -> dict:
        """Ask Steam for every active user's new share codes and queue them."""
        summary = {"users": 0, "queued": 0, "errors": 0}
        for access, steam_id in self.db.active_match_access():
            summary["users"] += 1
            auth = self.box.decrypt(access.auth_code_enc or "")
            if not auth:
                self.db.set_match_access(access.user_id, None, access.last_share_code, "REJECTED",
                                         "Stored code can't be read (server key changed). Enter it again.")
                summary["errors"] += 1
                continue
            known, status, error = access.last_share_code, "ACTIVE", None
            try:
                for _ in range(self.max_per_poll):
                    nxt = steam_history.next_share_code(self.api_key, steam_id, auth, known, self.http_get)
                    if nxt is None:
                        break
                    if sharecode.is_share_code(nxt) and self._queue(nxt, access.user_id):
                        summary["queued"] += 1
                    known = nxt
            except (steam_history.AuthCodeRejected, steam_history.KnownCodeRejected) as exc:
                status, error = "REJECTED", f"{exc}. Enter a new authentication code on the website."
                summary["errors"] += 1
            except steam_history.SteamUnavailable as exc:
                error = str(exc)  # temporary: keep ACTIVE and retry next poll
                summary["errors"] += 1
            self.db.set_match_access(access.user_id, access.auth_code_enc if status == "ACTIVE" else None,
                                     known, status, error)
        return summary

    # ---------------------------------------------------------------- routes

    def require_service(self, request: Request) -> None:
        """Dependency for ``/internal/...``: the demo fetcher's shared service token."""
        if not self.service_token:
            raise HTTPException(503, "demo fetcher not configured (set CS2A_SERVICE_TOKEN)")
        got = request.headers.get("authorization", "")[7:].strip()
        if not secrets.compare_digest(got.encode(), self.service_token.encode()):
            raise HTTPException(401, "bad service token")

    def router(self, require_user: Callable, on_demo_url: Callable[[dict, str], None]) -> APIRouter:
        """``on_demo_url(job, url)`` is called when the fetcher has found a demo; it downloads and analyzes."""
        r = APIRouter(tags=["ingest"])
        service = self.require_service

        @r.get("/me/steam-match-access")
        def get_access(user: dict = Depends(require_user)):
            return self.status(user["id"])

        @r.put("/me/steam-match-access")
        def put_access(req: AccessRequest, user: dict = Depends(require_user)):
            return self.register(user, req.authCode, req.knownCode)

        @r.delete("/me/steam-match-access", status_code=204)
        def delete_access(user: dict = Depends(require_user)):
            self.remove(user["id"])

        @r.get("/me/sharecodes")
        def my_codes(user: dict = Depends(require_user)):
            return self.db.share_code_jobs(user["id"])

        @r.post("/me/sharecodes/{code}/retry")
        def retry(code: str, user: dict = Depends(require_user)):
            """Try a failed match again: download again when Valve's URL is known, else ask the fetcher again."""
            job = self.db.retry_share_code(code, user["id"])
            if job is None:
                raise HTTPException(409, "Only your failed matches can be retried.")
            if job["status"] == "DOWNLOADING":
                on_demo_url(job, job["demoUrl"])
            return job

        @r.post("/internal/sharecodes/claim", dependencies=[Depends(service)])
        def claim():
            self.fetcher_last_seen = datetime.now(timezone.utc)
            job = self.db.claim_share_code()
            return job if job else Response(status_code=204)

        @r.post("/internal/sharecodes/{code}/result", dependencies=[Depends(service)])
        def result(code: str, res: FetchResult):
            if res.demoUrl:
                if not is_replay_url(res.demoUrl):
                    # Keep the URL in the error so a new Valve URL form can be recognised and allowed.
                    log.warning("share code %s: fetcher returned a non-Valve URL: %r", code, res.demoUrl)
                    job = self.db.update_share_code(code, status="FAILED",
                                                    error=f"fetcher returned a non-Valve URL: {res.demoUrl[:300]}")
                    raise HTTPException(400, "not a Valve replay URL") if job else HTTPException(404, "unknown code")
                res.demoUrl = res.demoUrl.strip()
                job = self.db.update_share_code(code, status="DOWNLOADING", demo_url=res.demoUrl, error=None)
                if job is None:
                    raise HTTPException(404, "unknown share code")
                if res.matchTime:
                    job["playedAt"] = datetime.fromtimestamp(res.matchTime, timezone.utc)
                on_demo_url(job, res.demoUrl)
                return job
            if res.expired:
                job = self.db.update_share_code(code, status="EXPIRED", error=res.error or "demo no longer available")
            else:
                job = self.db.update_share_code(code, status="FAILED", error=res.error or "fetch failed")
                if job and job["attempts"] < MAX_ATTEMPTS:
                    job = self.db.update_share_code(code, status="QUEUED")
            if job is None:
                raise HTTPException(404, "unknown share code")
            return job

        return r
