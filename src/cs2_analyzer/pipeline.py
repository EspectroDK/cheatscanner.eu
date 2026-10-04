"""End-to-end processing pipeline.

.dem -> parse -> world -> visibility -> knowledge -> encounters -> detectors
-> evidence -> match/player aggregation -> persistence + outputs -> demo deletion
"""

from __future__ import annotations

import json
import logging
import shutil
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from cs2_analyzer import DETECTOR_VERSION, SCORING_MODEL_VERSION, __version__
from cs2_analyzer.config import Config
from cs2_analyzer.detectors import AnalysisContext, EvidenceEvent, ObservationSink, build_detectors
from cs2_analyzer.detectors.base import jsonable
from cs2_analyzer.encounters.segmentation import encounter_frame, segment_encounters
from cs2_analyzer.features.fingerprint import behavior_shift, fingerprint
from cs2_analyzer.features.match_stats import compute_match_player_stats
from cs2_analyzer.geometry.angles import view_vector
from cs2_analyzer.geometry.mesh import MapGeometry
from cs2_analyzer.geometry.smoke import SmokeModel, SmokeParams
from cs2_analyzer.geometry.visibility import VisibilityEngine, VisibilityParams
from cs2_analyzer.knowledge.model import KnowledgeModel, KnowledgeParams
from cs2_analyzer.memory import release_memory
from cs2_analyzer.parser import backend_for_path, get_parser
from cs2_analyzer.parser.base import ParsedDemo
from cs2_analyzer.scoring.aggregate import MatchAssessment, assess_match
from cs2_analyzer.world import World, build_world

log = logging.getLogger("cs2_analyzer")


@dataclass
class AnalysisResult:
    meta: object
    demo: ParsedDemo
    world: World
    geometry: MapGeometry
    smoke: SmokeModel
    vis: VisibilityEngine
    knowledge: KnowledgeModel
    encounters: list
    events: list[EvidenceEvent]
    assessments: dict[int, MatchAssessment]
    fingerprints: dict[int, dict]
    behavior_shifts: dict[int, dict]
    observations: ObservationSink
    match_stats: pd.DataFrame
    rounds: pd.DataFrame
    output_dir: Path
    timings: dict[str, float] = field(default_factory=dict)
    history: dict[int, dict] = field(default_factory=dict)
    demo_deleted: bool = False
    warnings: list[str] = field(default_factory=list)


class Timer:
    def __init__(self):
        self.t = {}

    def __call__(self, name):
        timer = self

        class _T:
            def __enter__(self_):
                self_.s = time.perf_counter()

            def __exit__(self_, *a):
                timer.t[name] = round(time.perf_counter() - self_.s, 3)

        return _T()


def build_analysis(demo: ParsedDemo, config: Config, detector_names: list[str] | None = None,
                   player_filter: set[int] | None = None, baselines=None, timer: Timer | None = None,
                   geometry: MapGeometry | None = None, release_ticks: bool = False):
    """Run everything between parsing and scoring. Separated for tests and tools.

    ``release_ticks`` empties ``demo.ticks`` once the world is built from it: nothing after
    that reads the tick table, and it is the largest object of an analysis.
    """
    timer = timer or Timer()
    with timer("world"):
        world = build_world(demo)
        if release_ticks:
            demo.ticks = demo.ticks.iloc[:0].copy()
            release_memory()
    with timer("geometry_load"):
        if geometry is None:
            geometry = MapGeometry.load(demo.meta.map_name, config.get("geometry.maps_dir"),
                                        config.get("geometry.raycast_backend", "auto"))
    smoke = SmokeModel(demo.event("smokes"), world.tick0, world.T, world.tickrate,
                       SmokeParams.from_config(config.section("geometry.smoke")), demo.event("he_grenades"))
    # gunfire through smokes opens temporary holes: mark those windows uncertain
    for p in range(world.P):
        st = world.shot_ticks.get(p, np.array([], dtype=int))
        if st.size:
            smoke.add_gunfire_holes(world.eye[p, st].astype(np.float64), view_vector(world.pitch[p, st], world.yaw[p, st]), st)
    with timer("visibility"):
        vis = VisibilityEngine(world, geometry, smoke, VisibilityParams.from_config(config.section("geometry.visibility"))).compute()
    with timer("knowledge"):
        knowledge = KnowledgeModel(world, vis, demo.events, KnowledgeParams.from_config(config.section("knowledge"))).compute()
    with timer("encounters"):
        encounters = segment_encounters(world, vis, knowledge, demo.events, config.section("encounters"))
    ctx = AnalysisContext(
        match_id=demo.meta.match_id, map_name=demo.meta.map_name, demo=demo, world=world, vis=vis, knowledge=knowledge,
        encounters=encounters, config=config, baselines=baselines, player_filter=player_filter,
    )
    events: list[EvidenceEvent] = []
    for det in build_detectors(detector_names, config.section("detectors")):
        with timer(f"detector.{det.name}"):
            events.extend(det.analyze(ctx))
    return world, geometry, smoke, vis, knowledge, encounters, ctx, events


