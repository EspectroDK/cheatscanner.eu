"""Usage and pipeline numbers for the admin page (``GET /admin/overview``).

Everything here is read from the database. Some numbers only exist from 2026-09-29 on, because they
were not recorded before: analysis time per match (``matches.metadata.analysis_s``), queue wait
(``queue_wait_s``) and overlay lookups (``lobby_lookups``). Those come with a ``since`` date.
"""

from __future__ import annotations

import statistics
from datetime import datetime, timedelta, timezone

from sqlalchemy import case, func, select

from cs2_analyzer.storage import models as M
from cs2_analyzer.storage.repository import Database, _iso, _utc

COMPANION_TOKEN_PREFIX = "Companion app: "
SPEED_SAMPLE = 200          # latest analyzed matches used for the time-per-match numbers
SIGNUP_MATCH_WINDOW = timedelta(minutes=5)   # a user's first share code this close to sign-up is the one they typed in
CATCH_UP_AGE = timedelta(hours=24)           # matches already this old when found are history being caught up on


def _summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "median": None, "p90": None, "mean": None}
    v = sorted(values)
    return {"count": len(v), "median": round(statistics.median(v), 1),
            "p90": round(v[min(len(v) - 1, int(0.9 * len(v)))], 1), "mean": round(statistics.fmean(v), 1)}


def _game_seconds(s, completed_only: bool = True) -> dict[str, float]:
    """Length of each analyzed match in seconds: first round start to last round end, from the demo's ticks."""
    q = (select(M.Round.match_id, func.min(M.Round.start_tick), func.max(M.Round.end_tick), M.Match.tickrate)
         .join(M.Match, M.Match.match_id == M.Round.match_id).group_by(M.Round.match_id, M.Match.tickrate))
    if completed_only:
        q = q.where(M.Match.processing_status == "COMPLETED")
    out = {}
    for mid, first, last, rate in s.execute(q):
        if first is not None and last is not None and rate and last > first:
            out[mid] = (last - first) / rate
    return out


def _game_end_to_analyzed(s, since: datetime) -> dict:
    """Game finished -> analysis done, for matches fetched from Steam match history and analyzed since ``since``.

    Left out: the match a user typed in at sign-up (their first share code, queued within a few minutes of
    connecting their match history), and matches that were already more than a day old when the poller found
    them (history being caught up on after an old starting code). Uploads have no share code, so they are
    never in here.

    The demo has no clock, so the game end is estimated: Valve's match time (``played_at``, from the Game
    Coordinator) plus the match length from the demo's rounds, but never later than when the poller found
    the share code, since a share code only exists once the game is over.
    """
    access = dict(s.execute(select(M.SteamMatchAccess.user_id, M.SteamMatchAccess.created_at)).all())
    first_code: dict[int, tuple[datetime, str]] = {}
    for code, uid, created in s.execute(select(M.ShareCodeJob.share_code, M.ShareCodeJob.user_id,
                                               M.ShareCodeJob.created_at).where(M.ShareCodeJob.user_id.is_not(None))):
        if uid not in first_code or _utc(created) < first_code[uid][0]:
            first_code[uid] = (_utc(created), code)
    signup = {code for uid, (created, code) in first_code.items()
              if uid in access and abs(created - _utc(access[uid])) <= SIGNUP_MATCH_WINDOW}

    rows = s.execute(select(M.ShareCodeJob.share_code, M.ShareCodeJob.created_at, M.Match.match_id,
                            M.Match.played_at, M.Match.processed_at)
                     .join(M.Match, M.Match.match_id == M.ShareCodeJob.match_id)
                     .where(M.ShareCodeJob.status == "DONE", M.Match.processing_status == "COMPLETED",
                            M.Match.processed_at >= since)).all()
    lengths = _game_seconds(s) if rows else {}
    delays, skipped = [], {"signup": 0, "catchUp": 0, "noMatchTime": 0}
    for code, found, mid, played, done in rows:
        found, played, done = _utc(found), _utc(played), _utc(done)
        if code in signup:
            skipped["signup"] += 1
        elif played is None:
            skipped["noMatchTime"] += 1
        elif found - played > CATCH_UP_AGE:
            skipped["catchUp"] += 1
        else:
            end = min(played + timedelta(seconds=lengths.get(mid, 0.0)), found)
            delays.append(max((done - end).total_seconds(), 0.0))
    return _summary(delays) | {"skipped": skipped}


