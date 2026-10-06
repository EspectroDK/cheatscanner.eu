"""Calibrate the ``hidden_fire`` detector on CS2CD matches.

Runs visibility, the knowledge model, ``information_gap`` and ``hidden_fire`` on every CS2CD match in
``--dataset`` whose map has a mesh, and reports how often the ``hidden_fire`` rule fires for clean
players, unlabelled players in cheater matches and labelled cheaters, for a grid of thresholds, and how
many of the labelled cheaters it adds to ``information_gap``.

    cs2-analyzer cs2cd fetch --map de_nuke --dest data/cs2cd
    cs2-analyzer cs2cd labels --dest data/cs2cd > cheaters.txt
    python tools/calibration/hidden_fire_calibrate.py --dataset data/cs2cd --cheaters cheaters.txt \\
        --exclude docs/validation/cs2cd/anti_aim_clean_players.txt --work output/hidden_fire --jobs 4

Per-match observations are cached in ``--work``; re-running only adds new matches.
"""

from __future__ import annotations

import argparse
import itertools
import json
import traceback
from multiprocessing import Pool
from pathlib import Path

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
        world, *_, ctx, events = build_analysis(demo, Config.load(), detector_names=["information_gap", "hidden_fire"])
        ctx.observations.frame("hidden_fire").to_parquet(work / f"{tag}.hf.parquet")
        fired = [{"detector": e.detector_type, "steam_id": int(e.steam_id)} for e in events]
        (work / f"{tag}.json").write_text(json.dumps({"map": demo.meta.map_name, "ids": [int(x) for x in world.steam_ids],
                                                      "fired": fired}))
        return tag, "ok"
    except Exception:  # noqa: BLE001 - report and continue with the other matches
        return tag, "error: " + traceback.format_exc(limit=2)


def player_table(work: Path, cheaters: set[int], exclude: set[int]) -> pd.DataFrame:
    rows = []
    for meta_path in sorted(work.glob("*.json")):
        tag = meta_path.stem
        meta = json.loads(meta_path.read_text())
        obs = pd.read_parquet(work / f"{tag}.hf.parquet").set_index("steam_id")
        fired = {(f["detector"], f["steam_id"]) for f in meta["fired"]}
        for sid in meta["ids"]:
            if tag.startswith("no_cheater_present"):
                group = "excluded" if sid in exclude else "clean"
            else:
                group = "cheater" if sid in cheaters else "unlabelled"
            o = obs.loc[sid] if sid in obs.index else None
            rows.append({"match": tag, "map": meta["map"], "steam_id": sid, "group": group,
                         "excess": float(o["excess"]) if o is not None else 0.0,
                         "hidden_hits": int(o["hidden_hits"]) if o is not None else 0,
                         "all_hits": int(o["all_hits"]) if o is not None and "all_hits" in o else 0,
                         "hidden_noinfo_hits": int(o["hidden_noinfo_hits"]) if o is not None and "hidden_noinfo_hits" in o else 0,
                         "hidden_share": float(o["hidden_share"]) if o is not None and "hidden_share" in o else 0.0,
                         "hidden_fire": ("hidden_fire", sid) in fired,
                         "information_gap": ("information_gap", sid) in fired})
    return pd.DataFrame(rows)


def report(df: pd.DataFrame) -> str:
    groups = ["clean", "unlabelled", "cheater"]
    lines = ["| min excess | min hidden hits | " + " | ".join(groups) + " | cheaters not caught by information_gap |",
             "|---|---|---|---|---|---|"]
    for x, h in itertools.product([2, 3, 4], [6, 8, 10, 12]):
        fire = (df["excess"] >= x) & (df["hidden_hits"] >= h)
        cells = [f"{int(fire[df.group == g].sum())} / {int((df.group == g).sum())}" for g in groups]
        new = int((fire & (df.group == "cheater") & ~df["information_gap"]).sum())
        lines.append(f"| {x} | {h} | " + " | ".join(cells) + f" | {new} |")
    q = [0.5, 0.9, 0.99, 0.997]
    lines += ["", "| quantile | " + " | ".join(f"{g} excess / hidden hits" for g in groups) + " |", "|---|---|---|---|"]
    for v in q:
        cells = [f"{df[df.group == g]['excess'].quantile(v):.2f} / {df[df.group == g]['hidden_hits'].quantile(v):.0f}"
                 for g in groups]
        lines.append(f"| {v} | " + " | ".join(cells) + " |")
    lines += ["", "Share rule (hidden hits / all gun hits):", "",
              "| min share | min hits | min hidden hits without information | " + " | ".join(groups)
              + " | cheaters not caught by information_gap or the burst rule |", "|---|---|---|---|---|---|---|"]
    bursts = (df["excess"] >= 3) & (df["hidden_hits"] >= 10)
    for sh, mh, mn in itertools.product([0.25, 0.3, 0.35, 0.4], [20, 30, 40], [0, 2, 4]):
        fire = (df["hidden_share"] >= sh) & (df["all_hits"] >= mh) & (df["hidden_noinfo_hits"] >= mn)
        cells = [f"{int(fire[df.group == g].sum())} / {int((df.group == g).sum())}" for g in groups]
        new = int((fire & (df.group == "cheater") & ~df["information_gap"] & ~bursts).sum())
        lines.append(f"| {sh} | {mh} | {mn} | " + " | ".join(cells) + f" | {new} |")
    ch = df[df.group == "cheater"]
    lines += ["", f"Labelled cheaters with an event: information_gap {int(ch['information_gap'].sum())}, "
                  f"hidden_fire {int(ch['hidden_fire'].sum())}, either {int((ch['information_gap'] | ch['hidden_fire']).sum())} "
                  f"of {len(ch)}."]
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", default="data/cs2cd")
    ap.add_argument("--cheaters", required=True, help="file with labelled cheater steam ids (cs2cd labels)")
    ap.add_argument("--exclude", help="clean-split players to leave out (e.g. anti-aim rage cheaters)")
    ap.add_argument("--work", default="output/hidden_fire")
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
