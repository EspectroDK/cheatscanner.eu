"""Steam chat message when a user's match has been analyzed (opt-in, Settings).

The demo fetcher's bot account (``services/demo-fetcher``) sends the messages. Steam only lets it
message its friends, so the user switches the messages on here and adds the bot as a Steam friend;
the bot accepts friend requests only from users who switched them on (``/internal/chat/allowed``).

When an analysis finishes, ``queue_for_match`` puts one message per opted-in player of that match
in an outbox; the bot claims and sends them and reports back.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime, timezone
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from cs2_analyzer.storage.repository import Database

MAX_ATTEMPTS = 3
RAISED = ("ELEVATED", "HIGH", "VERY_HIGH")   # classes the message names; NORMAL and INSUFFICIENT_DATA are not


class ChatSetting(BaseModel):
    enabled: bool


class ChatClaim(BaseModel):
    botSteamId: str | None = Field(None, max_length=20)
    friends: list[str] | None = Field(None, max_length=2000)


class ChatResult(BaseModel):
    sent: bool = False
    notFriend: bool = False
    error: str | None = Field(None, max_length=500)


def map_label(name: str | None) -> str:
    """``de_mirage`` -> ``Mirage``."""
    if not name:
        return "an unknown map"
    base = name.split("_", 1)[1] if name.split("_", 1)[0] in ("de", "cs", "ar") and "_" in name else name
    return base.replace("_", " ").title()


def match_message(summary: dict, public_url: str) -> str:
    """Plain text for Steam chat: what was found, the link, and that a class is not a verdict."""
    when = summary.get("playedAt")
    on = f"on {map_label(summary.get('map'))}" + (f" ({when:%d %b %Y})" if isinstance(when, datetime) else "")
    events = summary.get("events", 0)
    raised = [(c, summary.get("classes", {}).get(c, 0)) for c in RAISED]
    raised = [(c, n) for c, n in raised if n]
    if not events and not raised:
        found = "no evidence events and no player with a raised evidence class"
    else:
        parts = [f"{events} evidence event{'s' if events != 1 else ''}"]
        parts += [f"{n} player{'s' if n != 1 else ''} {c.replace('_', ' ')}" for c, n in raised]
        found = ", ".join(parts)
    # The website uses hash routes; /matches/<id> without the # is the API's JSON endpoint.
    url = f"{public_url}/#/matches/{urllib.parse.quote(summary['matchId'], safe='')}"
    return (f"Your CS2 match {on} has been analyzed: {found}.\n{url}\n"
            "An evidence class means behaviour worth a closer look, not a verdict.")


class SteamChat:
    def __init__(self, db: Database, public_url: str):
        self.db = db
        self.public_url = public_url.rstrip("/")
        # Reported by the bot on every claim; kept in memory only (the bot claims every few seconds).
        self.bot_steam_id: str | None = None
        self.friends: set[str] | None = None
        self.bot_last_seen: datetime | None = None

    def queue_for_match(self, match_id: str) -> int:
        summary = self.db.match_summary(match_id)
        if summary is None:
            return 0
        return self.db.queue_chat_messages(match_id, match_message(summary, self.public_url))

    def status(self, user: dict) -> dict:
        bot = None
        if self.bot_steam_id:
            bot = {"steamId": self.bot_steam_id, "profileUrl": f"https://steamcommunity.com/profiles/{self.bot_steam_id}"}
        return {"enabled": self.db.chat_enabled(user["id"]), "bot": bot,
                "friends": (str(user["steamId"]) in self.friends) if self.friends is not None else None,
                "lastMessage": self.db.last_chat_message(user["id"])}

    def router(self, require_user: Callable, require_service: Callable) -> APIRouter:
        r = APIRouter(tags=["steam chat"])

        @r.get("/me/steam-chat")
        def get_setting(user: dict = Depends(require_user)):
            return self.status(user)

        @r.put("/me/steam-chat")
        def put_setting(req: ChatSetting, user: dict = Depends(require_user)):
            self.db.set_chat_enabled(user["id"], req.enabled)
            return self.status(user)

        @r.post("/internal/chat/claim", dependencies=[Depends(require_service)])
        def claim(req: ChatClaim | None = None):
            self.bot_last_seen = datetime.now(timezone.utc)
            if req and req.botSteamId and req.botSteamId.isdigit():
                self.bot_steam_id = req.botSteamId
            if req and req.friends is not None:
                self.friends = {f for f in req.friends if f.isdigit()}
            return self.db.claim_chat_messages()

        @r.get("/internal/chat/allowed/{steam_id}", dependencies=[Depends(require_service)])
        def allowed(steam_id: str):
            if not steam_id.isdigit() or len(steam_id) > 20:
                raise HTTPException(400, "steamId must be a SteamID64")
            return {"allowed": self.db.chat_allowed(int(steam_id))}

        @r.post("/internal/chat/{message_id}/result", dependencies=[Depends(require_service)])
        def result(message_id: int, res: ChatResult):
            if res.sent:
                msg = self.db.finish_chat_message(message_id, "SENT")
            elif res.notFriend:
                msg = self.db.finish_chat_message(message_id, "SKIPPED", "not friends with the bot on Steam")
            else:
                msg = self.db.finish_chat_message(message_id, "FAILED", res.error or "sending failed")
                if msg and msg["attempts"] < MAX_ATTEMPTS:
                    msg = self.db.finish_chat_message(message_id, "PENDING", msg["error"])
            if msg is None:
                raise HTTPException(404, "unknown message")
            return msg

        return r