def _queued_per_hour(s, now: datetime, hours: int = 24) -> list[dict]:
    """Demos put in the analysis queue in each of the last ``hours`` whole hours (UTC, the current hour last
    and still running), by when the job was created. Fetched matches apart from uploads and path imports."""
    end = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=hours - 1)
    buckets = {start + timedelta(hours=i): {"fetched": 0, "uploaded": 0} for i in range(hours)}
    for kind, t in s.execute(select(M.AnalysisJob.kind, M.AnalysisJob.created_at)
                             .where(M.AnalysisJob.created_at >= start, M.AnalysisJob.kind != "clips")):
        b = buckets.get(_utc(t).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0))
        if b is not None:
            b["fetched" if kind == "fetch" else "uploaded"] += 1
    return [{"hour": _iso(h), **c} for h, c in buckets.items()]


def _signups_per_hour(s, now: datetime, hours: int = 24) -> list[dict]:
    """New user accounts (first Steam sign-in, ``users.created_at``) in each of the last ``hours`` whole
    hours (UTC, the current hour last and still running)."""
    end = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=hours - 1)
    buckets = {start + timedelta(hours=i): 0 for i in range(hours)}
    for (t,) in s.execute(select(M.User.created_at).where(M.User.created_at >= start)):
        h = _utc(t).astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        if h in buckets:
            buckets[h] += 1
    return [{"hour": _iso(h), "count": c} for h, c in buckets.items()]


