"""Calibrate the ``information_gap`` detector on CS2CD matches.

Runs visibility, the knowledge model and ``information_gap`` (only) on every CS2CD match in
``--dataset`` whose map has a mesh, sums each player's tracked and opportunity degrees and
on-target first shots, and reports how often the match-level rule fires for clean players,
unlabelled players in cheater matches and labelled cheaters, for a grid of thresholds.

    cs2-analyzer cs2cd fetch --map de_nuke --dest data/cs2cd
    cs2-analyzer cs2cd labels --dest data/cs2cd > cheaters.txt
    python tools/calibration/information_gap_calibrate.py --dataset data/cs2cd --cheaters cheaters.txt \\
        --exclude docs/validation/cs2cd/anti_aim_clean_players.txt --work output/information_gap --jobs 4

Per-match observations are cached in ``--work``; re-running only adds new matches.
"""

from __future__ import annotations

import argparse
import itertools
import json
import traceback
from multiprocessing import Pool
from pathlib import Path

import numpy as np
import pandas as pd


def run_match(args: tuple[str, str]) -> tuple[str, str]:
    from cs2_analyzer.config import Config
    from cs2_analyzer.parser import get_parser
    from cs2_analyzer.pipeline import build_analysis

    path, work = Path(args[0]), Path(args[1])
    tag = f"{path.parent.name}_{path.stem}"
    if (work / f"{tag}.json").exists():
        return tag, "cached"
    try:
        demo = get_parser("cs2cd").parse(path)
        world, *_, ctx, _ = build_analysis(demo, Config.load(), detector_names=["information_gap"])
        ctx.observations.frame("information_gap").to_parquet(work / f"{tag}.runs.parquet")
        ctx.observations.frame("information_gap_shots").to_parquet(work / f"{tag}.shots.parquet")
        (work / f"{tag}.json").write_text(json.dumps({"map": demo.meta.map_name,
                                                      "ids": [int(x) for x in world.steam_ids]}))
        return tag, "ok"
    except Exception:  # noqa: BLE001 - report and continue with the other matches
        return tag, "error: " + traceback.format_exc(limit=2)


def player_table(work: Path, cheaters: set[int], exclude: set[int]) -> pd.DataFrame:
    rows = []
    for meta_path in sorted(work.glob("*.json")):
        tag = meta_path.stem
        meta = json.loads(meta_path.read_text())
        runs = pd.read_parquet(work / f"{tag}.runs.parquet")
        shots = pd.read_parquet(work / f"{tag}.shots.parquet")
        clean_split = tag.startswith("no_cheater_present")
        for sid in meta["ids"]:
            r = runs[runs.steam_id == sid] if len(runs) else runs
            s = shots[shots.steam_id == sid] if len(shots) else shots
            if clean_split:
                group = "excluded" if sid in exclude else "clean"
            else:
                group = "cheater" if sid in cheaters else "unlabelled"
            rows.append({"match": tag, "map": meta["map"], "steam_id": sid, "group": group,
                         "opportunity": float(r["opportunity_deg"].sum()) if len(r) else 0.0,
                         "tracked": float(r["tracked_deg"].sum()) if len(r) else 0.0,
                         "shots": int(s["beyond"].sum()) if len(s) else 0,
                         "shots_on": int(s["on_target"].sum()) if len(s) else 0})
    df = pd.DataFrame(rows)
    df["share"] = df["tracked"] / df["opportunity"].replace(0, np.nan)
    return df


def report(df: pd.DataFrame) -> str:
    groups = ["clean", "unlabelled", "cheater"]
    lines = ["| min tracked deg | min share | " + " | ".join(groups) + " |", "|---|---|---|---|---|"]
    for a, s in itertools.product([200, 250, 300, 350, 400], [0.02, 0.03, 0.04, 0.05]):
        fire = (df["tracked"] >= a) & (df["share"] >= s)
        cells = [f"{int(fire[df.group == g].sum())} / {int((df.group == g).sum())}" for g in groups]
        lines.append(f"| {a} | {s:.2f} | " + " | ".join(cells) + " |")
    q = [0.5, 0.9, 0.99, 0.997]
    lines += ["", "| quantile | " + " | ".join(f"{g} tracked / share" for g in groups) + " |", "|---|---|---|---|"]
    for x in q:
        cells = [f"{df[df.group == g]['tracked'].quantile(x):.0f} / {df[df.group == g]['share'].quantile(x):.3f}"
                 for g in groups]
        lines.append(f"| {x} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="data/cs2cd")
    ap.add_argument("--cheaters", required=True, help="file with labelled cheater steam ids (cs2cd labels)")
    ap.add_argument("--exclude", help="clean-split players to leave out (e.g. anti-aim rage cheaters)")
    ap.add_argument("--work", default="output/information_gap")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--out", help="write the markdown report here")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    files = sorted(Path(a.dataset).glob("*/*.parquet"))
    with Pool(a.jobs) as pool:
        for tag, status in pool.imap_unordered(run_match, [(str(f), str(work)) for f in files]):
            print(tag, status, flush=True)
    ids = lambda p: {int(x) for x in Path(p).read_text().split() if x.strip().isdigit()} if p else set()  # noqa: E731
    df = player_table(work, ids(a.cheaters), ids(a.exclude))
    df.to_parquet(work / "players.parquet")
    text = report(df)
    print(text)
    if a.out:
        Path(a.out).write_text(text + "\n")


if __name__ == "__main__":
    main()
