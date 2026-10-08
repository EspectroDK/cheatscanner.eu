"""Companion app endpoints: linking the app to an account, and the live lobby lookup.

Linking works like signing in a TV app:

1. the app calls ``POST /companion/pair`` and shows the short code it gets back;
2. the user opens ``<public url>/#/link?code=...`` in the browser, signs in with Steam and confirms
   that the code matches (``POST /companion/pair/confirm``, browser session only);
3. the app polls ``POST /companion/pair/token`` with its secret device code and receives an API token
   once. The token is listed (and revocable) under Settings > API tokens on the website.

``POST /lobby/risk`` answers the overlay: for each Steam ID in the current match, the global evidence
class and the number of analyzed matches, from all users' matches. ``VERY_HIGH`` is shown as
``HIGH``. For ELEVATED and HIGH players it adds the overlay's extended card (F7):
history score (0-100), high-evidence match count, a LOW/MEDIUM/HIGH level for wall tracking, aim and
reaction, and the latest flagged matches as map + score + date. Never events, clips or match ids.
Players with a VAC or game ban on record at Steam get ``bans`` (ingest/steam_bans.py), a separate layer that
never changes the class or raises an alert.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from cs2_analyzer.config import Config
from cs2_analyzer.storage.repository import Database
from cs2_analyzer.storage.stats import record_lobby_lookup

DISCLAIMER = "Evidence classes describe unusual behavior in analyzed matches. Not a verdict and not a probability of cheating."
LOBBY_CLASSES = {"NORMAL", "ELEVATED", "HIGH", "INSUFFICIENT_DATA"}


class PairRequest(BaseModel):
    deviceName: str = Field("Windows PC", min_length=1, max_length=48)


class ConfirmRequest(BaseModel):
    userCode: str = Field(..., min_length=8, max_length=16)


class TokenRequest(BaseModel):
    deviceCode: str = Field(..., min_length=20, max_length=128)


class LobbyPlayer(BaseModel):
    steamId: str | None = Field(None, max_length=20)
    name: str | None = Field(None, max_length=128)


class LobbyRequest(BaseModel):
    # 10 players plus coaches/spectators in the roster.
    players: list[LobbyPlayer] = Field(..., max_length=16)


class RateLimiter:
    """At most ``limit`` calls per ``window_s`` seconds per key (in memory, per server process)."""

    def __init__(self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic):
        self.limit, self.window, self.clock = limit, window_s, clock
        self.calls: dict[object, deque] = {}
        self.lock = threading.Lock()

    def allow(self, key) -> bool:
        if self.limit <= 0:
            return True
        now = self.clock()
        with self.lock:
            q = self.calls.setdefault(key, deque())
            while q and q[0] <= now - self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


def lobby_class(value: str | None) -> str:
    if value == "VERY_HIGH":
        return "HIGH"
    return value if value in LOBBY_CLASSES else "INSUFFICIENT_DATA"


def axis_level(score: float, thresholds: list[float]) -> str:
    """LOW / MEDIUM / HIGH on the same cut-offs as the classes (below ELEVATED, ELEVATED, HIGH and up)."""
    if score >= thresholds[1]:
        return "HIGH"
    return "MEDIUM" if score >= thresholds[0] else "LOW"


def detail_card(d: dict, thresholds: list[float]) -> dict:
    pct = lambda x: int(round(max(0.0, min(1.0, x or 0.0)) * 100))  # noqa: E731
    return {"evidenceScore": pct(d["evidenceScore"]), "highEvidenceMatches": d["highEvidenceMatches"],
            "axes": {k: axis_level(v or 0.0, thresholds) for k, v in d["axes"].items()},
            "recent": [{"map": r["map"], "evidenceScore": pct(r["evidenceScore"]), "playedAt": r["playedAt"]}
                       for r in d["recent"]]}


def router(config: Config, db: Database, public_url: str, require_user, viewer, bans=None) -> APIRouter:
    r = APIRouter(tags=["companion"])
    ttl = float(config.get("companion.pairing_ttl_s", 900))
    poll_interval = float(config.get("companion.poll_interval_s", 3))
    pair_limit = RateLimiter(int(config.get("companion.pairings_per_ip_per_hour", 20)), 3600)
    confirm_limit = RateLimiter(int(config.get("companion.confirms_per_user_per_hour", 20)), 3600)
    lobby_limit = RateLimiter(int(config.get("companion.lobby_lookups_per_minute", 30)), 60)
    thresholds = [float(x) for x in config.get("history.classification.thresholds", [0.25, 0.5, 0.75])]

    def client_ip(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    @r.post("/companion/pair", status_code=201)
    def start_pairing(req: PairRequest, request: Request):
        if not pair_limit.allow(client_ip(request)):
            raise HTTPException(429, "Too many link attempts from this network. Try again later.")
        device_code, user_code, expires = db.create_pairing(req.deviceName.strip() or "Windows PC", ttl)
        return {"deviceCode": device_code, "userCode": user_code, "expiresAt": expires.isoformat(),
                "expiresIn": int(ttl), "interval": poll_interval,
                "verifyUrl": f"{public_url}/#/link?code={user_code}"}

    @r.post("/companion/pair/confirm")
    def confirm_pairing(req: ConfirmRequest, user: dict = Depends(require_user)):
        # Only from a signed-in browser: an app token must not be able to link more apps.
        if user["tokenKind"] != "session":
            raise HTTPException(403, "confirm the code on the website while signed in")
        if not confirm_limit.allow(user["id"]):
            raise HTTPException(429, "Too many attempts. Try again later.")
        res = db.confirm_pairing(req.userCode, user["id"])
        if res is None:
            raise HTTPException(404, "That code is unknown or has expired. Start again in the app.")
        return res

    @r.post("/companion/pair/token")
    def claim_token(req: TokenRequest):
        status, token, user = db.claim_pairing(req.deviceCode)
        if status == "PENDING":
            return JSONResponse({"status": "PENDING"}, status_code=202)
        if status == "EXPIRED":
            return JSONResponse({"status": "EXPIRED", "detail": "The link code expired. Start again."}, status_code=410)
        return {"status": "LINKED", "token": token,
                "user": {k: user[k] for k in ("steamId", "personaName", "avatarUrl")}}

    @r.post("/lobby/risk")
    def lobby_risk(req: LobbyRequest, v=Depends(viewer)):
        """Class and analyzed-match count for everyone in the current match (global knowledge)."""
        if v.user is not None and not lobby_limit.allow(v.user["id"]):
            raise HTTPException(429, "Too many lobby lookups. Wait a moment.")
        ids = []
        for p in req.players:
            if p.steamId and p.steamId.isdigit() and 0 < int(p.steamId) < 1 << 64:
                ids.append(int(p.steamId))
        known = db.lobby_classes(ids)
        record_lobby_lookup(db, v.user["id"] if v.user else None, len(ids))  # a count for the admin page, no IDs
        flagged = [sid for sid, k in known.items() if lobby_class(k["classification"]) in ("ELEVATED", "HIGH")]
        details = db.lobby_details(flagged)
        banned = bans.get(ids) if bans is not None else {}
        out = []
        for p in req.players:
            sid = int(p.steamId) if p.steamId and p.steamId.isdigit() and int(p.steamId) in ids else None
            if sid is None:
                # The game didn't give a Steam ID (e.g. a bot, or a roster Overwolf couldn't fill).
                out.append({"steamId": p.steamId, "classification": None, "matchesAnalyzed": 0,
                            "note": "no Steam ID from the game"})
                continue
            k = known.get(sid)
            row = {"steamId": str(sid), "classification": lobby_class(k["classification"] if k else None),
                   "matchesAnalyzed": k["matchesAnalyzed"] if k else 0}
            if k and k.get("name"):
                row["name"] = k["name"]
            if sid in details:
                row["detail"] = detail_card(details[sid], thresholds)
            if banned.get(sid):
                row["bans"] = banned[sid]
            out.append(row)
        return {"players": out, "disclaimer": DISCLAIMER}

    return r
