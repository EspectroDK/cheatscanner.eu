"""Admin page data: queue, analysis speed and usage (``GET /admin/overview``).

Only for the Steam IDs in ``auth.admin_steam_ids`` (env ``CS2A_ADMIN_STEAM_IDS``), from a signed-in
browser; everyone else gets 404 so the page's existence isn't revealed. Without auth (local use) it is
open like everything else.
"""

from __future__ import annotations

import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Request

from cs2_analyzer.geometry.status import map_status
from cs2_analyzer.storage.repository import Database
from cs2_analyzer.storage.stats import admin_overview, recent_demo_patches

MAP_PATCH_DAYS = 30   # demos this recent decide whether a map's mesh is out of date


def router(db: Database, auth, live_jobs: Callable[[], dict], work_dir: Path,
           fetcher_last_seen: Callable[[], datetime | None], maps: dict | None = None) -> APIRouter:
    maps = maps or {}
    r = APIRouter(tags=["admin"], include_in_schema=False)

    def admin(request: Request) -> dict | None:
        user = auth.current_user(request)
        if auth.enabled and (user is None or user["tokenKind"] != "session" or not auth.is_admin(user)):
            raise HTTPException(404, "Not Found")
        return user

    @r.get("/admin/overview")
    def overview(_=Depends(admin)):
        out = admin_overview(db)
        disk = shutil.disk_usage(work_dir)
        seen = fetcher_last_seen()
        out["live"] = live_jobs()
        out["system"] = {"diskFreeGb": round(disk.free / (1 << 30), 1), "diskTotalGb": round(disk.total / (1 << 30), 1),
                         "fetcherLastSeenAt": seen.isoformat() if seen else None,
                         "serverTime": datetime.now(timezone.utc).isoformat()}
        if maps.get("dir"):
            since = datetime.now(timezone.utc) - timedelta(days=MAP_PATCH_DAYS)
            out["maps"] = map_status(maps["dir"], maps.get("render_dir"), recent_demo_patches(db, since),
                                     int(maps.get("patch_tolerance", 10)))
        return out

    return r