def admin_overview(db: Database, now: datetime | None = None, days: int = 14) -> dict:
    now = now or datetime.now(timezone.utc)
    day, week, month = now - timedelta(days=1), now - timedelta(days=7), now - timedelta(days=30)
    with db.session() as s:
        def count(q) -> int:
            return int(s.scalar(q) or 0)

        def active_users(since: datetime) -> int:
            return count(select(func.count(func.distinct(M.AuthToken.user_id)))
                         .where(M.AuthToken.kind == "session", M.AuthToken.last_used_at >= since))

        # ---------------------------------------------------------- users
        users = {
            "total": count(select(func.count()).select_from(M.User)),
            "new7d": count(select(func.count()).select_from(M.User).where(M.User.created_at >= week)),
            "new30d": count(select(func.count()).select_from(M.User).where(M.User.created_at >= month)),
            "active24h": active_users(day),
            "active7d": active_users(week),
            "active30d": active_users(month),
            "matchHistoryConnected": count(select(func.count()).select_from(M.SteamMatchAccess)
                                           .where(M.SteamMatchAccess.status == "ACTIVE")),
            "signupsPerHour": _signups_per_hour(s, now),
        }

        # -------------------------------------------------------- matches
        by_status = dict(s.execute(select(M.Match.processing_status, func.count()).group_by(M.Match.processing_status)).all())
        by_map = s.execute(select(M.Match.map, func.count()).where(M.Match.processing_status == "COMPLETED")
                           .group_by(M.Match.map).order_by(func.count().desc())).all()
        fetched = set(s.scalars(select(M.ShareCodeJob.match_id).where(M.ShareCodeJob.status == "DONE",
                                                                      M.ShareCodeJob.match_id.is_not(None))))
        uploaded = set(s.scalars(select(M.MatchUpload.match_id))) - fetched
        completed = by_status.get("COMPLETED", 0)
        fetched_n = count(select(func.count()).select_from(M.Match).where(
            M.Match.processing_status == "COMPLETED", M.Match.match_id.in_(fetched))) if fetched else 0
        uploaded_n = count(select(func.count()).select_from(M.Match).where(
            M.Match.processing_status == "COMPLETED", M.Match.match_id.in_(uploaded))) if uploaded else 0
        start = (now - timedelta(days=days - 1)).date()
        per_day = {start + timedelta(days=i): 0 for i in range(days)}
        for (t,) in s.execute(select(M.Match.processed_at).where(
                M.Match.processing_status == "COMPLETED",
                M.Match.processed_at >= datetime.combine(start, datetime.min.time(), timezone.utc))):
            d = _utc(t).date()
            if d in per_day:
                per_day[d] += 1
        matches = {
            "analyzed": completed,
            "failed": by_status.get("FAILED", 0),
            "processing": by_status.get("PROCESSING", 0),
            "analyzed24h": count(select(func.count()).select_from(M.Match).where(
                M.Match.processing_status == "COMPLETED", M.Match.processed_at >= day)),
            "analyzed7d": count(select(func.count()).select_from(M.Match).where(
                M.Match.processing_status == "COMPLETED", M.Match.processed_at >= week)),
            "bySource": {"fetched": fetched_n, "uploaded": uploaded_n,
                         "other": max(completed - fetched_n - uploaded_n, 0)},
            "byMap": [{"map": m or "unknown", "count": n} for m, n in by_map],
            "perDay": [{"date": d.isoformat(), "count": n} for d, n in per_day.items()],
            "queuedPerHour": _queued_per_hour(s, now),
        }

        # -------------------------------------------------------- players
        classes = dict(s.execute(select(M.PlayerAssessment.classification, func.count())
                                 .group_by(M.PlayerAssessment.classification)).all())
        # The overall class needs >=2 matches before the play pattern counts, so a player can be HIGH in
        # one match and still NORMAL overall. Also count each player once at their highest class in any
        # single analyzed match.
        rank = case((M.PlayerMatchAssessment.classification.in_(("HIGH", "VERY_HIGH")), 3),
                    (M.PlayerMatchAssessment.classification == "ELEVATED", 2),
                    (M.PlayerMatchAssessment.classification == "NORMAL", 1), else_=0)
        worst = (select(func.max(rank).label("r")).join(M.Match, M.Match.match_id == M.PlayerMatchAssessment.match_id)
                 .where(M.Match.processing_status == "COMPLETED")
                 .group_by(M.PlayerMatchAssessment.steam_id).subquery())
        by_rank = dict(s.execute(select(worst.c.r, func.count()).group_by(worst.c.r)).all())
        # Players in 2+ analyzed matches who never signed in to the site: people who keep turning up in
        # users' matches, rather than the users themselves.
        per_player = (select(M.MatchPlayer.steam_id.label("sid"), func.count().label("n"))
                      .join(M.Match, M.Match.match_id == M.MatchPlayer.match_id)
                      .where(M.Match.processing_status == "COMPLETED",
                             M.MatchPlayer.steam_id.not_in(select(M.User.steam_id)))
                      .group_by(M.MatchPlayer.steam_id).subquery())
        seen = dict(s.execute(select(per_player.c.n, func.count()).group_by(per_player.c.n)).all())
        players = {
            "total": count(select(func.count()).select_from(M.Player)),
            "nonUsers": sum(seen.values()),
            "nonUsersSeenTwice": sum(c for k, c in seen.items() if k >= 2),
            "nonUsersSeen3Times": sum(c for k, c in seen.items() if k >= 3),
            "nonUsersSeen5Times": sum(c for k, c in seen.items() if k >= 5),
            "assessed": sum(classes.values()),
            "byClass": {"NORMAL": classes.get("NORMAL", 0), "ELEVATED": classes.get("ELEVATED", 0),
                        "HIGH": classes.get("HIGH", 0) + classes.get("VERY_HIGH", 0),
                        "INSUFFICIENT_DATA": classes.get("INSUFFICIENT_DATA", 0)},
            "byHighestMatchClass": {"NORMAL": by_rank.get(1, 0), "ELEVATED": by_rank.get(2, 0),
                                    "HIGH": by_rank.get(3, 0), "INSUFFICIENT_DATA": by_rank.get(0, 0)},
            "evidenceEvents": count(select(func.count()).select_from(M.EvidenceEvent)),
        }

        # --------------------------------------------- fetch queue and speed
        codes = dict(s.execute(select(M.ShareCodeJob.status, func.count()).group_by(M.ShareCodeJob.status)).all())
        oldest = s.scalar(select(func.min(M.ShareCodeJob.created_at)).where(M.ShareCodeJob.status == "QUEUED"))
        failed_recent = [
            {"shareCode": j.share_code, "status": j.status, "error": j.error, "updatedAt": _iso(j.updated_at)}
            for j in s.scalars(select(M.ShareCodeJob).where(M.ShareCodeJob.status.in_(("FAILED", "EXPIRED")))
                               .order_by(M.ShareCodeJob.updated_at.desc()).limit(8))]
        end_to_end = [(_utc(u) - _utc(c)).total_seconds() for c, u in s.execute(
            select(M.ShareCodeJob.created_at, M.ShareCodeJob.updated_at).where(
                M.ShareCodeJob.status == "DONE", M.ShareCodeJob.updated_at >= month))]
        metas = [m for (m,) in s.execute(select(M.Match.meta).where(M.Match.processing_status == "COMPLETED")
                                         .order_by(M.Match.processed_at.desc()).limit(SPEED_SAMPLE))]
        analysis = [float(m["analysis_s"]) for m in metas if m and m.get("analysis_s") is not None]
        waits = [float(m["queue_wait_s"]) for m in metas if m and m.get("queue_wait_s") is not None]
        fetch = {
            "byStatus": {k: codes.get(k, 0) for k in
                         ("QUEUED", "FETCHING", "DOWNLOADING", "ANALYZING", "DONE", "SKIPPED", "FAILED", "EXPIRED")},
            "oldestQueuedAt": _iso(oldest),
            "recentProblems": failed_recent,
            "lastHistoryCheckAt": _iso(s.scalar(select(func.max(M.SteamMatchAccess.last_checked_at)))),
        }
        speed = {
            "analysisSeconds": _summary(analysis),
            "queueWaitSeconds": _summary(waits),
            "fetchedToResultSeconds": _summary(end_to_end),
            "gameEndToAnalyzedSeconds": _game_end_to_analyzed(s, month),
            "recordedSince": "2026-09-29",
        }

        # ----------------------------------------------- companion app / overlay
        tokens = s.execute(select(M.AuthToken.user_id, M.AuthToken.last_used_at).where(
            M.AuthToken.kind == "api", M.AuthToken.revoked_at.is_(None),
            M.AuthToken.name.like(f"{COMPANION_TOKEN_PREFIX}%"))).all()

        def used_since(t: datetime) -> int:
            return sum(1 for _, last in tokens if last is not None and _utc(last) >= t)

        lookups_since = lambda t: count(select(func.count()).select_from(M.LobbyLookup).where(M.LobbyLookup.created_at >= t))
        companion = {
            "linkedApps": len(tokens),
            "linkedUsers": len({u for u, _ in tokens}),
            "activeLast15m": used_since(now - timedelta(minutes=15)),
            "active24h": used_since(day),
            "active7d": used_since(week),
            "lookups24h": lookups_since(day),
            "lookups7d": lookups_since(week),
            "lookupUsers24h": count(select(func.count(func.distinct(M.LobbyLookup.user_id)))
                                    .where(M.LobbyLookup.created_at >= day)),
            "lookupsRecordedSince": "2026-09-29",
        }

        uploads = {
            "attempts24h": count(select(func.count()).select_from(M.UploadAttempt).where(M.UploadAttempt.created_at >= day)),
            "attempts7d": count(select(func.count()).select_from(M.UploadAttempt).where(M.UploadAttempt.created_at >= week)),
        }

    return {"generatedAt": _iso(now), "users": users, "matches": matches, "players": players, "fetch": fetch,
            "speed": speed, "companion": companion, "uploads": uploads}


