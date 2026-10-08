"""Persistence operations (SQLAlchemy 2.0). Works with PostgreSQL and SQLite."""

from __future__ import annotations

import hashlib
import secrets
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, delete, func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from cs2_analyzer.storage import models as M


# How long Valve keeps matchmaking demos on its replay servers (a month, per the user's ask).
VALVE_DEMO_KEPT = timedelta(days=30)


def _utc(dt):
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


ACTIVE_JOB = ("QUEUED", "PROCESSING")
# Job kinds: demos to analyze, and evidence clips to render for an analyzed match (worker.py).
ANALYSIS_KINDS = ("upload", "import", "fetch")
CLIP_KINDS = ("clips",)


class AlreadyProcessedError(Exception):
    def __init__(self, match_id: str, status: str):
        super().__init__(f"match {match_id} already processed (status={status}); use --force to reprocess")
        self.match_id = match_id
        self.status = status


class Database:
    def __init__(self, url: str):
        u = make_url(url)
        if u.drivername.startswith("sqlite") and u.database and u.database != ":memory:":
            Path(u.database).parent.mkdir(parents=True, exist_ok=True)
        if u.drivername == "postgresql":
            url = u.set(drivername="postgresql+psycopg").render_as_string(hide_password=False)
        self.url = url
        # SQLite (local use, tests) has one writer at a time: wait for it rather than fail after 5 s.
        args = {"connect_args": {"timeout": 30}} if u.drivername.startswith("sqlite") else {}
        self.engine = create_engine(url, future=True, pool_pre_ping=True, **args)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def safe_url(self) -> str:
        """URL with the password masked, for logs."""
        return make_url(self.url).render_as_string(hide_password=True)

    def init_schema(self):
        if self.engine.dialect.name != "postgresql":
            with self.engine.begin() as conn:
                M.Base.metadata.create_all(conn)
                _add_missing_columns(conn)
            return
        # The API, the poller and every worker start together: one creates missing tables at a time.
        with self.engine.begin() as conn:
            conn.execute(text("SELECT pg_advisory_xact_lock(4242001)"))
            M.Base.metadata.create_all(conn)
            _add_missing_columns(conn)

    def _insert_missing(self, model, rows: list[dict]) -> None:
        """Insert rows whose primary key doesn't exist yet; existing rows stay as they are (safe under concurrency)."""
        if not rows:
            return
        dialect = self.engine.dialect.name
        if dialect == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        elif dialect == "sqlite":
            from sqlalchemy.dialects.sqlite import insert
        else:
            with self.session() as s:
                for r in rows:
                    if s.get(model, tuple(r[c.name] for c in model.__table__.primary_key)) is None:
                        s.add(model(**r))
            return
        with self.session() as s:
            s.execute(insert(model).values(rows).on_conflict_do_nothing())

    @contextmanager
    def session(self):
        s: Session = self.Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    # ------------------------------------------------------------- matches

    def find_existing(self, match_id: str, sha256: str) -> M.Match | None:
        with self.session() as s:
            m = s.get(M.Match, match_id)
            if m is None:
                m = s.scalar(select(M.Match).where(M.Match.demo_sha256 == sha256))
            return m

    def begin_match(self, meta, force: bool, detector_version: str, scoring_version: str, played_at=None) -> None:
        existing = self.find_existing(meta.match_id, meta.demo_sha256)
        if existing is not None:
            if existing.processing_status == "COMPLETED" and not force:
                raise AlreadyProcessedError(existing.match_id, existing.processing_status)
            self.delete_match(existing.match_id)
        with self.session() as s:
            s.add(M.Match(
                match_id=meta.match_id, demo_sha256=meta.demo_sha256, source=meta.source, map=meta.map_name,
                mode=meta.mode, played_at=_utc(played_at), parser_version=f"{meta.parser_name} {meta.parser_version}",
                detector_version=detector_version, scoring_version=scoring_version, processing_status="PROCESSING",
                tickrate=meta.tickrate, meta={k: v for k, v in meta.extra.items()} | {"server_name": meta.server_name,
                                                                                         "patch_version": meta.patch_version,
                                                                                         "mode_source": meta.mode_source},
            ))

    def delete_match(self, match_id: str):
        with self.session() as s:
            for model in (M.EvidenceEvent, M.PlayerMatchAssessment, M.MatchPlayer, M.Round, M.PlayerMatchFeatures,
                          M.MatchShare):
                s.execute(delete(model).where(model.match_id == match_id))
            s.execute(delete(M.Match).where(M.Match.match_id == match_id))

    def mark_failed(self, match_id: str, error: str, trace: str | None = None):
        """``error`` is a one-line reason; the traceback goes into the match metadata, for the logs and admins only."""
        with self.session() as s:
            m = s.get(M.Match, match_id)
            if m:
                m.processing_status = "FAILED"
                m.error = error[:1000]
                if trace:
                    m.meta = dict(m.meta or {}) | {"error_trace": trace[-4000:]}

    def mark_completed(self, match_id: str, demo_deleted: bool, analysis_s: float | None = None):
        with self.session() as s:
            m = s.get(M.Match, match_id)
            m.processing_status = "COMPLETED"
            m.processed_at = datetime.now(timezone.utc)
            m.demo_deleted = demo_deleted
            m.error = None
            if analysis_s is not None:  # recorded since 2026-09-29, for the admin page
                m.meta = dict(m.meta or {}) | {"analysis_s": round(analysis_s, 2)}

    def note_queue_wait(self, match_id: str, wait_s: float) -> None:
        """How long an uploaded or fetched demo waited for a free analysis worker (admin page)."""
        with self.session() as s:
            m = s.get(M.Match, match_id)
            if m is not None:
                m.meta = dict(m.meta or {}) | {"queue_wait_s": round(wait_s, 2)}

    def save_results(self, result) -> None:
        """Persist parse + analysis results of one match (idempotent per match)."""
        meta = result.meta
        now = datetime.now(timezone.utc)
        # Created up front and race-free: another worker may be saving a match with the same new player right now.
        self._insert_missing(M.Player, [{"steam_id": sid, "first_seen_at": now, "last_seen_at": now, "matches_analyzed": 0}
                                        for sid in sorted({int(p.steam_id) for p in result.match_stats.itertuples()})])
        with self.session() as s:
            m = s.get(M.Match, meta.match_id)
            m.rounds_count = int(len(result.rounds))
            try:
                from cs2_analyzer.features.match_stats import starting_t_sides

                sides = starting_t_sides(result.world, result.match_stats, result.rounds)
                m.meta = dict(m.meta or {}) | {"starting_t_sides": {str(k): v for k, v in sides.items()}}
            except Exception:  # display-only extra: never fail storing a match over it
                pass
            for r in result.rounds.itertuples():
                s.merge(M.Round(match_id=meta.match_id, round_number=int(r.round_number), start_tick=_int(r.start_tick),
                                freeze_end_tick=_int(r.freeze_end_tick), end_tick=_int(r.end_tick), winner=_int(r.winner),
                                reason=_int(r.reason)))
            for p in result.match_stats.itertuples():
                sid = int(p.steam_id)
                pl = s.get(M.Player, sid)
                pl.last_known_name = str(p.name)[:128]
                pl.last_seen_at = now
                s.merge(M.MatchPlayer(match_id=meta.match_id, steam_id=sid, name=str(p.name)[:128], team=_int(p.team),
                                      kills=int(p.kills), deaths=int(p.deaths), assists=int(p.assists),
                                      headshots=int(p.headshots), damage=int(p.damage), score=_int(p.score),
                                      rank_type=_int(p.rank_type), rank_old=_int(p.rank_old), rank_new=_int(p.rank_new)))
            s.flush()
            for ev in result.events:
                s.merge(M.EvidenceEvent(
                    id=ev.id, match_id=ev.match_id, steam_id=ev.steam_id, round_number=ev.round_number,
                    tick_start=ev.tick_start, tick_peak=ev.tick_peak, tick_end=ev.tick_end, detector_type=ev.detector_type,
                    detector_version=ev.detector_version, severity=ev.severity, reliability=ev.reliability,
                    information_confidence=ev.information_confidence, confidence=ev.confidence,
                    evidence_axis=ev.evidence_axis, evidence_group=ev.evidence_group, incident_key=ev.incident_key,
                    target_steam_id=ev.target_steam_id, metrics=ev.to_dict()["metrics"], context=ev.to_dict()["context"],
                    explanation=ev.explanation, video_path=ev.video_path, debug_plot_path=ev.debug_plot_path,
                ))
            for sid, a in result.assessments.items():
                ax = a.axis_scores
                s.merge(M.PlayerMatchAssessment(
                    steam_id=int(sid), match_id=meta.match_id,
                    aim_score=ax.get("AIM_MECHANICS", 0.0), hidden_information_score=ax.get("HIDDEN_INFORMATION", 0.0),
                    shot_timing_score=ax.get("SHOT_TIMING", 0.0), recoil_score=ax.get("RECOIL", 0.0),
                    mechanical_impossibility_score=ax.get("IMPOSSIBLE_MECHANICS", 0.0),
                    decision_information_score=ax.get("DECISION_INFORMATION", 0.0),
                    overall_evidence_score=a.overall, classification=a.classification,
                    evidence_event_count=a.evidence_event_count, high_severity_event_count=a.high_severity_event_count,
                    encounters_analyzed=a.encounters_analyzed, model_version=a.model_version, details=a.to_dict(),
                ))
            for sid, fp in result.fingerprints.items():
                s.merge(M.PlayerMatchFeatures(steam_id=int(sid), match_id=meta.match_id, features=fp))

    def previous_fingerprints(self, steam_id: int, exclude_match: str) -> list[dict]:
        with self.session() as s:
            rows = s.scalars(select(M.PlayerMatchFeatures).where(M.PlayerMatchFeatures.steam_id == steam_id,
                                                                 M.PlayerMatchFeatures.match_id != exclude_match)).all()
            return [r.features for r in rows]

    def recompute_history(self, steam_id: int, cfg: dict) -> dict:
        from cs2_analyzer.scoring.aggregate import assess_history

        with self.session() as s:
            # One recomputation per player at a time (several workers may finish matches of the same player):
            # the second waits here and then reads both matches, so the last write is never missing one.
            s.get(M.Player, steam_id, with_for_update=True)
            q = (select(M.PlayerMatchAssessment, M.Match.processed_at, M.Match.played_at)
                 .join(M.Match, M.Match.match_id == M.PlayerMatchAssessment.match_id)
                 .where(M.PlayerMatchAssessment.steam_id == steam_id, M.Match.processing_status == "COMPLETED"))
            rows = s.execute(q).all()
            data = [{"overall": a.overall_evidence_score, "aim_score": a.aim_score,
                     "hidden_information_score": a.hidden_information_score, "shot_timing_score": a.shot_timing_score,
                     "recoil_score": a.recoil_score, "decision_information_score": a.decision_information_score,
                     "mechanical_impossibility_score": a.mechanical_impossibility_score,
                     "classification": a.classification, "encounters_analyzed": a.encounters_analyzed,
                     "processed_at": _utc(p), "match_id": a.match_id,
                     "played_at": _utc(pl or p), "profile": (a.details or {}).get("player_evidence")}
                    for a, p, pl in rows]
            h = assess_history(data, cfg)
            dates = [d["processed_at"] for d in data if d["processed_at"]]
            pa = s.get(M.PlayerAssessment, steam_id) or M.PlayerAssessment(steam_id=steam_id)
            pa.matches_analyzed = h["matches_analyzed"]
            pa.first_analyzed = min(dates) if dates else None
            pa.last_analyzed = max(dates) if dates else None
            pa.historical_evidence_score = h["historical_evidence_score"]
            pa.confidence_level = h["confidence_level"]
            pa.classification = h["classification"]
            pa.aim_score = h["aim_score"]
            pa.information_score = h["information_score"]
            pa.trigger_score = h["trigger_score"]
            pa.recoil_score = h["recoil_score"]
            pa.high_severity_matches = h["high_severity_matches"]
            pa.model_version = h["model_version"]
            pa.details = {"matches": [{"match_id": d["match_id"], "overall": d["overall"], "classification": d["classification"]}
                                      for d in data],
                          "player_evidence_history": h["player_evidence_history"],
                          # no column of its own (no migrations): kept here until one is added
                          "mechanical_impossibility_score": h["mechanical_impossibility_score"]}
            pa.updated_at = datetime.now(timezone.utc)
            s.merge(pa)
            pl = s.get(M.Player, steam_id)
            if pl:
                pl.matches_analyzed = h["matches_analyzed"]
            return h

    # ------------------------------------------------------------- queries (API)

    def get_match(self, match_id: str) -> dict | None:
        with self.session() as s:
            m = s.get(M.Match, match_id)
            if m is None:
                return None
            players = s.scalars(select(M.MatchPlayer).where(M.MatchPlayer.match_id == match_id)).all()
            ass = {a.steam_id: a for a in s.scalars(select(M.PlayerMatchAssessment).where(M.PlayerMatchAssessment.match_id == match_id))}
            rounds = s.scalars(select(M.Round).where(M.Round.match_id == match_id).order_by(M.Round.round_number)).all()
            return {
                "matchId": m.match_id, "source": m.source, "map": m.map, "mode": m.mode,
                "playedAt": _iso(m.played_at), "processedAt": _iso(m.processed_at), "parserVersion": m.parser_version,
                "detectorVersion": m.detector_version, "scoringVersion": m.scoring_version,
                "processingStatus": m.processing_status, "error": m.error, "demoDeleted": m.demo_deleted,
                "rounds": [{"round": r.round_number, "startTick": r.start_tick, "freezeEndTick": r.freeze_end_tick,
                            "endTick": r.end_tick, "winner": r.winner, "reason": r.reason,
                            "winnerTeam": _winner_team(r, m.meta)} for r in rounds],
                "score": _score(rounds, m.meta),
                "players": [{
                    "steamId": str(p.steam_id), "name": p.name, "team": p.team, "kills": p.kills, "deaths": p.deaths,
                    "assists": p.assists, "headshots": p.headshots, "damage": p.damage,
                    "rankType": p.rank_type, "rankOld": p.rank_old, "rankNew": p.rank_new,
                    "assessment": _assessment(ass.get(p.steam_id)),
                } for p in players],
                "valveDemo": self._valve_demo(s, m),
            }

    @staticmethod
    def _valve_demo(s: Session, m) -> dict | None:
        """Valve's own download link for a fetched match, while Valve still keeps the demo (about a month)."""
        job = s.scalars(select(M.ShareCodeJob).where(M.ShareCodeJob.match_id == m.match_id,
                                                     M.ShareCodeJob.demo_url.is_not(None))
                        .order_by(M.ShareCodeJob.created_at).limit(1)).first()
        if job is None:
            return None
        # When the match was played; older matches only have the time we first saw its share code.
        until = _utc(m.played_at or job.created_at) + VALVE_DEMO_KEPT
        if until <= datetime.now(timezone.utc):
            return None
        return {"url": job.demo_url, "shareCode": job.share_code, "availableUntil": _iso(until)}

    def match_status(self, match_id: str) -> str | None:
        with self.session() as s:
            m = s.get(M.Match, match_id)
            return m.processing_status if m else None

    def get_player(self, steam_id: int) -> dict | None:
        with self.session() as s:
            p = s.get(M.Player, steam_id)
            if p is None:
                return None
            pa = s.get(M.PlayerAssessment, steam_id)
            h = _history(pa)
            if h is not None:
                per_match = s.scalars(select(M.PlayerMatchAssessment.details)
                                      .join(M.Match, M.Match.match_id == M.PlayerMatchAssessment.match_id)
                                      .where(M.PlayerMatchAssessment.steam_id == steam_id,
                                             M.Match.processing_status == "COMPLETED")).all()
                h |= _why(pa, per_match)
            return {"steamId": str(p.steam_id), "lastKnownName": p.last_known_name, "firstSeenAt": _iso(p.first_seen_at),
                    "lastSeenAt": _iso(p.last_seen_at), "matchesAnalyzed": p.matches_analyzed,
                    "assessment": h}

    def player_matches(self, steam_id: int, only_match_ids: set[str] | None = None, limit: int | None = None) -> list[dict]:
        """Per-match assessments of a player; ``only_match_ids`` limits them to matches a viewer may see."""
        with self.session() as s:
            q = (select(M.PlayerMatchAssessment, M.Match)
                 .join(M.Match, M.Match.match_id == M.PlayerMatchAssessment.match_id)
                 .where(M.PlayerMatchAssessment.steam_id == steam_id)
                 .order_by(M.Match.processed_at.desc()))
            if only_match_ids is not None:
                q = q.where(M.Match.match_id.in_(only_match_ids))
            if limit:
                q = q.limit(limit)
            return [{"matchId": m.match_id, "map": m.map, "mode": m.mode, "playedAt": _iso(m.played_at),
                     "processedAt": _iso(m.processed_at), **_assessment(a)} for a, m in s.execute(q).all()]

    def player_timeline(self, steam_id: int) -> list[dict]:
        """Every analyzed match of a player, oldest first: date, map and that match's assessment."""
        with self.session() as s:
            q = (select(M.PlayerMatchAssessment, M.Match)
                 .join(M.Match, M.Match.match_id == M.PlayerMatchAssessment.match_id)
                 .where(M.PlayerMatchAssessment.steam_id == steam_id, M.Match.processing_status == "COMPLETED"))
            rows = [{"matchId": m.match_id, "map": m.map, "mode": m.mode, "playedAt": _iso(m.played_at or m.processed_at),
                     **_assessment(a)} for a, m in s.execute(q).all()]
        rows.sort(key=lambda r: r["playedAt"] or "")
        return rows

    def player_pattern_matches(self, steam_id: int) -> list[dict]:
        """Completed matches of a player with that match's play-pattern result (profile family), oldest first."""
        with self.session() as s:
            q = (select(M.PlayerMatchAssessment, M.Match)
                 .join(M.Match, M.Match.match_id == M.PlayerMatchAssessment.match_id)
                 .where(M.PlayerMatchAssessment.steam_id == steam_id, M.Match.processing_status == "COMPLETED"))
            rows = []
            for a, m in s.execute(q).all():
                pe = (a.details or {}).get("player_evidence") or {}
                rows.append({"matchId": m.match_id, "map": m.map, "playedAt": _iso(m.played_at or m.processed_at),
                             "classification": a.classification, "evidenceEventCount": a.evidence_event_count,
                             "strength": pe.get("strength"), "cleanPercentile": pe.get("clean_percentile")})
        rows.sort(key=lambda r: r["playedAt"] or "")
        return rows

    def match_rosters(self, match_ids: set[str]) -> dict[str, dict[int, tuple[str | None, int | None]]]:
        """Per match: steam id -> (name, team)."""
        if not match_ids:
            return {}
        out: dict[str, dict[int, tuple[str | None, int | None]]] = {}
        with self.session() as s:
            for p in s.scalars(select(M.MatchPlayer).where(M.MatchPlayer.match_id.in_(match_ids))):
                out.setdefault(p.match_id, {})[p.steam_id] = (p.name, p.team)
        return out

    def get_evidence_event(self, event_id: str) -> dict | None:
        with self.session() as s:
            e = s.get(M.EvidenceEvent, event_id)
            return _event(e) | {"_videoPath": e.video_path, "_plotPath": e.debug_plot_path,
                                "_posterPath": _poster(e.video_path)} if e else None

    def match_evidence(self, match_id: str, limit: int = 500) -> list[dict]:
        with self.session() as s:
            rows = s.scalars(select(M.EvidenceEvent).where(M.EvidenceEvent.match_id == match_id)
                             .order_by(M.EvidenceEvent.confidence.desc()).limit(limit)).all()
            m = s.get(M.Match, match_id)
            pending = _clips_pending({match_id: m} if m else {})
            return [_event(e, pending) for e in rows]

    def player_evidence(self, steam_id: int, limit: int = 200, only_match_ids: set[str] | None = None) -> list[dict]:
        with self.session() as s:
            q = select(M.EvidenceEvent).where(M.EvidenceEvent.steam_id == steam_id)
            if only_match_ids is not None:
                q = q.where(M.EvidenceEvent.match_id.in_(only_match_ids))
            rows = s.scalars(q.order_by(M.EvidenceEvent.confidence.desc()).limit(limit)).all()
            matches = {m.match_id: m for m in s.scalars(select(M.Match).where(M.Match.match_id.in_({e.match_id for e in rows})))}
            pending = _clips_pending(matches)
            return [_event(e, pending) | {"map": getattr(matches.get(e.match_id), "map", None),
                                 "playedAt": _iso(getattr(matches.get(e.match_id), "played_at", None))} for e in rows]

    def risk(self, steam_id: int) -> dict | None:
        with self.session() as s:
            pa = s.get(M.PlayerAssessment, steam_id)
            if pa is None:
                return None
            return {
                "steamId": str(steam_id),
                "classification": pa.classification,
                "evidenceScore": round(pa.historical_evidence_score, 4),
                "confidenceLevel": pa.confidence_level,
                "matchesAnalyzed": pa.matches_analyzed,
                "highSeverityMatches": pa.high_severity_matches,
                "axes": {"hiddenInformation": round(pa.information_score, 4), "aimMechanics": round(pa.aim_score, 4),
                         "shotTiming": round(pa.trigger_score, 4), "recoil": round(pa.recoil_score, 4),
                         "mechanicalImpossibility": round(_impossible(pa), 4)},
                "playerEvidenceHistory": (pa.details or {}).get("player_evidence_history"),
                "modelVersion": pa.model_version,
                "lastAnalyzed": _iso(pa.last_analyzed),
                "disclaimer": "Evidence/risk score from behavioral anomalies. Not a verdict and not a probability of cheating.",
            }

    def counts(self) -> dict:
        with self.session() as s:
            return {"matches": s.scalar(select(func.count()).select_from(M.Match)),
                    "players": s.scalar(select(func.count()).select_from(M.Player)),
                    "evidence_events": s.scalar(select(func.count()).select_from(M.EvidenceEvent))}

    # ------------------------------------------------------ viewer access

    def record_upload(self, match_id: str, user_id: int) -> None:
        with self.session() as s:
            if s.get(M.Match, match_id) is not None and s.get(M.MatchUpload, (match_id, user_id)) is None:
                s.add(M.MatchUpload(match_id=match_id, user_id=user_id))

    def record_upload_attempt(self, user_id: int) -> None:
        with self.session() as s:
            s.add(M.UploadAttempt(user_id=user_id, created_at=datetime.now(timezone.utc)))

    def upload_attempts_since(self, user_id: int, since: datetime) -> int:
        with self.session() as s:
            return len(s.scalars(select(M.UploadAttempt.id).where(M.UploadAttempt.user_id == user_id,
                                                                 M.UploadAttempt.created_at >= since)).all())

    def viewer_access(self, steam_id: int, user_id: int) -> dict:
        """What a signed-in user may see (see the module docstring of api/app.py).

        ``played``: matches the user played in. ``uploaded``: matches whose demo the user supplied.
        ``co_players``: the user and everyone in ``played`` or ``uploaded`` matches (supplying a demo
        counts like playing in it).
        """
        with self.session() as s:
            played = set(s.scalars(select(M.MatchPlayer.match_id).where(M.MatchPlayer.steam_id == steam_id)))
            uploaded = set(s.scalars(select(M.MatchUpload.match_id).where(M.MatchUpload.user_id == user_id)))
            seen = played | uploaded
            co = set(s.scalars(select(M.MatchPlayer.steam_id).where(M.MatchPlayer.match_id.in_(seen)))) if seen else set()
            return {"played": played, "uploaded": uploaded, "co_players": co | {steam_id}}

    def viewer_matches(self, steam_id: int, user_id: int) -> tuple[set[str], set[str]]:
        """(played, uploaded) of :meth:`viewer_access`, without the co-players."""
        with self.session() as s:
            played = set(s.scalars(select(M.MatchPlayer.match_id).where(M.MatchPlayer.steam_id == steam_id)))
            uploaded = set(s.scalars(select(M.MatchUpload.match_id).where(M.MatchUpload.user_id == user_id)))
            return played, uploaded

    def co_players(self, match_ids: set[str]) -> set[int]:
        """Everyone who played in any of ``match_ids``."""
        if not match_ids:
            return set()
        with self.session() as s:
            return set(s.scalars(select(M.MatchPlayer.steam_id).where(M.MatchPlayer.match_id.in_(match_ids))))

    def shared_matches(self, a: int, b: int) -> set[str]:
        """Matches in which both players appear."""
        with self.session() as s:
            mine = select(M.MatchPlayer.match_id).where(M.MatchPlayer.steam_id == a)
            return set(s.scalars(select(M.MatchPlayer.match_id).where(M.MatchPlayer.steam_id == b,
                                                                      M.MatchPlayer.match_id.in_(mine))))

    def matches_overview(self, match_ids: set[str], limit: int | None = None) -> list[dict]:
        """Newest first: match facts plus each player's class in that match; ``limit``: only the newest."""
        if not match_ids:
            return []
        with self.session() as s:
            if limit and len(match_ids) > limit:
                newest = func.coalesce(M.Match.played_at, M.Match.processed_at)
                match_ids = set(s.scalars(select(M.Match.match_id).where(M.Match.match_id.in_(match_ids))
                                          .order_by(newest.desc().nulls_last()).limit(limit)))
            matches = s.scalars(select(M.Match).where(M.Match.match_id.in_(match_ids))).all()
            rounds: dict[str, list] = {}
            for r in s.scalars(select(M.Round).where(M.Round.match_id.in_(match_ids))):
                rounds.setdefault(r.match_id, []).append(r)
            players = s.scalars(select(M.MatchPlayer).where(M.MatchPlayer.match_id.in_(match_ids))).all()
            ass = {(a.match_id, a.steam_id): a.classification for a in
                   s.scalars(select(M.PlayerMatchAssessment).where(M.PlayerMatchAssessment.match_id.in_(match_ids)))}
            by_match: dict[str, list] = {}
            for p in players:
                by_match.setdefault(p.match_id, []).append(
                    {"steamId": str(p.steam_id), "name": p.name, "team": p.team,
                     "classification": ass.get((p.match_id, p.steam_id))})
            out = [{"matchId": m.match_id, "map": m.map, "mode": m.mode, "playedAt": _iso(m.played_at),
                    "processedAt": _iso(m.processed_at), "processingStatus": m.processing_status,
                    "roundsCount": m.rounds_count, "score": _score(rounds.get(m.match_id, []), m.meta),
                    "players": by_match.get(m.match_id, [])} for m in matches]
            out.sort(key=lambda m: m["playedAt"] or m["processedAt"] or "", reverse=True)
            return out

    # ------------------------------------------------------ users and tokens

    def upsert_user(self, steam_id: int, profile: dict | None = None) -> dict:
        """Create or update the account for a Steam sign-in and stamp the login time."""
        profile = profile or {}
        with self.session() as s:
            u = s.scalar(select(M.User).where(M.User.steam_id == steam_id))
            if u is None:
                u = M.User(steam_id=steam_id)
                s.add(u)
            for attr, key in (("persona_name", "personaname"), ("avatar_url", "avatarfull"), ("profile_url", "profileurl")):
                if profile.get(key):
                    setattr(u, attr, str(profile[key])[:512])
            u.last_login_at = datetime.now(timezone.utc)
            s.flush()
            return _user(u)

    def get_user(self, user_id: int) -> dict | None:
        with self.session() as s:
            return _user(s.get(M.User, user_id))

    def create_token(self, user_id: int, kind: str, name: str | None = None, ttl_days: float | None = None) -> tuple[str, dict]:
        """Issue a new random token. Returns (raw token, token info); the raw token is not stored."""
        raw = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self.session() as s:
            t = M.AuthToken(user_id=user_id, kind=kind, name=name, token_hash=_token_hash(raw), created_at=now,
                            expires_at=now + timedelta(days=ttl_days) if ttl_days else None)
            s.add(t)
            s.flush()
            return raw, _token(t)

    def resolve_token(self, raw: str | None, kinds: tuple[str, ...] = ("session", "api")) -> dict | None:
        """User for a presented token, or None if unknown, revoked, expired or of another kind."""
        if not raw:
            return None
        now = datetime.now(timezone.utc)
        with self.session() as s:
            t = s.scalar(select(M.AuthToken).where(M.AuthToken.token_hash == _token_hash(raw)))
            if t is None or t.kind not in kinds or t.revoked_at is not None:
                return None
            if t.expires_at is not None and _utc(t.expires_at) <= now:
                return None
            # Every signed-in request lands here: write the time only every few minutes, not on each request.
            if t.last_used_at is None or now - _utc(t.last_used_at) >= timedelta(minutes=5):
                t.last_used_at = now
            user = _user(s.get(M.User, t.user_id))
            return user | {"tokenId": t.id, "tokenKind": t.kind} if user else None

    def revoke_token(self, user_id: int, token_id: int) -> bool:
        with self.session() as s:
            t = s.get(M.AuthToken, token_id)
            if t is None or t.user_id != user_id or t.revoked_at is not None:
                return False
            t.revoked_at = datetime.now(timezone.utc)
            return True

    def list_tokens(self, user_id: int, kind: str = "api") -> list[dict]:
        with self.session() as s:
            rows = s.scalars(select(M.AuthToken).where(M.AuthToken.user_id == user_id, M.AuthToken.kind == kind,
                                                       M.AuthToken.revoked_at.is_(None)).order_by(M.AuthToken.id))
            return [_token(t) for t in rows]

    # ------------------------------------------------------- match share links

    def create_share(self, match_id: str, user_id: int, ttl_hours: float, max_active: int) -> tuple[str, dict] | None:
        """A new share link for a match. Returns (raw token, info), or None when the user has too many active links."""
        now = datetime.now(timezone.utc)
        raw = secrets.token_urlsafe(32)
        with self.session() as s:
            s.execute(delete(M.MatchShare).where(M.MatchShare.expires_at < now - timedelta(days=7)))
            active = s.scalar(select(func.count(M.MatchShare.id)).where(
                M.MatchShare.user_id == user_id, M.MatchShare.revoked_at.is_(None), M.MatchShare.expires_at > now))
            if active >= max_active:
                return None
            sh = M.MatchShare(token_hash=_token_hash(raw), match_id=match_id, user_id=user_id, created_at=now,
                              expires_at=now + timedelta(hours=ttl_hours))
            s.add(sh)
            s.flush()
            return raw, _share(sh)

    def resolve_share(self, raw: str | None) -> dict | None:
        """The match and creator behind a share token, or None if unknown, removed or expired."""
        if not raw or len(raw) > 128:
            return None
        with self.session() as s:
            sh = s.scalar(select(M.MatchShare).where(M.MatchShare.token_hash == _token_hash(raw)))
            if sh is None or sh.revoked_at is not None or _utc(sh.expires_at) <= datetime.now(timezone.utc):
                return None
            user = _user(s.get(M.User, sh.user_id))
            return None if user is None else _share(sh) | {"user": user}

    def list_shares(self, user_id: int, match_id: str) -> list[dict]:
        """The user's links for a match that still work, newest first."""
        now = datetime.now(timezone.utc)
        with self.session() as s:
            rows = s.scalars(select(M.MatchShare).where(
                M.MatchShare.user_id == user_id, M.MatchShare.match_id == match_id, M.MatchShare.revoked_at.is_(None),
                M.MatchShare.expires_at > now).order_by(M.MatchShare.id.desc()))
            return [_share(sh) for sh in rows]

    def revoke_share(self, user_id: int, share_id: int) -> bool:
        with self.session() as s:
            sh = s.get(M.MatchShare, share_id)
            if sh is None or sh.user_id != user_id or sh.revoked_at is not None:
                return False
            sh.revoked_at = datetime.now(timezone.utc)
            return True

    # ------------------------------------------------- companion app pairing

    def create_pairing(self, device_name: str, ttl_s: float) -> tuple[str, str, datetime]:
        """Start linking a companion app. Returns (secret device code, short user code, expiry)."""
        now = datetime.now(timezone.utc)
        device_code = secrets.token_urlsafe(32)
        with self.session() as s:
            s.execute(delete(M.CompanionPairing).where(M.CompanionPairing.expires_at < now - timedelta(days=1)))
            used = set(s.scalars(select(M.CompanionPairing.user_code)))
            user_code = _user_code()
            while user_code in used:
                user_code = _user_code()
            expires = now + timedelta(seconds=ttl_s)
            s.add(M.CompanionPairing(device_code_hash=_token_hash(device_code), user_code=user_code,
                                     device_name=device_name, created_at=now, expires_at=expires))
        return device_code, user_code, expires

    def confirm_pairing(self, user_code: str, user_id: int) -> dict | None:
        """The signed-in user confirms the code the app shows. None if unknown, expired or already used."""
        now = datetime.now(timezone.utc)
        with self.session() as s:
            p = s.scalar(select(M.CompanionPairing).where(M.CompanionPairing.user_code == normalize_user_code(user_code)))
            if p is None or _utc(p.expires_at) <= now or p.consumed_at is not None:
                return None
            if p.user_id is not None and p.user_id != user_id:
                return None
            p.user_id, p.confirmed_at = user_id, p.confirmed_at or now
            return {"deviceName": p.device_name}

    def claim_pairing(self, device_code: str, token_ttl_days: float | None = None) -> tuple[str, str | None, dict | None]:
        """The app polls with its device code: ("PENDING"|"EXPIRED", None, None) or ("LINKED", token, user).

        The token is handed out once; later polls with the same code get EXPIRED.
        """
        now = datetime.now(timezone.utc)
        with self.session() as s:
            p = s.scalar(select(M.CompanionPairing).where(M.CompanionPairing.device_code_hash == _token_hash(device_code)))
            if p is None or p.consumed_at is not None or _utc(p.expires_at) <= now:
                return "EXPIRED", None, None
            if p.user_id is None:
                return "PENDING", None, None
            # Claim it atomically, so two polls arriving together can't both get a token.
            claimed = s.execute(update(M.CompanionPairing)
                                .where(M.CompanionPairing.id == p.id, M.CompanionPairing.consumed_at.is_(None))
                                .values(consumed_at=now)).rowcount
            if claimed != 1:
                return "EXPIRED", None, None
            user_id, name = p.user_id, p.device_name
        raw, _ = self.create_token(user_id, "api", name=f"Companion app: {name}"[:64], ttl_days=token_ttl_days)
        return "LINKED", raw, self.get_user(user_id)

    def lobby_classes(self, steam_ids: list[int]) -> dict[int, dict]:
        """Global class and matches analyzed per player (overlay, plan 4.5): nothing else."""
        if not steam_ids:
            return {}
        with self.session() as s:
            rows = s.scalars(select(M.PlayerAssessment).where(M.PlayerAssessment.steam_id.in_(steam_ids))).all()
            names = dict(s.execute(select(M.Player.steam_id, M.Player.last_known_name)
                                   .where(M.Player.steam_id.in_(steam_ids))).all())
            return {pa.steam_id: {"classification": pa.classification, "matchesAnalyzed": pa.matches_analyzed,
                                  "name": names.get(pa.steam_id)}
                    for pa in rows}

    def lobby_details(self, steam_ids: list[int], recent: int = 3) -> dict[int, dict]:
        """The overlay's extended card (F7) for flagged players: history score, high-evidence match
        count, the three evidence types and the latest flagged matches (map, score, date; no match ids)."""
        if not steam_ids:
            return {}
        out: dict[int, dict] = {}
        with self.session() as s:
            for pa in s.scalars(select(M.PlayerAssessment).where(M.PlayerAssessment.steam_id.in_(steam_ids))):
                q = (select(M.PlayerMatchAssessment, M.Match)
                     .join(M.Match, M.Match.match_id == M.PlayerMatchAssessment.match_id)
                     .where(M.PlayerMatchAssessment.steam_id == pa.steam_id, M.Match.processing_status == "COMPLETED",
                            M.PlayerMatchAssessment.classification.in_(("ELEVATED", "HIGH", "VERY_HIGH"))))
                flagged = [(a, m) for a, m in s.execute(q).all()]
                flagged.sort(key=lambda am: _aware(am[1].played_at or am[1].processed_at), reverse=True)
                out[pa.steam_id] = {
                    "evidenceScore": pa.historical_evidence_score,
                    "highEvidenceMatches": pa.high_severity_matches,
                    "axes": {"wallTracking": pa.information_score, "aim": pa.aim_score, "reaction": pa.trigger_score},
                    "recent": [{"map": m.map, "evidenceScore": a.overall_evidence_score,
                                "playedAt": _iso(m.played_at or m.processed_at)} for a, m in flagged[:recent]],
                }
        return out

    # ------------------------------------------------- match history access

    def get_match_access(self, user_id: int) -> M.SteamMatchAccess | None:
        with self.session() as s:
            return s.get(M.SteamMatchAccess, user_id)

    def set_match_access(self, user_id: int, auth_code_enc: str | None, last_share_code: str | None,
                         status: str = "ACTIVE", error: str | None = None) -> None:
        with self.session() as s:
            a = s.get(M.SteamMatchAccess, user_id) or M.SteamMatchAccess(user_id=user_id)
            a.auth_code_enc, a.last_share_code, a.status, a.last_error = auth_code_enc, last_share_code, status, error
            a.last_checked_at = datetime.now(timezone.utc)
            s.merge(a)

    def active_match_access(self) -> list[tuple[M.SteamMatchAccess, int]]:
        """(access, steam_id) for every user whose history should be polled."""
        with self.session() as s:
            q = (select(M.SteamMatchAccess, M.User.steam_id).join(M.User, M.User.id == M.SteamMatchAccess.user_id)
                 .where(M.SteamMatchAccess.status == "ACTIVE"))
            return [(a, sid) for a, sid in s.execute(q).all()]

    def queue_share_code(self, share_code: str, user_id: int, gc_match_id: int, reservation_id: int, tv_port: int) -> bool:
        """Queue a match for fetching. False if it is already known (from any user)."""
        with self.session() as s:
            if s.get(M.ShareCodeJob, share_code) is not None:
                return False
            s.add(M.ShareCodeJob(share_code=share_code, user_id=user_id, gc_match_id=gc_match_id,
                                 reservation_id=reservation_id, tv_port=tv_port))
            return True

    def claim_share_code(self, stale_after: timedelta = timedelta(minutes=10)) -> dict | None:
        """Oldest queued match for the demo fetcher (or one whose fetcher went quiet)."""
        now = datetime.now(timezone.utc)
        with self.session() as s:
            q = (select(M.ShareCodeJob)
                 .where((M.ShareCodeJob.status == "QUEUED")
                        | ((M.ShareCodeJob.status == "FETCHING") & (M.ShareCodeJob.updated_at < now - stale_after)))
                 .order_by(M.ShareCodeJob.created_at).limit(1).with_for_update(skip_locked=True))
            j = s.scalar(q)
            if j is None:
                return None
            j.status, j.attempts, j.updated_at = "FETCHING", j.attempts + 1, now
            return {"shareCode": j.share_code, "matchId": str(j.gc_match_id), "reservationId": str(j.reservation_id),
                    "tvPort": j.tv_port, "attempt": j.attempts}

    def requeue_interrupted_share_codes(self) -> int:
        """Matches stuck downloading or being analyzed with no analysis job left for them go back to the fetcher.

        (A job a worker lost on a restart stays queued as an analysis job and is picked up again; this is for
        matches left over from before the analysis queue was in the database.)
        """
        with self.session() as s:
            alive = select(M.AnalysisJob.share_code).where(M.AnalysisJob.share_code.is_not(None),
                                                           M.AnalysisJob.status.in_(ACTIVE_JOB))
            rows = list(s.scalars(select(M.ShareCodeJob).where(M.ShareCodeJob.status.in_(("DOWNLOADING", "ANALYZING")),
                                                               M.ShareCodeJob.share_code.not_in(alive))))
            for j in rows:
                j.status, j.error, j.updated_at = "QUEUED", "interrupted by a server restart; retrying", datetime.now(timezone.utc)
            return len(rows)

    def update_share_code(self, share_code: str, **fields) -> dict | None:
        with self.session() as s:
            j = s.get(M.ShareCodeJob, share_code)
            if j is None:
                return None
            for k, v in fields.items():
                setattr(j, k, v)
            j.updated_at = datetime.now(timezone.utc)
            return _share_job(j)

    def retry_share_code(self, share_code: str, user_id: int) -> dict | None:
        """Put the user's own FAILED match back to work; None if it isn't theirs or hasn't failed."""
        with self.session() as s:
            j = s.get(M.ShareCodeJob, share_code, with_for_update=True)
            if j is None or j.user_id != user_id or j.status != "FAILED":
                return None
            j.status = "DOWNLOADING" if j.demo_url else "QUEUED"
            j.attempts, j.error, j.updated_at = 0, None, datetime.now(timezone.utc)
            return _share_job(j) | {"demoUrl": j.demo_url}

    def share_code_jobs(self, user_id: int, limit: int = 50) -> list[dict]:
        with self.session() as s:
            rows = s.scalars(select(M.ShareCodeJob).where(M.ShareCodeJob.user_id == user_id)
                             .order_by(M.ShareCodeJob.created_at.desc()).limit(limit))
            return [_share_job(j) for j in rows]

    # ------------------------------------------------------ analysis queue (workers)

    def enqueue_analysis(self, kind: str, path: str | Path, *, user_id: int | None = None, share_code: str | None = None,
                         demo_url: str | None = None, force: bool = False, keep_demo: bool = False,
                         generate_evidence: bool = False, requested_match_id: str | None = None,
                         played_at=None, match_id: str | None = None) -> dict:
        """Put a demo in the analysis queue; any worker picks it up. Returns the job as the API shows it."""
        p = Path(path)
        with self.session() as s:
            j = M.AnalysisJob(id=secrets.token_hex(16), kind=kind, status="QUEUED", user_id=user_id, path=str(p),
                              file_name=p.name[:255], share_code=share_code, demo_url=demo_url, force=force,
                              keep_demo=keep_demo, generate_evidence=generate_evidence,
                              requested_match_id=requested_match_id, played_at=_utc(played_at), attempts=0,
                              match_id=match_id, created_at=datetime.now(timezone.utc))
            s.add(j)
            s.flush()
            return _analysis_job(j)

    def has_active_fetch_job(self, share_code: str) -> bool:
        with self.session() as s:
            return s.scalar(select(M.AnalysisJob.id).where(M.AnalysisJob.share_code == share_code,
                                                           M.AnalysisJob.status.in_(ACTIVE_JOB)).limit(1)) is not None

    def claim_analysis_job(self, worker: str, stale_after: timedelta, kinds: tuple[str, ...] | None = None) -> dict | None:
        """The oldest queued job (or one whose worker stopped sending heartbeats), now PROCESSING for ``worker``.

        ``kinds`` limits it to those job kinds (``ANALYSIS_KINDS``, ``CLIP_KINDS``). ``FOR UPDATE SKIP LOCKED``
        keeps two PostgreSQL workers from even looking at the same row; the conditional UPDATE does the same
        where there are no row locks (SQLite).
        """
        now = datetime.now(timezone.utc)
        J = M.AnalysisJob
        with self.session() as s:
            q = select(J).where(((J.status == "QUEUED") & (J.run_after.is_(None) | (J.run_after <= now)))
                                | ((J.status == "PROCESSING") & (J.heartbeat_at < now - stale_after)))
            if kinds is not None:
                q = q.where(J.kind.in_(kinds))
            q = q.order_by(J.created_at).limit(1).with_for_update(skip_locked=True)
            j = s.scalar(q)
            if j is None:
                return None
            taken_over = j.status == "PROCESSING"
            took = s.execute(update(J).where(J.id == j.id, J.status == j.status, J.attempts == j.attempts)
                             .values(status="PROCESSING", worker=worker[:128], attempts=j.attempts + 1, started_at=now,
                                     heartbeat_at=now, run_after=None)
                             .execution_options(synchronize_session=False)).rowcount
            if took != 1:
                return None
            if taken_over:  # its worker is gone: so are its claims on a match
                s.execute(delete(M.AnalysisLock).where(M.AnalysisLock.job_id == j.id))
            s.flush()
            s.refresh(j)
            return _analysis_job(j, internal=True) | {"takenOver": taken_over}

    def jobs_waiting(self, kinds: tuple[str, ...]) -> bool:
        """A job of these kinds is queued and may start now (an analysis worker rendering clips steps aside)."""
        now = datetime.now(timezone.utc)
        J = M.AnalysisJob
        with self.session() as s:
            return s.scalar(select(J.id).where(J.kind.in_(kinds), J.status == "QUEUED",
                                               J.run_after.is_(None) | (J.run_after <= now)).limit(1)) is not None

    def heartbeat_analysis_job(self, job_id: str, worker: str) -> bool:
        """Still working on it. False if the job is no longer this worker's (another worker took it over)."""
        J = M.AnalysisJob
        with self.session() as s:
            return s.execute(update(J).where(J.id == job_id, J.worker == worker[:128], J.status == "PROCESSING")
                             .values(heartbeat_at=datetime.now(timezone.utc))
                             .execution_options(synchronize_session=False)).rowcount == 1

    def lock_analysis_match(self, job_id: str, keys: list[str], stale_after: timedelta) -> bool:
        """Claim the match (by id and demo hash) for this job; False while another live job is analyzing it."""
        now = datetime.now(timezone.utc)
        J, L = M.AnalysisJob, M.AnalysisLock
        live = select(J.id).where(J.id != job_id, J.status == "PROCESSING", J.heartbeat_at >= now - stale_after)
        try:
            with self.session() as s:
                # leftovers of jobs that ended or died without releasing them
                s.execute(delete(L).where(L.key.in_(keys), L.job_id.not_in(live)).execution_options(synchronize_session=False))
                for k in keys:
                    s.add(L(key=k[:80], job_id=job_id, created_at=now))
                s.flush()
            return True
        except IntegrityError:
            return False

    def requeue_analysis_job(self, job_id: str, error: str | None, delay: timedelta = timedelta(0),
                             count_attempt: bool = False) -> None:
        """Back to the queue (match busy elsewhere, or the worker is shutting down); the demo stays where it is."""
        J = M.AnalysisJob
        with self.session() as s:
            j = s.get(J, job_id)
            if j is None:
                return
            j.status, j.error, j.worker, j.heartbeat_at = "QUEUED", error, None, None
            j.run_after = datetime.now(timezone.utc) + delay if delay else None
            if not count_attempt:
                j.attempts = max(0, j.attempts - 1)
            s.execute(delete(M.AnalysisLock).where(M.AnalysisLock.job_id == job_id))

    def finish_analysis_job(self, job_id: str, status: str, **fields) -> dict | None:
        """COMPLETED / DUPLICATE / FAILED, with ``match_id``, ``demo_deleted``, ``error``, ``trace``."""
        with self.session() as s:
            j = s.get(M.AnalysisJob, job_id)
            if j is None:
                return None
            j.status, j.finished_at = status, datetime.now(timezone.utc)
            for k, v in fields.items():
                setattr(j, k, v)
            s.execute(delete(M.AnalysisLock).where(M.AnalysisLock.job_id == job_id))
            s.flush()
            return _analysis_job(j)

    def get_analysis_job(self, job_id: str) -> dict | None:
        with self.session() as s:
            j = s.get(M.AnalysisJob, job_id)
            return None if j is None else _analysis_job(j, internal=True)

    # ------------------------------------------------------ evidence clips rendered after the analysis

    def set_clip_plan(self, match_id: str, event_ids: list[str]) -> None:
        """The analysis left these events' clips to a clips job: the match page shows them as on their way."""
        with self.session() as s:
            m = s.get(M.Match, match_id, with_for_update=True)
            if m is not None:
                m.meta = dict(m.meta or {}) | {"clips": {"pending": list(event_ids), "total": len(event_ids), "done": 0,
                                                         "state": "QUEUED" if event_ids else "DONE"}}

    def clip_plan(self, match_id: str) -> dict | None:
        with self.session() as s:
            m = s.get(M.Match, match_id)
            return dict((m.meta or {}).get("clips") or {}) if m is not None else None

    def match_classes(self, match_id: str) -> dict[int, str]:
        """Each player's class in this match."""
        with self.session() as s:
            return {int(a.steam_id): a.classification for a in s.scalars(
                select(M.PlayerMatchAssessment).where(M.PlayerMatchAssessment.match_id == match_id))}

    def has_active_clips_job(self, match_id: str) -> bool:
        J = M.AnalysisJob
        with self.session() as s:
            return s.scalar(select(J.id).where(J.kind.in_(CLIP_KINDS), J.match_id == match_id,
                                               J.status.in_(ACTIVE_JOB)).limit(1)) is not None

    def clip_events(self, match_id: str, event_ids: list[str]) -> list[M.EvidenceEvent]:
        with self.session() as s:
            rows = {e.id: e for e in s.scalars(select(M.EvidenceEvent).where(M.EvidenceEvent.match_id == match_id,
                                                                            M.EvidenceEvent.id.in_(event_ids)))}
            return [rows[i] for i in event_ids if i in rows]

    def update_clip_plan(self, match_id: str, *, done_event: str | None = None, video_path: str | None = None,
                         render_s: float | None = None, prep_s: float | None = None, state: str | None = None,
                         demo_deleted: bool | None = None) -> dict | None:
        """Progress of a clips job: one clip rendered (``done_event``), time spent preparing, or its end state.

        ``render_s`` and ``prep_s`` add up per match, for the estimate of when clips arrive.
        """
        with self.session() as s:
            m = s.get(M.Match, match_id, with_for_update=True)
            if m is None:
                return None
            c = dict((m.meta or {}).get("clips") or {})
            if done_event is not None:
                if video_path is not None:
                    e = s.get(M.EvidenceEvent, done_event)
                    if e is not None:
                        e.video_path = video_path
                c["pending"] = [i for i in c.get("pending", []) if i != done_event]
                c["done"] = int(c.get("done", 0)) + 1
            if render_s is not None:
                c["renderSeconds"] = round(float(c.get("renderSeconds", 0.0)) + render_s, 2)
                c["rendered"] = int(c.get("rendered", 0)) + 1
            if prep_s is not None:
                c["prepSeconds"] = round(float(c.get("prepSeconds", 0.0)) + prep_s, 2)
                c["preps"] = int(c.get("preps", 0)) + 1
            if state is not None:
                c["state"] = state
                if state in ("DONE", "FAILED"):
                    c["pending"] = []
                    c["finishedAt"] = _iso(datetime.now(timezone.utc))
            if demo_deleted is not None:
                m.demo_deleted = demo_deleted
            m.meta = dict(m.meta or {}) | {"clips": c}
            return c

    def clip_status(self, match_id: str, default_clip_s: float = 120.0, default_prep_s: float = 90.0,
                    alive_after: timedelta = timedelta(minutes=2)) -> dict | None:
        """Clips of a match still being rendered, and roughly when they will all be there (match page).

        The estimate: the clips waiting ahead of this match in the queue, shared by the workers that render
        clips, plus this match's own, at the average time per clip of recently finished clips jobs.
        """
        now = datetime.now(timezone.utc)
        J, W = M.AnalysisJob, M.AnalysisWorkerSeen
        with self.session() as s:
            m = s.get(M.Match, match_id)
            c = ((m.meta or {}).get("clips") if m is not None else None) or None
            if not c:
                return None
            pending = list(c.get("pending") or [])
            out = {"state": c.get("state", "DONE"), "total": int(c.get("total", 0)), "pending": len(pending),
                   "ready": max(0, int(c.get("total", 0)) - len(pending)), "etaSeconds": None}
            if not pending or out["state"] in ("DONE", "FAILED"):
                return out
            # Average time per clip and per demo preparation, from the last 20 finished clips jobs.
            recent = s.scalars(select(J.match_id).where(J.kind.in_(CLIP_KINDS), J.status == "COMPLETED",
                                                        J.match_id.is_not(None))
                               .order_by(J.finished_at.desc()).limit(20)).all()
            render = rendered = prep = preps = 0.0
            for mm in s.scalars(select(M.Match).where(M.Match.match_id.in_(set(recent)))) if recent else []:
                cc = (mm.meta or {}).get("clips") or {}
                render += float(cc.get("renderSeconds", 0.0))
                rendered += int(cc.get("rendered", 0))
                prep += float(cc.get("prepSeconds", 0.0))
                preps += int(cc.get("preps", 0))
            per_clip = render / rendered if rendered else default_clip_s
            per_prep = prep / preps if preps else default_prep_s

            own = s.scalars(select(J).where(J.kind.in_(CLIP_KINDS), J.match_id == match_id, J.status.in_(ACTIVE_JOB))
                            .order_by(J.created_at).limit(1)).first()
            if own is None:
                return out
            # Clips jobs ahead of this one (queued earlier, or being rendered right now).
            ahead = s.execute(select(J.match_id, J.status).where(
                J.kind.in_(CLIP_KINDS), J.status.in_(ACTIVE_JOB), J.id != own.id,
                (J.status == "PROCESSING") | (J.created_at < own.created_at))).all()
            ahead_s = 0.0
            ahead_meta = {mm.match_id: (mm.meta or {}).get("clips") or {}
                          for mm in s.scalars(select(M.Match).where(M.Match.match_id.in_({a for a, _ in ahead})))} if ahead else {}
            for mid, status in ahead:
                n = len((ahead_meta.get(mid) or {}).get("pending") or [])
                ahead_s += n * per_clip + (0.0 if status == "PROCESSING" else per_prep)
            roles = dict(s.execute(select(W.role, func.count()).where(W.last_seen_at >= now - alive_after)
                                   .group_by(W.role)).all())
            renderers = max(1, int(roles.get("clips", 0)) or sum(int(v) for v in roles.values()))
            own_s = len(pending) * per_clip + (0.0 if own.status == "PROCESSING" else per_prep)
            out["etaSeconds"] = round(ahead_s / renderers + own_s)
            out["state"] = "RENDERING" if own.status == "PROCESSING" else "QUEUED"
            return out

    def queued_uploads(self, user_id: int) -> int:
        """The user's uploaded demos still waiting for or in analysis (per-user queue limit)."""
        J = M.AnalysisJob
        with self.session() as s:
            return int(s.scalar(select(func.count()).select_from(J).where(
                J.user_id == user_id, J.kind == "upload", J.status.in_(ACTIVE_JOB))) or 0)

    def analysis_queue(self, alive_after: timedelta) -> dict:
        """Live numbers for the admin page and the deploy script."""
        now = datetime.now(timezone.utc)
        J, W = M.AnalysisJob, M.AnalysisWorkerSeen
        with self.session() as s:
            counts = {(clip, st): 0 for clip in (False, True) for st in ACTIVE_JOB}   # (clips job?, status)
            for kind, st, n in s.execute(select(J.kind, J.status, func.count()).where(J.status.in_(ACTIVE_JOB))
                                         .group_by(J.kind, J.status)).all():
                counts[(kind in CLIP_KINDS, st)] += int(n)
            uploads = s.scalar(select(func.count()).select_from(J).where(J.kind == "upload", J.status.in_(ACTIVE_JOB)))
            oldest = s.scalar(select(func.min(J.created_at)).where(J.status == "QUEUED", J.kind.not_in(CLIP_KINDS)))
            oldest_clips = s.scalar(select(func.min(J.created_at)).where(J.status == "QUEUED", J.kind.in_(CLIP_KINDS)))
            roles = dict(s.execute(select(W.role, func.count()).where(W.last_seen_at >= now - alive_after)
                                   .group_by(W.role)).all())

            def waited(t):
                return round(max(0.0, (now - _utc(t)).total_seconds()), 1) if t else 0

            return {"queued": counts[(False, "QUEUED")], "processing": counts[(False, "PROCESSING")],
                    "uploads": int(uploads or 0), "oldestWaitingSeconds": waited(oldest),
                    "clipsQueued": counts[(True, "QUEUED")], "clipsProcessing": counts[(True, "PROCESSING")],
                    "clipsOldestWaitingSeconds": waited(oldest_clips),
                    # every running job, analyses and clips (deploy/server-deploy.sh waits for these)
                    "busy": counts[(False, "PROCESSING")] + counts[(True, "PROCESSING")],
                    "workers": int(sum(roles.values())),
                    "clipWorkers": int(roles.get("clips", 0))}

    def worker_seen(self, name: str, job_id: str | None, role: str | None = None) -> None:
        now = datetime.now(timezone.utc)
        with self.session() as s:
            w = s.get(M.AnalysisWorkerSeen, name[:128])
            if w is None:
                s.add(M.AnalysisWorkerSeen(name=name[:128], role=role, started_at=now, last_seen_at=now, job_id=job_id))
            else:
                w.last_seen_at, w.job_id = now, job_id
                if role is not None:
                    w.role = role

    def worker_gone(self, name: str) -> None:
        with self.session() as s:
            s.execute(delete(M.AnalysisWorkerSeen).where(M.AnalysisWorkerSeen.name == name[:128]))

    def prune_workers(self, older_than: timedelta) -> None:
        """Forget workers not heard from in a long time (containers replaced by a deploy)."""
        cutoff = datetime.now(timezone.utc) - older_than
        with self.session() as s:
            s.execute(delete(M.AnalysisWorkerSeen).where(M.AnalysisWorkerSeen.last_seen_at < cutoff))

    # ------------------------------------------------------ Steam bans (ingest/steam_bans.py)

    def fresh_ban_ids(self, steam_ids: list[int], since: datetime) -> set[int]:
        """The SteamIDs among these whose ban record was checked at or after ``since``."""
        if not steam_ids:
            return set()
        with self.session() as s:
            q = select(M.SteamBan.steam_id, M.SteamBan.checked_at).where(M.SteamBan.steam_id.in_(steam_ids))
            return {sid for sid, at in s.execute(q) if _utc(at) >= since}

    def save_bans(self, records: dict[int, dict], checked_at: datetime) -> None:
        for attempt in (1, 2):  # a second try when another request inserted one of these players meanwhile
            try:
                with self.session() as s:
                    for sid, r in records.items():
                        row = s.get(M.SteamBan, sid) or M.SteamBan(steam_id=sid)
                        row.vac_bans, row.game_bans = r["vac_bans"], r["game_bans"]
                        row.days_since_last_ban, row.community_banned = r["days_since_last_ban"], r["community_banned"]
                        row.economy_ban, row.checked_at = r["economy_ban"], checked_at
                        s.add(row)
                return
            except IntegrityError:
                if attempt == 2:
                    raise

    def bans(self, steam_ids: list[int]) -> dict[int, dict]:
        if not steam_ids:
            return {}
        with self.session() as s:
            rows = s.scalars(select(M.SteamBan).where(M.SteamBan.steam_id.in_(steam_ids)))
            return {r.steam_id: {"vac_bans": r.vac_bans or 0, "game_bans": r.game_bans or 0,
                                 "days_since_last_ban": r.days_since_last_ban or 0,
                                 "community_banned": bool(r.community_banned), "economy_ban": r.economy_ban or "none",
                                 "checked_at": _utc(r.checked_at)} for r in rows}

    # ------------------------------------------------------ Steam chat messages (bot)

    def chat_enabled(self, user_id: int) -> bool:
        with self.session() as s:
            c = s.get(M.SteamChatSetting, user_id)
            return bool(c and c.enabled)

    def set_chat_enabled(self, user_id: int, enabled: bool) -> None:
        with self.session() as s:
            c = s.get(M.SteamChatSetting, user_id) or M.SteamChatSetting(user_id=user_id)
            c.enabled, c.updated_at = enabled, datetime.now(timezone.utc)
            s.add(c)

    def chat_allowed(self, steam_id: int) -> bool:
        """Whether this Steam account is a user who asked for chat messages (the bot accepts their friend request)."""
        with self.session() as s:
            q = (select(M.SteamChatSetting.enabled).join(M.User, M.User.id == M.SteamChatSetting.user_id)
                 .where(M.User.steam_id == steam_id))
            return bool(s.scalar(q))

    def match_summary(self, match_id: str) -> dict | None:
        """Map, date, number of evidence events and players per class: what the chat message says."""
        with self.session() as s:
            m = s.get(M.Match, match_id)
            if m is None:
                return None
            events = s.scalar(select(func.count()).select_from(M.EvidenceEvent).where(M.EvidenceEvent.match_id == match_id))
            classes = dict(s.execute(select(M.PlayerMatchAssessment.classification, func.count())
                                     .where(M.PlayerMatchAssessment.match_id == match_id)
                                     .group_by(M.PlayerMatchAssessment.classification)).all())
            return {"matchId": match_id, "map": m.map, "playedAt": _utc(m.played_at), "events": int(events or 0),
                    "classes": classes}

    def queue_chat_messages(self, match_id: str, text: str) -> int:
        """Queue ``text`` for every opted-in user who played in the match.

        Only players: the message counts the classes of everyone in the match, and players may see those (an
        uploader who wasn't in the match sees strangers as hidden). At most one message per user and match, so a
        re-analysis doesn't message anyone twice.
        """
        with self.session() as s:
            players = select(M.MatchPlayer.steam_id).where(M.MatchPlayer.match_id == match_id)
            q = (select(M.User.id, M.User.steam_id).join(M.SteamChatSetting, M.SteamChatSetting.user_id == M.User.id)
                 .where(M.SteamChatSetting.enabled.is_(True))
                 .where(M.User.steam_id.in_(players)))
            n = 0
            for uid, sid in s.execute(q).all():
                done = s.scalar(select(M.SteamChatMessage.id).where(M.SteamChatMessage.user_id == uid,
                                                                    M.SteamChatMessage.match_id == match_id))
                if done is None:
                    s.add(M.SteamChatMessage(user_id=uid, match_id=match_id, steam_id=sid, text=text))
                    n += 1
            return n

    def claim_chat_messages(self, limit: int = 10, stale_after: timedelta = timedelta(minutes=10)) -> list[dict]:
        """Oldest waiting messages for the bot (or ones claimed by a bot that went quiet)."""
        now = datetime.now(timezone.utc)
        with self.session() as s:
            q = (select(M.SteamChatMessage)
                 .where((M.SteamChatMessage.status == "PENDING")
                        | ((M.SteamChatMessage.status == "SENDING") & (M.SteamChatMessage.updated_at < now - stale_after)))
                 .order_by(M.SteamChatMessage.created_at).limit(limit).with_for_update(skip_locked=True))
            out = []
            for m in s.scalars(q):
                m.status, m.attempts, m.updated_at = "SENDING", m.attempts + 1, now
                out.append({"id": m.id, "steamId": str(m.steam_id), "text": m.text, "attempt": m.attempts})
            return out

    def finish_chat_message(self, message_id: int, status: str, error: str | None = None) -> dict | None:
        with self.session() as s:
            m = s.get(M.SteamChatMessage, message_id)
            if m is None:
                return None
            m.status, m.error, m.updated_at = status, error, datetime.now(timezone.utc)
            return _chat_message(m)

    def last_chat_message(self, user_id: int) -> dict | None:
        with self.session() as s:
            m = s.scalar(select(M.SteamChatMessage).where(M.SteamChatMessage.user_id == user_id)
                         .order_by(M.SteamChatMessage.created_at.desc(), M.SteamChatMessage.id.desc()).limit(1))
            return _chat_message(m) if m else None