def analyze_demo(
    path: str | Path,
    config: Config,
    *,
    db=None,
    keep_demo: bool | None = None,
    export_parquet: bool = False,
    generate_evidence: bool = False,
    debug: bool = False,
    player_filter: set[int] | None = None,
    detector_names: list[str] | None = None,
    force: bool = False,
    match_id: str | None = None,
    played_at=None,
    progress=None,
    on_parsed=None,
) -> AnalysisResult:
    path = Path(path)
    say = progress or (lambda msg: log.info(msg))
    timer = Timer()
    started = time.perf_counter()
    keep = bool(config.get("retention.keep_demos", False)) if keep_demo is None else keep_demo
    backend = backend_for_path(path, config.get("parser.backend", "demoparser2"))
    if backend == "cs2cd":
        # Dataset files are shared, already-anonymised inputs, not raw demos.
        keep = True

    if db is not None and not force:
        # cheap duplicate check before spending time on parsing
        from cs2_analyzer.parser.demoparser2_backend import _MATCH_FILE_RE, _sha256
        from cs2_analyzer.storage.repository import AlreadyProcessedError

        m = _MATCH_FILE_RE.search(path.name)
        existing = db.find_existing(match_id or (m.group(1) if m else ""), _sha256(path))
        if existing is not None and existing.processing_status == "COMPLETED":
            raise AlreadyProcessedError(existing.match_id, existing.processing_status)
    with timer("parse"):
        say(f"Parsing {path.name} ...")
        demo = get_parser(backend).parse(path, match_id=match_id)
    meta = demo.meta
    if on_parsed is not None:  # the analysis worker claims the match here (worker.py)
        on_parsed(meta)
    if db is not None:
        db.begin_match(meta, force=force, detector_version=DETECTOR_VERSION, scoring_version=SCORING_MODEL_VERSION,
                       played_at=played_at)
    try:
        baselines = None
        if db is not None:
            from cs2_analyzer.scoring.baselines import BaselineStore

            with db.session() as s:
                baselines = BaselineStore.from_db(s)
        say("Reconstructing world, visibility and knowledge ...")
        match_stats = compute_match_player_stats(demo)  # reads demo.ticks, which build_analysis may release
        world, geometry, smoke, vis, knowledge, encounters, ctx, events = build_analysis(
            demo, config, detector_names, player_filter, baselines, timer, release_ticks=not export_parquet)
        warnings = []
        if not geometry.available:
            warnings.append(f"No map geometry for {meta.map_name}: visibility is UNKNOWN everywhere, so hidden-information "
                            f"detectors cannot fire. Put {meta.map_name}.tri in {config.get('geometry.maps_dir')}.")
        elif (geometry.patch_version and str(meta.patch_version or "").isdigit()
              and abs(int(meta.patch_version) - geometry.patch_version) > int(config.get("geometry.patch_tolerance", 10))):
            warnings.append(f"Demo patch {meta.patch_version} differs from the collision mesh's patch {geometry.patch_version}. "
                            f"If {meta.map_name} changed in between, walls may be missing or extra: review hidden-information "
                            f"evidence with care, or rebuild the mesh (tools/geometry/build_tris.py).")

        rounds_alive = {p: int(sum(1 for r in demo.rounds[demo.rounds["live"]].itertuples()
                                   if 0 <= int(r.freeze_end_tick) - world.tick0 < world.T
                                   and world.alive[p, int(r.freeze_end_tick) - world.tick0]))
                        for p in range(world.P)}
        with timer("player_evidence"):
            profiles = _player_evidence(ctx, config)
        assessments: dict[int, MatchAssessment] = {}
        fingerprints: dict[int, dict] = {}
        shifts: dict[int, dict] = {}
        for p in range(world.P):
            sid = int(world.steam_ids[p])
            if player_filter and sid not in player_filter:
                continue
            pe = [e for e in events if e.steam_id == sid]
            n_enc = sum(1 for e in encounters if e.observer == p)
            assessments[sid] = assess_match(sid, meta.match_id, pe, n_enc, rounds_alive[p], config.section("scoring"),
                                            world.tickrate, profile=profiles.get(sid))
            fingerprints[sid] = fingerprint(ctx.observations, sid)
            history = db.previous_fingerprints(sid, meta.match_id) if db is not None else []
            shifts[sid] = behavior_shift(fingerprints[sid], history)
            assessments[sid].notes.append("behavior shift features: " + (
                ", ".join(shifts[sid].get("flagged", [])) or "none flagged") if shifts[sid].get("available") else
                "behavior shift: " + shifts[sid].get("reason", "unavailable"))

        with timer("population"):
            _population(assessments, ctx, config)

        out_dir = Path(config.get("output.dir")) / _safe(meta.match_id)
        result = AnalysisResult(
            meta=meta, demo=demo, world=world, geometry=geometry, smoke=smoke, vis=vis, knowledge=knowledge,
            encounters=encounters, events=events, assessments=assessments, fingerprints=fingerprints,
            behavior_shifts=shifts, observations=ctx.observations, match_stats=match_stats, rounds=demo.rounds,
            output_dir=out_dir, timings=timer.t, warnings=warnings,
        )

        with timer("outputs"):
            _write_outputs(result, config, export_parquet=export_parquet, debug=debug, generate_evidence=generate_evidence,
                           say=say)
        if db is not None:
            with timer("persist"):
                db.save_results(result)
        # Everything needed to understand the result is persisted above; only now
        # may the raw demo go.
        if not keep:
            path.unlink(missing_ok=True)
            result.demo_deleted = True
        if db is not None:
            db.mark_completed(meta.match_id, result.demo_deleted, analysis_s=time.perf_counter() - started)
            for sid in assessments:
                result.history[sid] = db.recompute_history(sid, config.section("history"))
        _write_summary(result)
        return result
    except Exception as exc:
        if db is not None:
            db.mark_failed(meta.match_id, f"{exc}\n{traceback.format_exc()}")
        if not keep and not config.get("retention.keep_failed_demos", True):
            path.unlink(missing_ok=True)
        elif not keep:
            dst = Path(config.get("retention.retained_demo_dir"))
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dst / path.name)
            path.unlink(missing_ok=True)
        raise


