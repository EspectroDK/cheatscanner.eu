"""Command line interface: ``cs2-analyzer <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from cs2_analyzer.config import Config, ConfigError, load_dotenv

DETECTOR_LABELS = {
    "HIDDEN_INFORMATION": "Hidden information",
    "AIM_MECHANICS": "Aim anomalies",
    "SHOT_TIMING": "Trigger timing",
    "RECOIL": "Recoil anomalies",
    "IMPOSSIBLE_MECHANICS": "Impossible mechanics",
    "DECISION_INFORMATION": "Strategic information",
}

MIRAGE_TRI_URL = "https://raw.githubusercontent.com/AtomicBool/cs2-map-parser/main/vischeck_example/mirage.tri"


def _db(cfg: Config, args):
    if getattr(args, "no_db", False):
        return None
    from cs2_analyzer.storage.repository import Database

    db = Database(args.db_url or cfg.get("storage.database_url"))
    db.init_schema()
    return db


def _load_cfg(args) -> Config:
    overrides = {}
    if getattr(args, "output", None):
        overrides.setdefault("output", {})["dir"] = args.output
    if getattr(args, "maps_dir", None):
        overrides.setdefault("geometry", {})["maps_dir"] = args.maps_dir
    if getattr(args, "observations", None):
        overrides.setdefault("output", {})["observations_dir"] = args.observations
    if getattr(args, "evidence_min_class", None):
        labels = ["NORMAL", "ELEVATED", "HIGH", "VERY_HIGH"]
        overrides.setdefault("evidence", {})["generate_for_classes"] = labels[labels.index(args.evidence_min_class):]
    return Config.load(getattr(args, "config", None), overrides)


def cmd_analyze(args) -> int:
    from cs2_analyzer.pipeline import analyze_demo
    from cs2_analyzer.storage.repository import AlreadyProcessedError

    cfg = _load_cfg(args)
    db = _db(cfg, args)
    players = {int(p) for p in args.player} if args.player else None
    detectors = [d.strip() for d in args.detectors.split(",")] if args.detectors else None
    rc = 0
    for dem in args.demos:
        try:
            res = analyze_demo(
                dem, cfg, db=db, keep_demo=args.keep_demo, export_parquet=args.export_parquet,
                generate_evidence=args.generate_evidence, debug=args.debug, player_filter=players,
                detector_names=detectors, force=args.force, match_id=args.match_id,
                progress=lambda m: print(m, file=sys.stderr),
            )
        except AlreadyProcessedError as exc:
            print(str(exc), file=sys.stderr)
            rc = 3
            continue
        if args.json:
            print((res.output_dir / "match.json").read_text(encoding="utf-8"))
        else:
            print_result(res, verbose=args.debug)
    return rc


def print_result(res, verbose: bool = False):
    w = res.world
    m = res.meta
    print(f"\nMatch: {m.match_id}  ({m.source})")
    print(f"Map: {m.map_name}   Mode: {m.mode or 'unknown'} ({m.mode_source})   Tickrate: {m.tickrate:.0f}")
    print(f"Rounds: {len(res.rounds)} ({int(res.rounds['live'].sum())} live)   Encounters: {len(res.encounters)}")
    print(f"Players analyzed: {len(res.assessments)}")
    for wmsg in res.warnings:
        print(f"WARNING: {wmsg}")
    if verbose:
        print("\nRounds:")
        for r in res.rounds.itertuples():
            print(f"  #{r.round_number:<3} ticks {r.start_tick}-{r.end_tick}  winner={r.winner} reason={r.reason} live={r.live}")
        from cs2_analyzer.pipeline import _vis_summary

        print("\nVisibility / knowledge (pair-ticks):", json.dumps(_vis_summary(res)))
        print("Timings (s):", json.dumps(res.timings))
    stats = res.match_stats.set_index("steam_id")
    order = sorted(res.assessments.values(), key=lambda a: -a.overall)
    for a in order:
        p = w.index_of[a.steam_id]
        st = stats.loc[a.steam_id] if a.steam_id in stats.index else None
        print(f"\n{a.steam_id}  {w.names[p]}")
        if st is not None:
            print(f"  K/D/A {st.kills}/{st.deaths}/{st.assists}  (context only, not evidence)")
        print(f"  Evidence score: {a.classification}  ({a.overall:.2f})")
        pc = (a.population or {}).get("combined_percentile")
        if pc is not None:
            print(f"  Vs clean players:       percentile {pc * 100:.0f}{'  (above flag)' if a.population['above_flag'] else ''}"
                  f"  (reported only)")
        pr = a.profile
        if pr is not None:
            top = ", ".join(f"{k.split('.')[-1]} {v['contribution']:+.1f}" for k, v in list(pr["features"].items())[:3])
            print(f"  Player evidence:        {pr['score']:+.1f}, above {pr['clean_percentile'] * 100:.1f}% of clean players"
                  f"  ({top})")
        for axis, label in DETECTOR_LABELS.items():
            print(f"  {label + ':':<24}{a.axis_scores.get(axis, 0.0):.2f}")
        evs = sorted([e for e in res.events if e.steam_id == a.steam_id], key=lambda e: -e.confidence)
        if evs:
            print("  Evidence events:")
            for i, e in enumerate(evs[:5], 1):
                print(f"   #{i} {e.detector_type:<22} Round {e.round_number:<3} sev {e.severity:.2f} "
                      f"rel {e.reliability:.2f} weight {e.confidence:.2f}")
        h = res.history.get(a.steam_id)
        if h:
            print(f"  History: {h['classification']} ({h['historical_evidence_score']:.2f}) over "
                  f"{h['matches_analyzed']} match(es), confidence {h['confidence_level']}")
        if verbose and a.notes:
            for n in a.notes:
                print(f"   note: {n}")
    print(f"\nEvidence written to:\n{res.output_dir}/")
    print(f"Raw demo {'deleted' if res.demo_deleted else 'kept'}.")
    print("Scores are behavioral evidence for review, not verdicts.")


def cmd_db_init(args) -> int:
    cfg = _load_cfg(args)
    db = _db(cfg, args)
    print(f"schema ready at {db.safe_url()}")
    return 0


def cmd_player(args) -> int:
    cfg = _load_cfg(args)
    db = _db(cfg, args)
    sid = int(args.steam_id)
    out = {"player": db.get_player(sid), "risk": db.risk(sid), "matches": db.player_matches(sid)}
    if args.evidence:
        out["evidence"] = db.player_evidence(sid)
    print(json.dumps(out, indent=2, default=str))
    return 0 if out["player"] else 1


def cmd_serve(args) -> int:
    import uvicorn

    from cs2_analyzer.api.app import create_app

    cfg = _load_cfg(args)
    # Progress of each analysis (queued, parsing, done, warnings) goes to the server log: `docker compose logs -f api`.
    logging.getLogger("cs2_analyzer").setLevel(logging.INFO)
    uvicorn.run(create_app(cfg, db_url=args.db_url), host=args.host, port=args.port)
    return 0


def cmd_worker(args) -> int:
    """Analysis worker: analyze demos from the queue in the database until stopped (worker.py)."""
    from cs2_analyzer.worker import AnalysisWorker

    cfg = _load_cfg(args)
    logging.getLogger("cs2_analyzer").setLevel(logging.INFO)  # `docker compose logs -f worker`
    AnalysisWorker(cfg, _db(cfg, args)).run_process()
    return 0


def cmd_queue(args) -> int:
    """Analysis queue right now, as JSON (or one number with --field, for deploy/server-deploy.sh)."""
    from datetime import timedelta

    cfg = _load_cfg(args)
    q = _db(cfg, args).analysis_queue(timedelta(seconds=float(cfg.get("worker.stale_after_s", 120))))
    print(q[args.field] if args.field else json.dumps(q))
    return 0


def cmd_maps_fetch(args) -> int:
    import urllib.request

    cfg = _load_cfg(args)
    maps_dir = Path(cfg.get("geometry.maps_dir"))
    maps_dir.mkdir(parents=True, exist_ok=True)
    if args.map != "de_mirage" and not args.url:
        print("Only de_mirage has a known public .tri source. For other maps build one from a local CS2 "
              "install with tools/geometry/build_tris.py (see docs/geometry.md) or pass --url.", file=sys.stderr)
        return 2
    url = args.url or MIRAGE_TRI_URL
    dst = maps_dir / f"{args.map}.tri"
    print(f"downloading {url} -> {dst}")
    urllib.request.urlretrieve(url, dst)
    from cs2_analyzer.geometry.mesh import load_tri

    print(f"ok: {len(load_tri(dst))} triangles")
    return 0


def cmd_map_images(args) -> int:
    """Download the map screenshots the website shows (deploy/server-deploy.sh runs this on the server)."""
    from cs2_analyzer.map_images import fetch, images_dir

    cfg = _load_cfg(args)
    failed = fetch(images_dir(cfg.get("geometry.maps_dir")), force=args.force)
    return 1 if failed else 0


def cmd_maps_check(args) -> int:
    """Check .tri meshes before they are installed (tools/deploy/push-maps.ps1 runs this on the server)."""
    from cs2_analyzer.geometry.status import check_mesh, mesh_patch

    cfg = _load_cfg(args)
    paths = [Path(p) for p in args.paths] or [Path(cfg.get("geometry.maps_dir"))]
    files = sorted({f for p in paths for f in (p.rglob("*.tri") if p.is_dir() else [p])})
    if not files:
        print("no .tri files found", file=sys.stderr)
        return 1
    bad = 0
    for f in files:
        n, problem = check_mesh(f)
        patch = mesh_patch(f)
        print(f"{'BAD' if problem else 'ok '}  {f}  {n} triangles"
              + (f", patch {patch}" if patch else "") + (f": {problem}" if problem else ""))
        bad += problem is not None
    return 1 if bad else 0


def cmd_cs2cd(args) -> int:
    """CS2CD dataset helpers: download matches, list labelled cheaters."""
    from cs2_analyzer.parser import cs2cd_backend as cs2cd

    if args.action == "list":
        rows = [r for r in cs2cd.load_index() if _cs2cd_match(r, args)]
        for r in rows:
            print(f"{r['split']}/{r['match']}  {r['map']:<12} {r['match_making_type']:<22} "
                  f"cheaters={r['cheaters'] or '-'}  {int(r['parquet_bytes']) / 1e6:.0f} MB")
        print(f"{len(rows)} matches, {sum(int(r['parquet_bytes']) for r in rows) / 1e9:.1f} GB", file=sys.stderr)
        return 0
    if args.action == "fetch":
        import urllib.request

        rows = [r for r in cs2cd.load_index() if _cs2cd_match(r, args)][: args.limit or None]
        dest = Path(args.dest)
        for i, r in enumerate(rows, 1):
            for ext in (".json", ".parquet"):
                out = dest / r["split"] / f"{r['match']}{ext}"
                if out.exists() and out.stat().st_size:
                    continue
                out.parent.mkdir(parents=True, exist_ok=True)
                url = f"{cs2cd.HF_RESOLVE}/{r['split']}/{r['match']}{ext}"
                print(f"[{i}/{len(rows)}] {url}", file=sys.stderr)
                tmp = out.with_suffix(ext + ".part")
                urllib.request.urlretrieve(url, tmp)
                tmp.rename(out)
        print(f"{len(rows)} matches in {dest}")
        return 0
    # labels: synthetic ids of labelled cheaters, one per line (for calibrate --exclude)
    ids = []
    for j in sorted(Path(args.dest).glob("*/*.json")):
        ids.extend(cs2cd.match_labels(j)["cheater_steam_ids"])
    print("\n".join(str(i) for i in ids))
    return 0


def _cs2cd_match(row: dict, args) -> bool:
    return (not args.map or row["map"] == args.map) and (not args.split or row["split"] == args.split)


def cmd_inspect_visibility(args) -> int:
    """Visual validation of one (observer, target, tick) visibility decision."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    from cs2_analyzer.evidence.render import project, render_pov, render_topdown
    from cs2_analyzer.geometry.angles import bearing
    from cs2_analyzer.parser import get_parser
    from cs2_analyzer.pipeline import build_analysis

    cfg = _load_cfg(args)
    demo = get_parser().parse(args.demo)
    world, geometry, smoke, vis, knowledge, *_ = build_analysis(demo, cfg, detector_names=["flash"])
    o, e, t = world.index_of[int(args.observer)], world.index_of[int(args.target)], world.t(args.tick)
    res = vis.check(args.observer, args.target, args.tick)
    res["derived_state"] = knowledge.derived_state(o, e, t)
    res["game_spotted_by_observer"] = bool(world.spotted_by[e, o, t])
    print(json.dumps(res, indent=2, default=str))
    eye, chest = world.eye[o, t], world.body_points(e, np.array([t]))["chest"][0]
    fig, (a1, a2, a3) = plt.subplots(1, 3, figsize=(24, 6))
    img = render_pov(geometry, eye, float(world.pitch[o, t]), float(world.yaw[o, t]), 640, 360, smoke=smoke, t=t)
    a1.imshow(img)
    a1.set_title("observer's actual view")
    by, bp = bearing(eye, chest)
    img2 = render_pov(geometry, eye, float(bp), float(by), 640, 360, smoke=smoke, t=t)
    a2.imshow(img2)
    bpts = world.body_points(e, np.array([t]))
    sx, sy, _ = project(np.array([bpts[k][0] for k in ("head", "chest", "pelvis", "knee")]), eye, float(bp), float(by), 640, 360)
    a2.plot(sx, sy, "m-o")
    a2.set_title(f"looking straight at target: {res['state']} / {res.get('knowledge')}")
    td, ext = render_topdown(geometry, ceiling_z=float(max(eye[2], chest[2])) + 150)
    a3.imshow(td, extent=ext)
    a3.plot([eye[0], chest[0]], [eye[1], chest[1]], "y-")
    a3.plot(eye[0], eye[1], "co")
    a3.plot(chest[0], chest[1], "mo")
    c = (eye[:2] + chest[:2]) / 2
    r = max(500, float(np.linalg.norm(eye[:2] - chest[:2])) / 2 + 300)
    a3.set_xlim(c[0] - r, c[0] + r)
    a3.set_ylim(c[1] - r, c[1] + r)
    out = Path(args.out or f"visibility_{args.observer}_{args.target}_{args.tick}.png")
    fig.savefig(out, dpi=70, bbox_inches="tight")
    print(f"wrote {out}")
    return 0