# No 0/O, 1/I/L: the code is read off one screen and typed on another.
_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def _add_missing_columns(conn) -> None:
    """``create_all`` only creates missing tables: add columns that newer versions added to existing ones.

    Only nullable columns without a server default are added this way (all a new column may be).
    """
    from sqlalchemy import inspect

    insp = inspect(conn)
    tables = set(insp.get_table_names())
    for table in M.Base.metadata.sorted_tables:
        if table.name not in tables:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name not in have and col.nullable and not col.primary_key:
                ddl = col.type.compile(dialect=conn.dialect)
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN "{col.name}" {ddl}'))


def _user_code() -> str:
    c = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{c[:4]}-{c[4:]}"


def normalize_user_code(code: str) -> str:
    """'abcd 2345', 'ABCD-2345' and 'abcd2345' are the same code."""
    c = "".join(ch for ch in (code or "").upper() if ch.isalnum())[:8]
    return f"{c[:4]}-{c[4:]}"

def _int(v):
    try:
        if v is None or v != v:  # NaN
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _winner_team(r, meta: dict | None) -> int | None:
    """Winner of a round as the team's *starting* side (2 = team that started T, 3 = started CT)."""
    if r.winner not in (2, 3):
        return None
    side_of_t = (meta or {}).get("starting_t_sides", {}).get(str(r.round_number))
    if side_of_t is None:
        if r.round_number > 24:        # overtime swaps can't be derived without the stored sides
            return None
        side_of_t = 2 if r.round_number <= 12 else 3
    return 2 if r.winner == side_of_t else 3