# What the scoring was fitted and checked on (docs/how-it-works.md, "How well it works"). Fixed numbers:
# update them together with that page when the calibration changes.
CALIBRATION = {
    "datasetMatches": 626,          # CS2CD matches on the nine maps with a game-built map model
    "datasetCleanMatches": 323,
    "datasetLabelledCheaters": 1244,
    "datasetMaps": 9,
    "proMatches": 15,
    "proPlayers": 140,
    "matchmakingDemos": 31,         # real matchmaking demos the mouse-input check was run on
}


def public_stats(db: Database, now: datetime | None = None) -> dict:
    """Aggregate usage numbers for the front page. Counts only: no names, Steam IDs or classes."""
    now = now or datetime.now(timezone.utc)
    with db.session() as s:
        done = M.Match.processing_status == "COMPLETED"
        matches = int(s.scalar(select(func.count()).select_from(M.Match).where(done)) or 0)
        players = int(s.scalar(select(func.count(func.distinct(M.MatchPlayer.steam_id))).select_from(M.MatchPlayer)
                               .join(M.Match, M.Match.match_id == M.MatchPlayer.match_id).where(done)) or 0)
        rounds = int(s.scalar(select(func.count()).select_from(M.Round)
                              .join(M.Match, M.Match.match_id == M.Round.match_id).where(done)) or 0)
        week = int(s.scalar(select(func.count()).select_from(M.Match)
                            .where(done, M.Match.processed_at >= now - timedelta(days=7))) or 0)
        seconds = sum(_game_seconds(s).values())
    return {"generatedAt": _iso(now), "matchesAnalyzed": matches, "matchesAnalyzed7d": week,
            "playersAnalyzed": players, "roundsAnalyzed": rounds, "gameMinutes": int(seconds // 60),
            "calibration": CALIBRATION}


def record_lobby_lookup(db: Database, user_id: int | None, players: int) -> None:
    with db.session() as s:
        s.add(M.LobbyLookup(user_id=user_id, players=players, created_at=datetime.now(timezone.utc)))


def recent_demo_patches(db: Database, since: datetime) -> dict[str, int]:
    """Newest game patch per map among demos analyzed since ``since`` (for the map-mesh check)."""
    out: dict[str, int] = {}
    with db.session() as s:
        for m, meta in s.execute(select(M.Match.map, M.Match.meta).where(M.Match.processed_at >= since)):
            p = str((meta or {}).get("patch_version") or "")
            if m and p.isdigit():
                out[m] = max(out.get(m, 0), int(p))
    return out
