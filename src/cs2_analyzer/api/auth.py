"""Accounts: Steam sign-in, browser sessions and API tokens.

A browser signs in with Steam and gets a session cookie. Programs (later the
companion app) use an API token sent as ``Authorization: Bearer <token>``;
tokens are created and revoked by a signed-in user. Only SHA-256 hashes of
sessions and tokens are stored.

With ``auth.enabled = false`` (the default, for local use) the data endpoints
stay open as before; the sign-in routes still work.
"""

from __future__ import annotations

import secrets
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from cs2_analyzer.api import steam_openid
from cs2_analyzer.config import Config
from cs2_analyzer.storage.repository import Database

SESSION_COOKIE = "cs2a_session"
STATE_COOKIE = "cs2a_openid_state"
CALLBACK_PATH = "/auth/steam/callback"


class TokenRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)


class Auth:
    def __init__(self, config: Config, db: Database, http_post: steam_openid.HttpPost | None = None,
                 profile_fetcher: Callable[[int], dict] | None = None):
        self.enabled = bool(config.get("auth.enabled", False))
        self.public_url = str(config.get("auth.public_url", "http://localhost:8000")).rstrip("/")
        self.post_login_redirect = str(config.get("auth.post_login_redirect", "/"))
        self.session_days = float(config.get("auth.session_days", 30))
        self.timeout = float(config.get("auth.openid_timeout_s", 10))
        self.secure_cookies = self.public_url.startswith("https://")
        self.db = db
        self.http_post = http_post
        api_key = str(config.get("auth.steam_api_key", "") or "")
        self.profile_fetcher = profile_fetcher or (lambda sid: steam_openid.fetch_profile(sid, api_key, self.timeout))
        # Who may open the admin page (env CS2A_ADMIN_STEAM_IDS). Without auth (local use) everyone may.
        self.admin_ids = {int(x) for x in config.get("auth.admin_steam_ids", []) or [] if str(x).strip().isdigit()}

    def is_admin(self, user: dict | None) -> bool:
        if not self.enabled:
            return True
        return user is not None and int(user["steamId"]) in self.admin_ids

    # ------------------------------------------------------------ dependencies

    def current_user(self, request: Request) -> dict | None:
        """The signed-in user from a Bearer token or session cookie, else None."""
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            return self.db.resolve_token(header[7:].strip(), kinds=("api",))
        return self.db.resolve_token(request.cookies.get(SESSION_COOKIE), kinds=("session",))

    def require_user(self, request: Request) -> dict:
        user = self.current_user(request)
        if user is None:
            raise HTTPException(401, "sign in with Steam or send an API token")
        return user

    def guard(self, request: Request) -> dict | None:
        """Dependency for data endpoints: requires a user only when auth is enabled."""
        return self.require_user(request) if self.enabled else self.current_user(request)

    # ------------------------------------------------------------------ routes

    def router(self) -> APIRouter:
        r = APIRouter(tags=["auth"])

        @r.get("/auth/steam/login")
        def login(remember: bool = False):
            # remember: the user ticked "Keep me signed in". Only then does the session cookie outlive the
            # browser (EU rules exempt a login cookie from consent while it lasts the browser session, or
            # longer when the user asked to stay signed in).
            state = secrets.token_urlsafe(24)
            return_to = f"{self.public_url}{CALLBACK_PATH}?state={state}"
            resp = RedirectResponse(steam_openid.login_url(return_to, realm=self.public_url), status_code=302)
            resp.set_cookie(STATE_COOKIE, f"{state}.{int(remember)}", max_age=600, httponly=True,
                            secure=self.secure_cookies, samesite="lax", path=CALLBACK_PATH)
            return resp

        @r.get(CALLBACK_PATH)
        def callback(request: Request):
            params = dict(request.query_params)
            # The state ties the callback to the browser that started the sign-in, so nobody can
            # sign a victim into the attacker's account with a pre-made callback link.
            state, _, remember = (request.cookies.get(STATE_COOKIE) or "").partition(".")
            if not state or not secrets.compare_digest(state, params.get("state", "")):
                raise HTTPException(400, "sign-in expired or was started in another browser; try again")
            try:
                steam_id = steam_openid.verify(params, f"{self.public_url}{CALLBACK_PATH}", self.http_post, self.timeout)
            except steam_openid.OpenIDError as exc:
                raise HTTPException(401, f"Steam sign-in failed: {exc}") from None
            user = self.db.upsert_user(steam_id, self.profile_fetcher(steam_id))
            raw, _ = self.db.create_token(user["id"], "session", ttl_days=self.session_days)
            resp = RedirectResponse(self.post_login_redirect, status_code=303)
            resp.set_cookie(SESSION_COOKIE, raw, max_age=int(self.session_days * 86400) if remember == "1" else None,
                            httponly=True, secure=self.secure_cookies, samesite="lax", path="/")
            resp.delete_cookie(STATE_COOKIE, path=CALLBACK_PATH)
            return resp

        @r.post("/auth/logout")
        def logout(user: dict = Depends(self.require_user)):
            if user["tokenKind"] == "session":
                self.db.revoke_token(user["id"], user["tokenId"])
            resp = RedirectResponse(self.post_login_redirect, status_code=303)
            resp.delete_cookie(SESSION_COOKIE, path="/")
            return resp

        @r.get("/me")
        def me(user: dict = Depends(self.require_user)):
            return {k: v for k, v in user.items() if k not in ("tokenId", "tokenKind")} | {"isAdmin": self.is_admin(user)}

        # API tokens can only be managed from a browser session, so a leaked token can't mint more.
        def session_user(request: Request) -> dict:
            user = self.require_user(request)
            if user["tokenKind"] != "session":
                raise HTTPException(403, "manage API tokens from a signed-in browser session")
            return user

        @r.get("/me/tokens")
        def list_tokens(user: dict = Depends(session_user)):
            return self.db.list_tokens(user["id"])

        @r.post("/me/tokens", status_code=201)
        def create_token(req: TokenRequest, user: dict = Depends(session_user)):
            raw, info = self.db.create_token(user["id"], "api", name=req.name)
            return info | {"token": raw, "note": "Shown once. Store it now; only a hash is kept."}

        @r.delete("/me/tokens/{token_id}", status_code=204)
        def delete_token(token_id: int, user: dict = Depends(session_user)):
            if not self.db.revoke_token(user["id"], token_id):
                raise HTTPException(404, "token not found")

        return r