def _player_evidence(ctx, config: Config) -> dict[int, dict]:
    """Per-player evidence accumulated over the match (scoring/player_evidence.py)."""
    from cs2_analyzer.scoring import player_evidence as pe

    cfg = config.section("player_evidence")
    if not cfg.get("enabled", True):
        return {}
    model = pe.load_model(cfg.get("model") or None, ctx.map_name)
    if model is None:
        return {}
    frames = {det: ctx.observations.frame(det) for det, *_ in pe.FEATURES + pe.PLAYER_FEATURES}
    return pe.score_players(frames, model)


def _population(assessments: dict, ctx, config: Config) -> None:
    """Attach the clean-population comparison to each assessment (reported only)."""
    from cs2_analyzer.scoring import population as pop

    cfg = config.section("population")
    if not cfg.get("enabled", True) or not assessments:
        return
    ref = pop.load_reference(cfg.get("reference") or None, ctx.map_name)
    if ref is None:
        return
    frames = {det: ctx.observations.frame(det) for det, *_ in pop.METRICS}
    res = pop.percentiles(frames, ref, cfg.get("min_obs"), int(cfg.get("min_metrics", 2)))
    flag = float(cfg.get("flag_percentile", 0.9))
    for sid, a in assessments.items():
        r = res.get(sid, {"combined_percentile": None, "metrics_used": 0, "metrics": {}})
        r = {**r, "reference": ref.get("source"), "reference_matches": ref.get("matches"),
             "flag_percentile": flag,
             "above_flag": r["combined_percentile"] is not None and r["combined_percentile"] >= flag}
        a.population = r
        if r["combined_percentile"] is None:
            a.notes.append(f"population comparison: not enough data ({r['metrics_used']} metric(s))")
        else:
            a.notes.append(f"population comparison: typical aim/information metrics at percentile "
                           f"{r['combined_percentile'] * 100:.0f} of the clean reference "
                           f"(reported only, not part of the evidence score)")