def _score(rounds, meta: dict | None) -> dict | None:
    """Rounds won per team, keyed by starting side ("2" started T, "3" started CT)."""
    score = {"2": 0, "3": 0}
    for r in rounds:
        if r.winner not in (2, 3):
            continue
        w = _winner_team(r, meta)
        if w is None:
            return None
        score[str(w)] += 1
    return score if rounds else None


def _share_job(j) -> dict:
    return {"shareCode": j.share_code, "userId": j.user_id, "status": j.status, "attempts": j.attempts, "matchId": j.match_id,
            "error": j.error, "createdAt": _iso(j.created_at), "updatedAt": _iso(j.updated_at)}

def _chat_message(m) -> dict:
    return {"id": m.id, "matchId": m.match_id, "status": m.status, "attempts": m.attempts, "error": m.error,
            "createdAt": _iso(m.created_at), "updatedAt": _iso(m.updated_at)}


def _token_hash(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _user(u) -> dict | None:
    if u is None:
        return None
    return {"id": u.id, "steamId": str(u.steam_id), "personaName": u.persona_name, "avatarUrl": u.avatar_url,
            "profileUrl": u.profile_url, "createdAt": _iso(u.created_at), "lastLoginAt": _iso(u.last_login_at)}


def _token(t) -> dict:
    return {"id": t.id, "kind": t.kind, "name": t.name, "createdAt": _iso(t.created_at),
            "expiresAt": _iso(t.expires_at), "lastUsedAt": _iso(t.last_used_at)}


def _share(sh) -> dict:
    return {"id": sh.id, "matchId": sh.match_id, "createdAt": _iso(sh.created_at), "expiresAt": _iso(sh.expires_at)}


def _iso(dt):
    return _utc(dt).isoformat() if dt else None


def _assessment(a) -> dict | None:
    if a is None:
        return None
    return {"classification": a.classification, "overallEvidenceScore": round(a.overall_evidence_score, 4),
            "axes": {"aim": a.aim_score, "hiddenInformation": a.hidden_information_score, "shotTiming": a.shot_timing_score,
                     "recoil": a.recoil_score, "mechanicalImpossibility": a.mechanical_impossibility_score,
                     "decisionInformation": a.decision_information_score},
            "evidenceEventCount": a.evidence_event_count, "highSeverityEventCount": a.high_severity_event_count,
            "encountersAnalyzed": a.encounters_analyzed, "modelVersion": a.model_version,
            # the match's overall play pattern (profile family): it can lift the class without any event
            "profileStrength": ((a.details or {}).get("player_evidence") or {}).get("strength")}


def _aware(dt: datetime | None) -> datetime:
    """For sorting: SQLite hands back naive datetimes, Postgres aware ones; missing dates sort first."""
    if dt is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _history(pa) -> dict | None:
    if pa is None:
        return None
    return {"classification": pa.classification, "historicalEvidenceScore": round(pa.historical_evidence_score, 4),
            "confidenceLevel": pa.confidence_level, "matchesAnalyzed": pa.matches_analyzed,
            "firstAnalyzed": _iso(pa.first_analyzed), "lastAnalyzed": _iso(pa.last_analyzed),
            "aimScore": pa.aim_score, "informationScore": pa.information_score, "triggerScore": pa.trigger_score,
            "recoilScore": pa.recoil_score, "mechanicalImpossibilityScore": _impossible(pa),
            "highSeverityMatches": pa.high_severity_matches}


def _impossible(pa) -> float:
    return float((pa.details or {}).get("mechanical_impossibility_score") or 0.0)


def _why(pa, per_match_details: list) -> dict:
    """What the history score is made of, so a class is never shown without its reason.

    The history score joins evidence events and the player's overall play pattern (the ``profile``
    family, scoring/player_evidence.py) by noisy-OR (scoring/aggregate.assess_history). The pattern
    is measured over every duel of a match and produces no evidence events of its own, so a player
    can be ELEVATED with an empty event list. ``profile.features`` names the measurements that
    pushed the pattern up most, summed over the player's matches.
    """
    prof = (pa.details or {}).get("player_evidence_history") or {}
    strength = float(prof.get("strength") or 0.0)
    total = float(pa.historical_evidence_score or 0.0)
    events_part = max(0.0, 1.0 - (1.0 - total) / (1.0 - strength)) if strength < 1.0 else 0.0
    feats: dict[str, dict] = {}
    for d in per_match_details:
        for key, f in (((d or {}).get("player_evidence") or {}).get("features") or {}).items():
            c = float(f.get("contribution") or 0.0)
            if c > 0:
                acc = feats.setdefault(key, {"feature": key, "contribution": 0.0, "matches": 0})
                acc["contribution"] += c
                acc["matches"] += 1
    top = sorted(feats.values(), key=lambda f: -f["contribution"])[:3]
    return {
        "eventEvidenceScore": round(events_part, 4),
        "profile": None if not prof else {
            "strength": round(strength, 4), "percentile": prof.get("percentile"), "matches": prof.get("matches"),
            "suddenChange": bool(prof.get("sudden_change")),
            "features": [f | {"contribution": round(f["contribution"], 3)} for f in top],
        },
    }


def _clips_pending(matches: dict) -> set[str]:
    """Events whose clip is still to be rendered (a clips job is queued for their match)."""
    return {i for m in matches.values() for i in ((m.meta or {}).get("clips") or {}).get("pending", [])}


def _event(e, clips_pending: set[str] = frozenset()) -> dict:
    return {"id": e.id, "matchId": e.match_id, "steamId": str(e.steam_id), "round": e.round_number,
            "tickStart": e.tick_start, "tickPeak": e.tick_peak, "tickEnd": e.tick_end, "detector": e.detector_type,
            "detectorVersion": e.detector_version, "severity": e.severity, "reliability": e.reliability,
            "informationConfidence": e.information_confidence, "confidence": e.confidence, "axis": e.evidence_axis,
            "group": e.evidence_group, "targetSteamId": str(e.target_steam_id) if e.target_steam_id else None,
            "metrics": e.metrics, "context": e.context, "explanation": e.explanation,
            # Files stay on the server; the API serves them at these URLs to viewers who may see the event.
            "clipUrl": f"/evidence/{e.id}/clip" if _is_file(e.video_path) else None,
            "posterUrl": f"/evidence/{e.id}/poster" if _is_file(e.video_path) and _is_file(_poster(e.video_path)) else None,
            "plotUrl": f"/evidence/{e.id}/plot" if _is_file(e.debug_plot_path) else None,
            "clipPending": e.id in clips_pending and not _is_file(e.video_path),
            "createdAt": _iso(e.created_at)}


def _poster(video_path: str | None) -> str | None:
    """Thumbnail frame written next to the clip (evidence.clips.poster_path)."""
    return str(Path(video_path).with_suffix(".jpg")) if video_path else None


def _is_file(path: str | None) -> bool:
    return bool(path) and Path(path).is_file()


def _analysis_job(j: M.AnalysisJob, internal: bool = False) -> dict:
    """A queued analysis as ``GET /jobs/{id}`` shows it (``internal`` adds what a worker needs)."""
    out = {"jobId": j.id, "status": j.status, "file": j.file_name}
    if j.match_id:
        out["matchId"] = j.match_id
    if j.status == "COMPLETED":
        out["demoDeleted"] = bool(j.demo_deleted)
    if j.error:
        out["error"] = j.error
    if internal:
        out.update(kind=j.kind, path=j.path, userId=j.user_id, shareCode=j.share_code, demoUrl=j.demo_url,
                   force=bool(j.force), keepDemo=bool(j.keep_demo), generateEvidence=bool(j.generate_evidence),
                   requestedMatchId=j.requested_match_id, playedAt=_utc(j.played_at), attempts=j.attempts,
                   createdAt=_utc(j.created_at), trace=j.trace)
    return out
