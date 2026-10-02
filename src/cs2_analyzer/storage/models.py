"""Relational schema (PostgreSQL in production, SQLite for tests).

Raw per-tick data is intentionally NOT stored here; only compact metadata,
features, evidence and aggregates. Evidence rows are append-only per match so
player history never overwrites individual match evidence.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def clean_json(value):
    """Make a value storable as PostgreSQL JSONB, which rejects NaN/Infinity and NUL characters.

    Demo headers and player names can carry NUL padding, and metrics can be NaN; SQLite takes both,
    PostgreSQL refuses the whole row (DataError). NaN/inf become null, NULs are dropped.
    """
    if isinstance(value, dict):
        return {clean_text(str(k)): clean_json(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return [clean_json(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(value) else None
    if isinstance(value, str):
        return clean_text(value)
    return value


def clean_text(value: str | None) -> str | None:
    return value.replace("\x00", "") if isinstance(value, str) else value


class SafeJSON(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(JSONB() if dialect.name == "postgresql" else JSON())

    def process_bind_param(self, value, dialect):
        return clean_json(value)


class SafeText(TypeDecorator):
    """Text from outside (player names, error messages): no NUL characters, cut to the column length."""

    impl = Text
    cache_ok = True

    def __init__(self, length: int | None = None):
        super().__init__()
        self.length = length

    def load_dialect_impl(self, dialect):
        return dialect.type_descriptor(String(self.length) if self.length else Text())

    def process_bind_param(self, value, dialect):
        value = clean_text(value)
        return value[: self.length] if self.length and isinstance(value, str) else value


JSONType = SafeJSON()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Player(Base):
    __tablename__ = "players"
    steam_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    last_known_name: Mapped[str | None] = mapped_column(SafeText(128))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    matches_analyzed: Mapped[int] = mapped_column(Integer, default=0)


class Match(Base):
    __tablename__ = "matches"
    match_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    demo_sha256: Mapped[str | None] = mapped_column(String(64), unique=True)
    source: Mapped[str | None] = mapped_column(String(64))
    map: Mapped[str | None] = mapped_column(String(64))
    mode: Mapped[str | None] = mapped_column(String(32))
    played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    parser_version: Mapped[str | None] = mapped_column(String(64))
    detector_version: Mapped[str | None] = mapped_column(String(64))
    scoring_version: Mapped[str | None] = mapped_column(String(64))
    processing_status: Mapped[str] = mapped_column(String(16), default="PENDING")
    error: Mapped[str | None] = mapped_column(SafeText())
    tickrate: Mapped[float | None] = mapped_column(Float)
    rounds_count: Mapped[int | None] = mapped_column(Integer)
    demo_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    meta: Mapped[dict | None] = mapped_column("metadata", JSONType)


class Round(Base):
    __tablename__ = "rounds"
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.match_id", ondelete="CASCADE"), primary_key=True)
    round_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    start_tick: Mapped[int | None] = mapped_column(Integer)
    freeze_end_tick: Mapped[int | None] = mapped_column(Integer)
    end_tick: Mapped[int | None] = mapped_column(Integer)
    winner: Mapped[int | None] = mapped_column(Integer)
    reason: Mapped[int | None] = mapped_column(Integer)


class MatchPlayer(Base):
    """Ordinary scoreboard stats. NOT used as cheating evidence."""

    __tablename__ = "match_players"
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.match_id", ondelete="CASCADE"), primary_key=True)
    steam_id: Mapped[int] = mapped_column(ForeignKey("players.steam_id"), primary_key=True)
    name: Mapped[str | None] = mapped_column(SafeText(128))
    team: Mapped[int | None] = mapped_column(Integer)
    kills: Mapped[int] = mapped_column(Integer, default=0)
    deaths: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    headshots: Mapped[int] = mapped_column(Integer, default=0)
    damage: Mapped[int] = mapped_column(Integer, default=0)
    score: Mapped[int | None] = mapped_column(Integer)
    rank_type: Mapped[int | None] = mapped_column(Integer)
    rank_old: Mapped[int | None] = mapped_column(Integer)
    rank_new: Mapped[int | None] = mapped_column(Integer)


class EvidenceEvent(Base):
    __tablename__ = "evidence_events"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.match_id", ondelete="CASCADE"), index=True)
    steam_id: Mapped[int] = mapped_column(BigInteger, index=True)
    round_number: Mapped[int | None] = mapped_column(Integer)
    tick_start: Mapped[int] = mapped_column(Integer)
    tick_peak: Mapped[int] = mapped_column(Integer)
    tick_end: Mapped[int] = mapped_column(Integer)
    detector_type: Mapped[str] = mapped_column(String(64))
    detector_version: Mapped[str | None] = mapped_column(String(64))
    severity: Mapped[float] = mapped_column(Float)
    reliability: Mapped[float] = mapped_column(Float)
    information_confidence: Mapped[float] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float)
    evidence_axis: Mapped[str] = mapped_column(String(32))
    evidence_group: Mapped[str] = mapped_column(String(32))
    incident_key: Mapped[str | None] = mapped_column(String(128))
    target_steam_id: Mapped[int | None] = mapped_column(BigInteger)
    metrics: Mapped[dict | None] = mapped_column(JSONType)
    context: Mapped[dict | None] = mapped_column(JSONType)
    explanation: Mapped[str | None] = mapped_column(SafeText())
    video_path: Mapped[str | None] = mapped_column(Text)
    debug_plot_path: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (Index("ix_evidence_player_match", "steam_id", "match_id"),)


class PlayerMatchAssessment(Base):
    __tablename__ = "player_match_assessments"
    steam_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.match_id", ondelete="CASCADE"), primary_key=True)
    aim_score: Mapped[float] = mapped_column(Float, default=0.0)
    hidden_information_score: Mapped[float] = mapped_column(Float, default=0.0)
    shot_timing_score: Mapped[float] = mapped_column(Float, default=0.0)
    recoil_score: Mapped[float] = mapped_column(Float, default=0.0)
    mechanical_impossibility_score: Mapped[float] = mapped_column(Float, default=0.0)
    decision_information_score: Mapped[float] = mapped_column(Float, default=0.0)
    overall_evidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    classification: Mapped[str] = mapped_column(String(32))
    evidence_event_count: Mapped[int] = mapped_column(Integer, default=0)
    high_severity_event_count: Mapped[int] = mapped_column(Integer, default=0)
    encounters_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    model_version: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSONType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerAssessment(Base):
    """History aggregate; recomputed from PlayerMatchAssessment rows, never the source of truth."""

    __tablename__ = "player_assessments"
    steam_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    matches_analyzed: Mapped[int] = mapped_column(Integer, default=0)
    first_analyzed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_analyzed: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    historical_evidence_score: Mapped[float] = mapped_column(Float, default=0.0)
    confidence_level: Mapped[str] = mapped_column(String(16))
    classification: Mapped[str] = mapped_column(String(32))
    aim_score: Mapped[float] = mapped_column(Float, default=0.0)
    information_score: Mapped[float] = mapped_column(Float, default=0.0)
    trigger_score: Mapped[float] = mapped_column(Float, default=0.0)
    recoil_score: Mapped[float] = mapped_column(Float, default=0.0)
    high_severity_matches: Mapped[int] = mapped_column(Integer, default=0)
    model_version: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict | None] = mapped_column(JSONType)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PlayerMatchFeatures(Base):
    """Per-match behavioral fingerprint (distribution summaries) for player baselines."""

    __tablename__ = "player_match_features"
    steam_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.match_id", ondelete="CASCADE"), primary_key=True)
    features: Mapped[dict] = mapped_column(JSONType)


class BaselineStat(Base):
    """Population reference distribution of one metric in one stratum.

    Built by the calibration tools from matches believed to be legitimate.
    ``stratum`` is a canonical ``key=value;...`` string (weapon, map, range
    bucket, movement state, scoped, engagement type, rank bucket...).
    """

    __tablename__ = "baseline_stats"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    metric: Mapped[str] = mapped_column(String(96))
    stratum: Mapped[str] = mapped_column(String(256))
    n: Mapped[int] = mapped_column(Integer)
    mean: Mapped[float | None] = mapped_column(Float)
    std: Mapped[float | None] = mapped_column(Float)
    quantiles: Mapped[dict | None] = mapped_column(JSONType)
    source_matches: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (UniqueConstraint("metric", "stratum", name="uq_baseline_metric_stratum"),)


class User(Base):
    """A person signed in with Steam. Not the same as ``Player`` (anyone seen in a demo)."""

    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    steam_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    persona_name: Mapped[str | None] = mapped_column(SafeText(128))
    avatar_url: Mapped[str | None] = mapped_column(String(512))
    profile_url: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AuthToken(Base):
    """Browser session or API token. Only a SHA-256 of the token is stored, never the token itself."""

    __tablename__ = "auth_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))          # session | api
    name: Mapped[str | None] = mapped_column(String(64))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MatchUpload(Base):
    """Which signed-in user supplied a match's demo (upload or automatic fetch)."""

    __tablename__ = "match_uploads"
    match_id: Mapped[str] = mapped_column(ForeignKey("matches.match_id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class UploadAttempt(Base):
    """A website demo upload that was started, for the per-user daily upload limit."""

    __tablename__ = "upload_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class SteamMatchAccess(Base):
    """A user's permission to read their match history (Steam game authentication code).

    The authentication code is stored encrypted (``ingest/secrets.py``) and never returned by the API.
    """

    __tablename__ = "steam_match_access"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    auth_code_enc: Mapped[str | None] = mapped_column(Text)
    last_share_code: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(16), default="ACTIVE")   # ACTIVE | REJECTED | REMOVED
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(SafeText())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ShareCodeJob(Base):
    """One match to fetch: share code -> demo URL (Game Coordinator) -> download -> analysis."""

    __tablename__ = "share_code_jobs"
    share_code: Mapped[str] = mapped_column(String(40), primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    gc_match_id: Mapped[int] = mapped_column(BigInteger)
    reservation_id: Mapped[int] = mapped_column(BigInteger)
    tv_port: Mapped[int] = mapped_column(Integer)
    # QUEUED -> FETCHING (claimed by the demo fetcher) -> DOWNLOADING -> ANALYZING -> DONE
    # or FAILED / EXPIRED (Valve no longer has the demo)
    status: Mapped[str] = mapped_column(String(16), default="QUEUED", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    demo_url: Mapped[str | None] = mapped_column(Text)
    match_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(SafeText())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SteamChatSetting(Base):
    """A user's choice to get a Steam chat message from the demo-fetcher bot when a match of theirs is analyzed."""

    __tablename__ = "steam_chat_settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SteamChatMessage(Base):
    """Outbox of Steam chat messages the bot sends (one per user and match)."""

    __tablename__ = "steam_chat_messages"
    __table_args__ = (UniqueConstraint("user_id", "match_id"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    match_id: Mapped[str] = mapped_column(String(64))
    steam_id: Mapped[int] = mapped_column(BigInteger)
    text: Mapped[str] = mapped_column(SafeText())
    # PENDING -> SENDING (claimed by the bot) -> SENT, or SKIPPED (not friends with the bot) / FAILED
    status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(SafeText())
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CompanionPairing(Base):
    """Linking the companion app to an account (device-code flow, like signing in a TV app).

    The app asks for a pairing and shows the short ``user_code``; the user confirms that code on the
    website while signed in; the app, polling with its secret device code, then receives an API token
    once. Only a SHA-256 of the device code is stored.
    """

    __tablename__ = "companion_pairings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    device_code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_code: Mapped[str] = mapped_column(String(16), unique=True)
    device_name: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LobbyLookup(Base):
    """One overlay lookup (``POST /lobby/risk``), for the admin page's usage numbers. No Steam IDs are kept."""

    __tablename__ = "lobby_lookups"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    players: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)


class AnalysisJob(Base):
    """One demo waiting for, or going through, analysis: the queue the analysis workers share.

    Uploads, path imports and fetched matches all land here; any worker (``cs2-analyzer worker`` or a
    thread inside the API) claims the oldest queued job with ``SELECT ... FOR UPDATE SKIP LOCKED``, so
    two workers never take the same job. A running job's ``heartbeat_at`` is refreshed while it works;
    a job whose heartbeat stops (worker killed, server restarted) is claimed again by another worker.
    """

    __tablename__ = "analysis_jobs"
    __table_args__ = (Index("ix_analysis_jobs_queue", "status", "created_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))                 # upload | import | fetch
    # QUEUED -> PROCESSING -> COMPLETED | DUPLICATE | FAILED
    status: Mapped[str] = mapped_column(String(16), default="QUEUED")
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    path: Mapped[str] = mapped_column(Text)                       # the .dem (for fetch: where the download goes)
    file_name: Mapped[str] = mapped_column(String(255))
    share_code: Mapped[str | None] = mapped_column(String(40), index=True)
    demo_url: Mapped[str | None] = mapped_column(Text)
    force: Mapped[bool] = mapped_column(Boolean, default=False)
    keep_demo: Mapped[bool] = mapped_column(Boolean, default=False)
    generate_evidence: Mapped[bool] = mapped_column(Boolean, default=False)
    requested_match_id: Mapped[str | None] = mapped_column(String(64))
    played_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    match_id: Mapped[str | None] = mapped_column(String(64))
    demo_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    error: Mapped[str | None] = mapped_column(SafeText())
    trace: Mapped[str | None] = mapped_column(SafeText())
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    worker: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    run_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))   # not before (match busy elsewhere)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class AnalysisLock(Base):
    """A match being analyzed right now, so two workers never analyze the same match at once.

    Taken once the demo is parsed, under both the match id and the demo's SHA-256 (the primary key makes
    the second taker fail); released when the job ends, or when its job is taken over after a lost heartbeat.
    """

    __tablename__ = "analysis_locks"
    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("analysis_jobs.id", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AnalysisWorkerSeen(Base):
    """An analysis worker that is running (admin page: how many workers there are, and what they do)."""

    __tablename__ = "analysis_workers"
    name: Mapped[str] = mapped_column(String(128), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    job_id: Mapped[str | None] = mapped_column(String(32))