def cmd_calibrate(args) -> int:
    from cs2_analyzer import calibration as cal

    cfg = _load_cfg(args)
    obs_dir = args.obs_dir or cfg.get("output.observations_dir")
    exclude = {int(x) for x in Path(args.exclude).read_text().split()} if args.exclude else None
    if args.action == "report":
        path = cal.distribution_report(obs_dir, args.out, exclude_players=exclude)
        print(f"wrote {path}")
    elif args.action == "population-reference":
        from cs2_analyzer.scoring.population import build_reference

        ref = build_reference(obs_dir, min_obs=args.min_obs_player, exclude_players=exclude, source=args.source or "")
        out = Path(args.json_out or Path(args.out) / "population_reference.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(ref))
        print(f"wrote {out}: " + ", ".join(f"{k} n={v['n_players']}" for k, v in ref["metrics"].items()))
    elif args.action == "player-evidence-model":
        from cs2_analyzer.scoring import player_evidence as pe

        if not (args.cheater_obs_dir and args.cheater_ids):
            print("player-evidence-model needs --cheater-obs-dir and --cheater-ids (e.g. from `cs2cd labels`)")
            return 2
        cheater_ids = {int(x) for x in Path(args.cheater_ids).read_text().split()}

        def table(d, keep=None, drop=None):
            t = pe.observation_table({det: cal.load_observations(d, det) for det, *_ in pe.FEATURES + pe.PLAYER_FEATURES})
            if keep is not None:
                t = t[t["steam_id"].isin(keep)]
            return t[~t["steam_id"].isin(drop)] if drop else t

        model = pe.fit_model(table(obs_dir, drop=exclude), table(args.cheater_obs_dir, keep=cheater_ids),
                             source=args.source or "")
        out = Path(args.json_out or Path(args.out) / "player_evidence_model.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(model))
        print(f"wrote {out}: {len(model['features'])} features, {model['clean_players']} clean reference players, "
              f"{model['cheater_players']} labelled cheaters")
    else:
        rows = cal.build_baselines(obs_dir, exclude_players=exclude, min_n=args.min_n)
        db = None if args.no_db else _db(cfg, args)
        cal.save_baselines(rows, db=db, json_path=args.json_out)
        print(f"built {len(rows)} baseline rows from {obs_dir}"
              + (f" (json: {args.json_out})" if args.json_out else "") + ("" if db is None else " (database)"))
    return 0


def cmd_ingest(args) -> int:
    """Ask Steam for every onboarded user's new matches and queue them for the demo fetcher."""
    import time

    from cs2_analyzer.ingest.service import Ingest

    cfg = _load_cfg(args)
    ingest = Ingest(cfg, _db(cfg, args))
    interval = args.loop if args.loop is not None else None
    while True:
        s = ingest.poll_once()
        print(f"{time.strftime('%H:%M:%S')} users {s['users']}, new matches queued {s['queued']}, errors {s['errors']}",
              flush=True)
        if interval is None:
            return 0
        time.sleep(interval or float(cfg.get("ingest.poll_interval_s", 600)))


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cs2-analyzer", description="Offline CS2 demo behavioral evidence analyzer.")
    ap.add_argument("--config", help="TOML config overriding defaults")
    ap.add_argument("--db-url", help="database URL (default from config / CS2A_DATABASE_URL)")
    ap.add_argument("--maps-dir", help="directory with <map>.tri collision meshes")
    ap.add_argument("-v", "--verbose", action="store_true")
    sub = ap.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("analyze", help="analyze one or more .dem files")
    a.add_argument("demos", nargs="+")
    a.add_argument("--keep-demo", action="store_true", default=None, help="do not delete the demo after success")
    a.add_argument("--export-parquet", action="store_true", help="write normalized ticks and encounter channels as Parquet")
    a.add_argument("--generate-evidence", action="store_true", help="render plots + MP4 clips for flagged players")
    a.add_argument("--evidence-min-class", choices=["NORMAL", "ELEVATED", "HIGH", "VERY_HIGH"],
                   help="generate evidence for players at or above this class (default HIGH)")
    a.add_argument("--debug", action="store_true", help="verbose output + debug plots for every player's top events")
    a.add_argument("--player", action="append", help="only analyze this steamid (repeatable)")
    a.add_argument("--detectors", help="comma-separated detector names")
    a.add_argument("--force", action="store_true", help="reprocess a match already in the database")
    a.add_argument("--match-id", help="explicit match id (default: from Valve filename or content hash)")
    a.add_argument("--output", help="output directory")
    a.add_argument("--observations", help="directory for raw calibration observations")
    a.add_argument("--no-db", action="store_true", help="do not persist to the database")
    a.add_argument("--json", action="store_true", help="print match.json instead of the text summary")
    a.set_defaults(func=cmd_analyze)

    d = sub.add_parser("db-init", help="create database tables")
    d.set_defaults(func=cmd_db_init)

    p = sub.add_parser("player", help="show stored history for a player")
    p.add_argument("steam_id")
    p.add_argument("--evidence", action="store_true")
    p.set_defaults(func=cmd_player)

    s = sub.add_parser("serve", help="run the REST API")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.set_defaults(func=cmd_serve)

    w = sub.add_parser("worker", help="analysis worker: analyze queued demos (uploads, fetched matches) until stopped")
    w.set_defaults(func=cmd_worker)

    q = sub.add_parser("queue", help="show the analysis queue (JSON)")
    q.add_argument("--field", choices=["queued", "processing", "uploads", "oldestWaitingSeconds", "workers"],
                   help="print only this number")
    q.set_defaults(func=cmd_queue)

    m = sub.add_parser("maps-fetch", help="download a map collision mesh (.tri)")
    m.add_argument("map")
    m.add_argument("--url")
    m.set_defaults(func=cmd_maps_fetch)

    mi = sub.add_parser("map-images", help="download the map screenshots for the website (skips ones already there)")
    mi.add_argument("--force", action="store_true", help="download all again")
    mi.set_defaults(func=cmd_map_images)

    mc = sub.add_parser("maps-check", help="check .tri map meshes (files or folders; default: the maps folder)")
    mc.add_argument("paths", nargs="*")
    mc.set_defaults(func=cmd_maps_check)

    c = sub.add_parser("calibrate", help="calibration: distribution report or population baselines")
    c.add_argument("action", choices=["report", "build-baselines", "population-reference",
                                          "player-evidence-model"])
    c.add_argument("--obs-dir", help="observations directory (default from config)")
    c.add_argument("--out", default="output/calibration", help="report output directory")
    c.add_argument("--exclude", help="file with SteamIDs to exclude (e.g. banned / suspected players)")
    c.add_argument("--min-n", type=int, default=30, help="minimum samples per baseline stratum")
    c.add_argument("--json-out", help="also write baselines as JSON")
    c.add_argument("--no-db", action="store_true")
    c.add_argument("--min-obs-player", type=int, default=5, help="population-reference: observations per player median")
    c.add_argument("--source", help="population-reference / player-evidence-model: description stored in the file")
    c.add_argument("--cheater-obs-dir", help="player-evidence-model: observations of matches with labelled cheaters")
    c.add_argument("--cheater-ids", help="player-evidence-model: file with the labelled cheaters' SteamIDs")
    c.set_defaults(func=cmd_calibrate)

    cd = sub.add_parser("cs2cd", help="CS2CD Hugging Face dataset: list, fetch, labels")
    cd.add_argument("action", choices=["list", "fetch", "labels"])
    cd.add_argument("--map", help="only this map, e.g. de_mirage")
    cd.add_argument("--split", choices=["no_cheater_present", "with_cheater_present"])
    cd.add_argument("--limit", type=int, help="fetch at most this many matches")
    cd.add_argument("--dest", default="data/cs2cd", help="dataset directory (fetch target / labels source)")
    cd.set_defaults(func=cmd_cs2cd)

    ig = sub.add_parser("ingest", help="automatic demo fetching: poll Steam for users' new matches")
    ig.add_argument("action", choices=["poll"])
    ig.add_argument("--loop", type=float, nargs="?", const=0,
                    help="keep polling; optional seconds between polls (default ingest.poll_interval_s)")
    ig.set_defaults(func=cmd_ingest)

    iv = sub.add_parser("inspect-visibility", help="render and explain one visibility decision")
    iv.add_argument("demo")
    iv.add_argument("--observer", required=True)
    iv.add_argument("--target", required=True)
    iv.add_argument("--tick", type=int, required=True)
    iv.add_argument("--out")
    iv.set_defaults(func=cmd_inspect_visibility)
    return ap


def main(argv=None) -> int:
    load_dotenv()  # settings from ./.env, as Docker Compose would pass them
    args = build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s")
    try:
        return int(args.func(args) or 0)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