def _safe(s: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in s)


def _write_outputs(result: AnalysisResult, config: Config, *, export_parquet: bool, debug: bool, generate_evidence: bool, say):
    from cs2_analyzer.evidence.writer import write_evidence

    out = result.output_dir
    out.mkdir(parents=True, exist_ok=True)
    # raw observations for calibration
    obs_dir = Path(config.get("output.observations_dir")) / _safe(result.meta.match_id)
    obs_dir.mkdir(parents=True, exist_ok=True)
    for det in result.observations.detectors():
        df = result.observations.frame(det)
        for c in df.columns:
            if df[c].dtype == object:
                df[c] = df[c].map(lambda v: json.dumps(jsonable(v)) if isinstance(v, (dict, list)) else v)
        df["match_id"] = result.meta.match_id
        df["map"] = result.meta.map_name
        df.to_parquet(obs_dir / f"{det}.parquet", index=False)
    if export_parquet:
        say("Exporting Parquet (normalized ticks + encounter channels) ...")
        result.demo.ticks.to_parquet(out / "ticks.parquet", index=False)
        frames = [encounter_frame(result.world, result.vis, result.knowledge, e) for e in result.encounters]
        if frames:
            pd.concat(frames, ignore_index=True).to_parquet(out / "encounter_channels.parquet", index=False)
        pd.DataFrame([e.to_dict(result.world) for e in result.encounters]).to_parquet(out / "encounters.parquet", index=False)
    write_evidence(result, config, debug=debug, generate_evidence=generate_evidence, say=say)


def _write_summary(result: AnalysisResult):
    w = result.world
    summary = {
        "match": {
            "match_id": result.meta.match_id, "match_id_source": result.meta.source, "map": result.meta.map_name,
            "mode": result.meta.mode, "mode_source": result.meta.mode_source, "server": result.meta.server_name,
            "patch_version": result.meta.patch_version, "tickrate": result.meta.tickrate,
            "demo_sha256": result.meta.demo_sha256, "parser": f"{result.meta.parser_name} {result.meta.parser_version}",
            "analyzer_version": __version__, "detector_version": DETECTOR_VERSION, "scoring_version": SCORING_MODEL_VERSION,
            "geometry_source": result.geometry.source, "demo_deleted": result.demo_deleted,
            "data_checks": result.meta.extra.get("eye_position_check"),
        },
        "rounds": jsonable(result.rounds.to_dict("records")),
        "players": jsonable(result.match_stats.to_dict("records")),
        "encounters": {"total": len(result.encounters),
                       "by_anchor": pd.Series([e.anchor_type for e in result.encounters]).value_counts().to_dict()},
        "visibility_summary": _vis_summary(result),
        "assessments": {str(k): v.to_dict() for k, v in result.assessments.items()},
        "history": {str(k): jsonable(v) for k, v in result.history.items()},
        "evidence_event_count": len(result.events),
        "timings_s": result.timings,
        "warnings": result.warnings,
        "disclaimer": "Scores are behavioral evidence/anomaly scores, not verdicts or probabilities of cheating.",
    }
    (result.output_dir / "match.json").write_text(json.dumps(jsonable(summary), indent=2, ensure_ascii=False),
                                                  encoding="utf-8")


def _vis_summary(result: AnalysisResult) -> dict:
    from cs2_analyzer.geometry.visibility import LOS
    from cs2_analyzer.knowledge.model import Knowledge

    los = result.vis.los
    kn = result.knowledge.level
    return {
        "los_pair_ticks": {s.name: int((los == s).sum()) for s in LOS if s != LOS.NOT_APPLICABLE},
        "knowledge_pair_ticks": {s.name: int((kn == s).sum()) for s in Knowledge if s != Knowledge.NOT_APPLICABLE},
        "rays_cast": int(result.vis.rays_cast),
    }
