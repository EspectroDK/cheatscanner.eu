"""Calibrate the ``mouse_view`` detector on CS2CD matches.

Downloads every dataset match from Hugging Face (one at a time per worker, deleted after use),
runs ``mouse_view``, ``view_integrity`` and ``input_lattice`` (no map mesh needed) and writes one
row per player: the ``mouse_view`` summary, whether each detector fired, and the group (clean,
unlabelled, cheater, excluded). Then prints how often the rule fires per group for a grid of
``min_absolute_share`` values and how many cheaters it adds to the other two detectors.

    python tools/calibration/mouse_view_calibrate.py \\
        --exclude docs/validation/cs2cd/anti_aim_clean_players.txt --work output/mouse_view --jobs 4

Matches and cheater labels come from the dataset index; ``--matches`` (csv with split, match columns)
limits the run.

Results per match are cached in ``--work``; re-running only adds new matches.
"""

from __future__ import annotations

import argparse
import csv
import json
import time
import traceback
import urllib.request
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd

DETECTORS = ["mouse_view", "view_integrity", "input_lattice"]


def _fetch(url: str, out: Path) -> None:
    for delay in (5, 30, 120, None):
        try:
            urllib.request.urlretrieve(url, out)
            return
        except OSError:  # includes truncated downloads (ContentTooShortError)
            if delay is None:
                raise
            time.sleep(delay)


def run_match(args: tuple[str, str, str]) -> tuple[str, str]:
    from cs2_analyzer.config import Config
    from cs2_analyzer.geometry.mesh import MapGeometry
    from cs2_analyzer.parser import get_parser
    from cs2_analyzer.parser.cs2cd_backend import HF_RESOLVE
    from cs2_analyzer.pipeline import build_analysis

    split, match, work = args[0], args[1], Path(args[2])
    tag = f"{split}_{match}"
    if (work / f"{tag}.json").exists():
        return tag, "cached"
    d = work / "tmp" / split
    d.mkdir(parents=True, exist_ok=True)
    pq, js = d / f"{match}.parquet", d / f"{match}.json"
    try:
        for ext, f in (("json", js), ("parquet", pq)):
            _fetch(f"{HF_RESOLVE}/{split}/{match}.{ext}", f)
        demo = get_parser("cs2cd").parse(pq)
        empty = MapGeometry(demo.meta.map_name, np.zeros((0, 3, 3), np.float32), source="none", backend="numpy")
        world, *_, ctx, events = build_analysis(demo, Config.load(), detector_names=DETECTORS, geometry=empty)
        summary = ctx.observations.frame("mouse_view_summary")
        rows = summary.to_dict("records") if len(summary) else []
        fired = sorted({(e.detector_type, int(e.steam_id)) for e in events})
        (work / f"{tag}.json").write_text(json.dumps({"map": demo.meta.map_name, "ids": [int(x) for x in world.steam_ids],
                                                      "summary": rows, "fired": fired}, default=float))
        return tag, "ok"
    except Exception:  # noqa: BLE001 - report and continue with the other matches
        return tag, "error: " + traceback.format_exc(limit=2)
    finally:
        pq.unlink(missing_ok=True)
        js.unlink(missing_ok=True)


def player_table(work: Path, cheaters: set[int], exclude: set[int]) -> pd.DataFrame:
    rows = []
    for meta_path in sorted(work.glob("*.json")):
        tag = meta_path.stem
        meta = json.loads(meta_path.read_text())
        summ = {int(r["steam_id"]): r for r in meta["summary"]}
        fired = {(d, int(s)) for d, s in meta["fired"]}
        for sid in meta["ids"]:
            if tag.startswith("no_cheater_present"):
                group = "excluded" if sid in exclude else "clean"
            else:
                group = "cheater" if sid in cheaters else "unlabelled"
            r = summ.get(sid, {})
            rows.append({"match": tag, "map": meta["map"], "steam_id": sid, "group": group,
                         **{k: r.get(k, np.nan) for k in ("absolute_share", "absolute_ticks", "agreement", "gain_yaw",
                                                          "unexplained", "no_mouse_share", "mouse_ticks", "moved_ticks",
                                                          "mouse_share")},
                         **{d: (d, sid) in fired for d in DETECTORS}})
    return pd.DataFrame(rows)


def report(df: pd.DataFrame, min_ticks: int = 2000, min_mouse_share: float = 0.9) -> str:
    groups = ["clean", "unlabelled", "cheater"]
    elig = (df.mouse_ticks >= min_ticks) & (df.mouse_share >= min_mouse_share)
    lines = [f"Eligible players (>= {min_ticks} ticks with mouse data): "
             + ", ".join(f"{g} {int((elig & (df.group == g)).sum())}" for g in groups), "",
             "| min absolute share | " + " | ".join(groups) + " | cheaters not caught by view_integrity or input_lattice |",
             "|---|---|---|---|---|"]
    other = df.view_integrity | df.input_lattice
    for a in (0.001, 0.01, 0.05, 0.1, 0.2, 0.5, 0.8):
        fire = elig & (df.absolute_share >= a)
        cells = [f"{int(fire[df.group == g].sum())}" for g in groups]
        lines.append(f"| {a:g} | " + " | ".join(cells) + f" | {int((fire & (df.group == 'cheater') & ~other).sum())} |")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matches", help="csv with split, match columns (default: every dataset match)")
    ap.add_argument("--exclude", help="clean-split ids to leave out (anti-aim players)")
    ap.add_argument("--work", default="output/mouse_view")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", help="write the per-player table here (csv)")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    from cs2_analyzer.parser import cs2cd_backend as cs2cd

    index = cs2cd.load_index()
    cheaters = {cs2cd.cs2cd_steam_id(r["split"], int(r["match"]), p.strip())
                for r in index for p in (r["cheaters"] or "").replace(";", ",").split(",") if p.strip()}
    if a.matches:
        with open(a.matches) as f:
            wanted = {(r["split"], str(r["match"])) for r in csv.DictReader(f)}
        index = [r for r in index if (r["split"], str(r["match"])) in wanted]
    rows = [(r["split"], str(r["match"]), str(work)) for r in index]
    with Pool(a.jobs) as pool:
        for i, (tag, status) in enumerate(pool.imap_unordered(run_match, rows)):
            if status not in ("ok", "cached") or i % 50 == 0:
                print(i, tag, status, flush=True)
    exclude = {int(x) for x in Path(a.exclude).read_text().split() if x.strip().isdigit()} if a.exclude else set()
    df = player_table(work, cheaters, exclude)
    if a.out:
        df.to_csv(a.out, index=False)
    print(report(df))


if __name__ == "__main__":
    main()
